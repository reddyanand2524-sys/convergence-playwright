"""Screenshot-only redaction of typed fields in selenium_compat_runner.

Unit tests use a recording fake driver (no browser). The real-browser test drives
headless Chrome through the compat module in a subprocess (the module patches
selenium globally, so it must not be imported into the test session) and is
skipped when no browser can be started. Unit results and real-browser results are
reported separately by their test names.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

COMPAT = Path(__file__).parent.parent / "app" / "services" / "selenium_compat_runner.py"
SECRET = "TYPED-SECRET-code-91"


def run_script(body: str, shots_dir: Path, mode: str = "all", timeout: int = 90) -> tuple[dict, str]:
    """Runs `body` in a fresh interpreter with the compat module loaded as `mod`.
    Returns (json result, captured stdout+stderr)."""
    script = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('compat_under_test', {str(COMPAT)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n" + body
    )
    env = os.environ.copy()
    env.update({"SELENIUM_COMPAT_SCREENSHOT_MODE": mode, "SELENIUM_COMPAT_RUN_DIR": str(shots_dir)})
    # Output goes through files, not pipes: a browser driver left running by a failed
    # script would otherwise keep a pipe open and make subprocess.run wait forever.
    out_path, err_path = shots_dir / "_out.txt", shots_dir / "_err.txt"
    with open(out_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
        completed = subprocess.run([sys.executable, "-c", script], stdout=out, stderr=err, timeout=timeout, env=env)
    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, f"subprocess failed:\nstdout={stdout}\nstderr={stderr[-1500:]}"
    return json.loads(stdout.strip().splitlines()[-1]), stdout + stderr


FAKE_DRIVER = '''
class StaleElementReferenceException(Exception):
    pass

class FakeElement:
    def __init__(self, driver, name):
        self.parent = driver
        self.name = name

class FakeDriver:
    def __init__(self, fail_save=False, stale_names=()):
        self.calls = []
        self.fail_save = fail_save
        self.stale_names = set(stale_names)
    def execute_script(self, script, *args):
        if "[data-cv-redacted]" in script and "hasAttribute" not in script:
            self.calls.append("restore")
        elif args and isinstance(args[0], FakeElement):
            if args[0].name in self.stale_names:
                raise StaleElementReferenceException("stale element reference")
            self.calls.append("redact_element:" + args[0].name)
        elif "input[type=password]" in script:
            self.calls.append("redact_page:" + json.dumps(args[0]))
        else:
            self.calls.append("other")
    def save_screenshot(self, path):
        self.calls.append("save")
        if self.fail_save:
            raise RuntimeError("screenshot failed")
'''


# ---------------------------------------------------------------------------
# UNIT TESTS (fake driver, no browser)
# ---------------------------------------------------------------------------

def test_unit_typed_non_password_field_is_redacted_before_capture_and_restored_after(tmp_path):
    result, output = run_script(FAKE_DRIVER + f'''
driver = FakeDriver()
code = FakeElement(driver, "code")      # a field with an innocuous locator (id=code)
mod._track_typed(code, ("{SECRET}",))
name = mod.shot(driver, "step_3")
print(json.dumps({{"name": name, "calls": driver.calls}}))
''', tmp_path)

    assert result["name"].endswith(".png") and "step_3" in result["name"]          # filename format unchanged
    calls = result["calls"]
    assert calls == ["redact_element:code", f'redact_page:["{SECRET}"]', "save", "restore"]
    assert SECRET not in output.replace(json.dumps(result), "")                    # nothing logs the typed value


def test_unit_password_inputs_are_always_protected_even_when_nothing_was_typed(tmp_path):
    result, _ = run_script(FAKE_DRIVER + '''
driver = FakeDriver()
name = mod.shot(driver, "login")
print(json.dumps({"name": name, "calls": driver.calls}))
''', tmp_path)

    assert result["calls"] == ["redact_page:[]", "save", "restore"]                # password rule runs on every capture


def test_unit_page_is_restored_even_when_the_capture_raises(tmp_path):
    result, _ = run_script(FAKE_DRIVER + f'''
driver = FakeDriver(fail_save=True)
mod._track_typed(FakeElement(driver, "code"), ("{SECRET}",))
name = mod.shot(driver, "x")
print(json.dumps({{"name": name, "calls": driver.calls}}))
''', tmp_path)

    assert result["name"] == ""                                                    # same contract as before: "" on failure
    assert result["calls"][-2:] == ["save", "restore"]                             # restored after the failure


def test_unit_stale_or_missing_elements_never_fail_the_screenshot(tmp_path):
    result, _ = run_script(FAKE_DRIVER + f'''
driver = FakeDriver(stale_names=("gone",))
mod._track_typed(FakeElement(driver, "gone"), ("{SECRET}",))     # stale: script raises
mod._track_typed(FakeElement(driver, "alive"), ())
mod._track_typed(FakeElement(None, "other-driver"), ())          # belongs to another driver: skipped
mod._track_typed(None, ())                                       # no element at all (ActionChains.send_keys)
name = mod.shot(driver, "x")
print(json.dumps({{"name": name, "calls": driver.calls}}))
''', tmp_path)

    assert result["name"].endswith(".png")
    assert "redact_element:alive" in result["calls"] and "redact_element:gone" not in result["calls"]
    assert "redact_element:other-driver" not in result["calls"]
    assert result["calls"][-2:] == ["save", "restore"]


def test_unit_existing_behavior_without_tracked_elements_or_execute_script(tmp_path):
    """The pre-existing fake driver has only save_screenshot: capture must work unchanged."""
    result, _ = run_script('''
class Plain:
    def __init__(self):
        self.saved = []
    def save_screenshot(self, path):
        self.saved.append(path)
d = Plain()
name = mod.shot(d, "some_step")
print(json.dumps({"name": name, "saved": len(d.saved)}))
''', tmp_path)

    assert result["saved"] == 1 and result["name"].endswith("_some_step_" + result["name"].split("_some_step_")[1])


def test_unit_mode_none_never_touches_the_page(tmp_path):
    result, _ = run_script(FAKE_DRIVER + '''
driver = FakeDriver()
name = mod.shot(driver, "x")
print(json.dumps({"name": name, "calls": driver.calls}))
''', tmp_path, mode="none")

    assert result == {"name": "", "calls": []}


def test_unit_tracking_ignores_special_keys_and_resets_per_test(tmp_path):
    result, _ = run_script(FAKE_DRIVER + f'''
from types import SimpleNamespace
driver = FakeDriver()
mod._track_typed(FakeElement(driver, "a"), ("{SECRET}", "\\ue007", "x", 5))   # Keys.ENTER, 1-char, non-str
before = list(mod.CUR.typed_values)
for i in range(80):
    mod._track_typed(FakeElement(driver, f"e{{i}}"), (f"value-{{i}}",))
capped = (len(mod.CUR.typed_elements), len(mod.CUR.typed_values))
mod.pytest_runtest_setup(SimpleNamespace(nodeid="next::test"))
print(json.dumps({{"before": before, "capped": capped, "after_reset": [len(mod.CUR.typed_elements), len(mod.CUR.typed_values)]}}))
''', tmp_path)

    assert result["before"] == [SECRET]            # special keys, 1-char and non-string values are not tracked
    assert result["capped"] == [50, 50]
    assert result["after_reset"] == [0, 0]


# ---------------------------------------------------------------------------
# REAL-BROWSER TEST (headless Chrome through the compat layer)
# ---------------------------------------------------------------------------

REAL_BROWSER_SCRIPT = '''
import io, os
from PIL import Image, ImageChops
from selenium import webdriver
from selenium.webdriver.common.by import By

if True:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=900,700")
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as exc:
        print(json.dumps({"skipped": "no browser: " + type(exc).__name__}))
        sys.exit(0)
    import atexit
    atexit.register(driver.quit)          # never leave a browser/driver process behind

PAGE = ("data:text/html,<title>t</title><style>input{font-size:24px;margin:8px;display:block;width:420px}</style>"
        "<input id=code><input id=name value=visible-untyped-value><input id=pw type=password>")
driver.get(PAGE)
mod.pytest_runtest_setup(type("I", (), {"nodeid": "real::browser"})())

def region(img, el):
    r = el.rect
    return img.crop((int(r["x"]), int(r["y"]), int(r["x"] + r["width"]), int(r["y"] + r["height"]))).convert("L")

def diff(a, b):
    return sum(ImageChops.difference(a, b).getdata()) / (a.width * a.height)

code = driver.find_element(By.ID, "code")
code.send_keys("TYPED-SECRET-code-91")                     # patched send_keys: tracked
driver.execute_script("document.getElementById('pw').value = 'prefilled-not-typed'")   # never typed by the test
plain = Image.open(io.BytesIO(driver.get_screenshot_as_png()))
name = mod.shot(driver, "after_typing")
redacted = Image.open(os.path.join(os.environ["SELENIUM_COMPAT_RUN_DIR"], "screenshots", name))
restored = Image.open(io.BytesIO(driver.get_screenshot_as_png()))

els = {i: driver.find_element(By.ID, i) for i in ("code", "name", "pw")}
leftovers = driver.execute_script("return document.querySelectorAll('[data-cv-redacted]').length")
filters = [driver.execute_script("return arguments[0].style.filter", e) for e in els.values()]
values = {i: e.get_attribute("value") for i, e in els.items()}

measured = {
    "typed_field_changed": diff(region(plain, els["code"]), region(redacted, els["code"])),
    "untyped_field_changed": diff(region(plain, els["name"]), region(redacted, els["name"])),
    "password_changed": diff(region(plain, els["pw"]), region(redacted, els["pw"])),
    "restored_vs_original": diff(plain.convert("L"), restored.convert("L")),
}

# still interactive after a capture (typed more into an untouched field)
els["name"].send_keys("-more")
name_after = els["name"].get_attribute("value")

# page re-render: the tracked element goes stale, the new field holds the same typed text
driver.execute_script("document.body.innerHTML = \\"<input id=again style='font-size:24px;width:420px'>\\"; "
                      "document.getElementById('again').value = 'TYPED-SECRET-code-91'")
again = driver.find_element(By.ID, "again")
plain2 = Image.open(io.BytesIO(driver.get_screenshot_as_png()))
name2 = mod.shot(driver, "after_rerender")
redacted2 = Image.open(os.path.join(os.environ["SELENIUM_COMPAT_RUN_DIR"], "screenshots", name2))

print(json.dumps({
    **measured,
    "leftover_attrs": leftovers, "filters": filters, "values": values,
    "rerendered_field_changed": diff(region(plain2, again), region(redacted2, again)),
    "name_after_more_typing": name_after,
    "shot_names": [name, name2],
}))
driver.quit()
'''


def test_real_browser_redacts_typed_and_password_fields_and_restores_the_page(tmp_path):
    result, output = run_script(REAL_BROWSER_SCRIPT, tmp_path, timeout=120)
    if "skipped" in result:
        pytest.skip(result["skipped"])

    assert result["typed_field_changed"] > 3, "typed non-password field (id=code) was not redacted"
    assert result["password_changed"] > 1, "password input was not redacted"
    # blur spills a few pixels onto a neighbouring field's border, so compare relatively
    assert result["untyped_field_changed"] < result["typed_field_changed"] / 3, "an untouched field must not be redacted"
    assert result["restored_vs_original"] < 0.01, "page appearance was not restored after capture"
    assert result["leftover_attrs"] == 0 and result["filters"] == ["", "", ""]
    assert result["values"] == {"code": SECRET, "name": "visible-untyped-value", "pw": "prefilled-not-typed"}
    assert result["rerendered_field_changed"] > 3, "re-rendered field holding typed text was not redacted"
    assert result["name_after_more_typing"] == "visible-untyped-value-more"            # still interactive
    assert all(n.endswith(".png") for n in result["shot_names"])
    assert SECRET not in output.replace(json.dumps(result), "")                          # no logging of the value
