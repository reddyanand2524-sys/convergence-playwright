"""Phase 5: the backend re-validates every descriptive event field before publishing it
(the uploaded test code can write events.jsonl, so nothing is trusted), and replays what
was persisted for finished runs."""
import json
from datetime import datetime

import pytest

from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.services import run_event_service as svc
from tests.conftest import make_run
from tests.test_screenshot_redaction import run_script

NODE = "test_login.py::test_login"


@pytest.fixture(autouse=True)
def _clean_registry():
    svc.reset_registry()
    yield
    svc.reset_registry()


def publish(tmp_path, record, secrets=()):
    """Runs one raw events.jsonl record through the tailer; returns the public event."""
    path = tmp_path / "events.jsonl"
    base = {"type": "step_started", "nodeid": NODE, "step_id": f"{NODE}#0", "step_index": 0, "step_number": None,
            "action": "click", "target": "button", "timestamp": "2026-10-12T10:31:07.412Z"}
    base.update(record)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(base) + "\n")
    svc.RunEventTailer(1, path, lambda: secrets).poll()
    return svc.get_buffer(1).read_after(0)[0][-1]


def test_valid_descriptive_fields_are_forwarded(tmp_path):
    event = publish(tmp_path, {
        "element_role": "button", "element_label": "Sign In", "element_hint": "Enter your email",
        "locator_by": "id", "locator_value": "login", "description": "Open the login page",
        "page": "example.test/login", "wait_condition": "presence_of_element_located", "wait_timeout": 10,
        "window_size": "1366x768", "source_line": 27,
    })
    assert event["element_label"] == "Sign In" and event["element_hint"] == "Enter your email"
    assert (event["locator_by"], event["locator_value"]) == ("id", "login")
    assert event["description"] == "Open the login page" and event["page"] == "example.test/login"
    assert event["wait_condition"] == "presence_of_element_located" and event["wait_timeout"] == 10.0
    assert event["window_size"] == "1366x768" and event["source_line"] == 27 and event["element_role"] == "button"


@pytest.mark.parametrize("bad_label", [
    "abcdefghijklmnopqrstuvwxyz0123456789",     # token-like
    "me@example.test",                          # e-mail
    "4111 1111 1111 1111",                      # card-like
    "password=hunter2",                         # key=value secret
    "<script>alert(1)</script>",                # markup
    "x" * 41,                                   # too long
    "tab\tseparated\x00",                       # control characters
    12345,                                      # not text
])
def test_unsafe_labels_are_dropped_not_edited(tmp_path, bad_label):
    event = publish(tmp_path, {"element_label": bad_label, "description": bad_label})
    assert "element_label" not in event and "description" not in event


def test_a_known_secret_in_a_label_or_description_drops_it(tmp_path):
    event = publish(tmp_path, {"element_label": "Welcome Hunter2Pass", "description": "Enter Hunter2Pass now"},
                    secrets=("Hunter2Pass",))
    assert "element_label" not in event and "description" not in event
    assert "Hunter2Pass" not in json.dumps(event)


def test_the_placeholder_of_a_sensitive_field_is_never_forwarded(tmp_path):
    event = publish(tmp_path, {"sensitive": True, "element_label": "Password", "element_hint": "Your password"})
    assert event["sensitive"] is True and event["element_label"] == "Password" and "element_hint" not in event


def test_invalid_locators_pages_and_other_fields_are_dropped(tmp_path):
    event = publish(tmp_path, {
        "locator_by": "javascript", "locator_value": "document.cookie",
        "page": "https://example.test/login?token=abc", "wait_condition": "bad name!", "wait_timeout": -5,
        "window_size": "wide", "source_line": -1, "element_role": "Button!", "sensitive": "yes",
    })
    for key in ("locator_by", "locator_value", "page", "wait_condition", "wait_timeout", "window_size",
                "source_line", "element_role", "sensitive"):
        assert key not in event, key


def test_a_locator_with_a_secret_looking_value_is_dropped_whole(tmp_path):
    event = publish(tmp_path, {"locator_by": "xpath", "locator_value": "//a[@id='token=abc123456789012345678901234567890']"})
    assert "locator_by" not in event and "locator_value" not in event
    ok = publish(tmp_path, {"step_id": f"{NODE}#1", "step_index": 1, "locator_by": "css selector", "locator_value": "form > button.primary"})
    assert ok["locator_value"] == "form > button.primary"      # selector syntax such as ">" is fine


def test_descriptive_fields_never_break_the_stream_contract(tmp_path):
    event = publish(tmp_path, {"element_label": "Sign In", "value": "TYPED", "url": "https://x.test/?t=1"})
    assert "value" not in event and "url" not in event and "nodeid" not in event


SAFE_PAGE_CASES = [
    "https://user:pw@example.test/login?token=abc#frag",
    "https://example.test:8443/a/b",
    "http://example.test/reset/ABCDEFGHIJKLMNOPQRSTUV123/confirm",
    "https://example.test/orders/12345678901/items",
    "https://example.test/files/0123456789abcdef0123/x",
    "ftp://example.test/x",
    "javascript:alert(1)",
    "not a url",
    "https://example.test/" + "a/" * 80,
]


def test_safe_page_rules():
    assert svc.safe_page(SAFE_PAGE_CASES[0]) == "example.test/login"
    assert svc.safe_page(SAFE_PAGE_CASES[1]) == "example.test:8443/a/b"
    assert svc.safe_page(SAFE_PAGE_CASES[2]) == "example.test/reset/…/confirm"
    assert svc.safe_page(SAFE_PAGE_CASES[3]) == "example.test/orders/…/items"
    assert svc.safe_page(SAFE_PAGE_CASES[4]) == "example.test/files/…/x"
    for rejected in SAFE_PAGE_CASES[5:]:
        assert svc.safe_page(rejected) == ""


def test_runner_and_backend_agree_on_safe_pages(tmp_path):
    result, _ = run_script(f"print(json.dumps([mod._safe_page(u) for u in {SAFE_PAGE_CASES!r}]))", tmp_path)
    assert result == [svc.safe_page(u) for u in SAFE_PAGE_CASES]


@pytest.mark.parametrize("stored,category", [
    ("RuntimeError('boom')", "RuntimeError"),
    ("NoSuchElementException: Message: no such element: Unable to locate element", "NoSuchElementException"),
    ("AssertionError()", "AssertionError"),
    ("TimeoutException", "TimeoutException"),
])
def test_replay_reads_the_exception_class_from_every_stored_error_shape(stored, category):
    assert svc._ERROR_CLASS_RE.match(stored).group(1) == category


def test_replay_of_a_finished_run_gives_readable_facts_without_inventing_any(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    test = TestResult(run_id=run.id, nodeid="t.py::test_a", file_name="t.py", class_name=None, method_name="test_a",
                      status="FAILED", duration_seconds=1.0)
    db_session.add(test)
    db_session.flush()
    when = datetime(2026, 10, 12, 10, 0, 0)
    rows = [
        ("open", "https://user:pw@example.test/login?token=SECRETQ", "PASS", None),
        ("click", "id=login", "PASS", None),
        ("type", "css=input.email", "PASS", None),
        ("click", "xpath=//a[@id='token=abc123456789012345678901234567890']", "FAIL", "TimeoutException('x')"),
        ("click", "button", "PASS", None),
    ]
    db_session.add_all([
        StepResult(test_result_id=test.id, step_number=i + 1, action=a, target=t, value="***", status=s,
                   duration_seconds=0.1, error=e, sequence_index=i, occurred_at=when)
        for i, (a, t, s, e) in enumerate(rows)
    ])
    db_session.commit()

    response = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/events", headers=auth_headers)
    events = [json.loads(block.split("data: ", 1)[1]) for block in response.text.split("\n\n") if "data: " in block]
    steps = [e for e in events if e["type"].startswith("step_")]
    assert steps[0]["page"] == "example.test/login"
    assert (steps[1]["locator_by"], steps[1]["locator_value"]) == ("id", "login")
    assert (steps[2]["locator_by"], steps[2]["locator_value"]) == ("css selector", "input.email")
    assert "locator_value" not in steps[3] and steps[3]["error_category"] == "TimeoutException"
    assert "locator_by" not in steps[4] and steps[4]["target"] == "button"
    assert not any("element_label" in e or "description" in e for e in steps)    # never recorded, so never shown
    assert all(e["type"] != "step_started" for e in steps)
    for secret in ("SECRETQ", "user:pw", "***", "abc12345678901"):
        assert secret not in response.text
