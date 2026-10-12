"""
Live run events: the backend half of the runner's events.jsonl.

selenium_compat_runner (running inside the pytest subprocess) appends one JSON
object per line to <run temp dir>/events.jsonl. This module

  * tails that file by byte offset (RunEventTailer), turning each complete line
    into a validated, sanitized public event,
  * keeps those events in a bounded in-memory buffer per run, with a
    backend-assigned, increasing `seq` (RunEventBuffer / the registry), and
  * serves them as Server-Sent Events with replay from a cursor (sse_stream),
    falling back to the persisted StepResult rows for runs whose buffer is gone
    (replay_from_results).

Limits (single uvicorn process, no external infrastructure):
  * The buffer lives in this process's memory. It is not shared between backend
    workers and does not survive a restart. After a restart, finished runs replay
    from the database (completion events only); a run that was executing is
    reported through its database status.
  * MAX_EVENTS_PER_RUN events are kept per run (oldest dropped first; a client that
    asks for a dropped range is told with a "gap" event). At most MAX_RETAINED_RUNS
    buffers are kept; a finished run's buffer is dropped TERMINAL_RETENTION_SECONDS
    after its terminal event, an untouched unfinished one after IDLE_RETENTION_SECONDS.
  * Cleanup is lazy (on registry access); there is no background thread.

Parametrize ids (the "[...]" part of a pytest node id) are never shown: they can hold
credentials passed as parameters that no sanitizer can recognise.

Security: nothing from events.jsonl is trusted. The uploaded test code runs in the
same directory and can write that file, so every record is validated against a
whitelist, strings are re-validated or sanitized with failure_sanitizer, and only
public fields are copied. The raw pytest node id is a private correlation key: the
public ids are generated here (t1, t1-s0, ...) and the display name goes through
sanitize_identifier, so a lossy sanitized name is never used to join events.
"""

import asyncio
import json
import logging
import math
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from app.services.failure_sanitizer import sanitize_identifier

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_RUN = 2000
MAX_RETAINED_RUNS = 50
TERMINAL_RETENTION_SECONDS = 600
IDLE_RETENTION_SECONDS = 2 * 60 * 60

MAX_READ_BYTES = 1024 * 1024        # per poll
MAX_LINE_BYTES = 64 * 1024          # a longer line is dropped, never parsed
MAX_TESTS_PER_RUN = 500
MAX_STEP_INDEX = 100_000

POLL_INTERVAL_SECONDS = 0.25
HEARTBEAT_SECONDS = 15
STATUS_PROBE_SECONDS = 5

TERMINAL_TYPES = {"COMPLETED": "run_completed", "FAILED": "run_failed", "CANCELLED": "run_cancelled"}
_TERMINAL_EVENT_TYPES = frozenset(TERMINAL_TYPES.values())

_STEP_TYPES = {"step_started": "RUNNING", "step_passed": "PASSED", "step_failed": "FAILED"}
_TEST_STATUSES = ("PASSED", "FAILED", "SKIPPED")
_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_ -]{0,60}$")
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,30}$")
_CATEGORY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,80}$")
_ROLE_RE = re.compile(r"^[a-z]{3,20}$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")
_PAGE_RE = re.compile(r"^[A-Za-z0-9.-]{1,100}(?::\d{1,5})?(?:/[^\s?#]*)?$")
_WINDOW_RE = re.compile(r"^\d{2,5}x\d{2,5}$")
_LOCATOR_BY = frozenset(("id", "name", "xpath", "link text", "partial link text", "tag name", "class name", "css selector"))
_IDE_BY = {"id": "id", "name": "name", "css": "css selector", "xpath": "xpath", "linktext": "link text",
           "link": "link text", "class": "class name", "tag": "tag name"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LONG_DIGITS_RE = re.compile(r"\d{8,}|\d(?:[ -]?\d){11,}")
_TOKEN_SEGMENT_RE = re.compile(r"^(?:[A-Za-z0-9_-]{20,}|[0-9a-fA-F]{16,})$|\d{8,}")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?Z$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Buffer + registry
# --------------------------------------------------------------------------

class RunEventBuffer:
    """Ordered, bounded events of one run. Written by the execution thread, read by
    any number of stream handlers; every access holds the lock."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=MAX_EVENTS_PER_RUN)
        self._next_seq = 1
        self._terminal = False
        self._finished_at: float | None = None
        self.last_touch = time.monotonic()

    def publish(self, payload: dict) -> int:
        """Stamps run_id and the next seq, appends, returns seq (0: rejected, run finished)."""
        with self._lock:
            if self._terminal:
                return 0  # nothing follows a terminal event
            seq = self._next_seq
            self._next_seq += 1
            self._events.append({**payload, "seq": seq, "run_id": self.run_id})
            self.last_touch = time.monotonic()
            return seq

    def publish_terminal(self, status: str) -> bool:
        """Appends the run's terminal event exactly once. Returns False if one was
        already published (cancel / timeout / normal exit can race)."""
        event_type = TERMINAL_TYPES.get(status, "run_failed")
        with self._lock:
            if self._terminal:
                return False
            self._terminal = True
            self._finished_at = time.monotonic()
            seq = self._next_seq
            self._next_seq += 1
            final_status = status if status in TERMINAL_TYPES else "FAILED"
            self._events.append(
                {"type": event_type, "status": final_status, "timestamp": _utc_now(),
                 "seq": seq, "run_id": self.run_id}
            )
            self.last_touch = time.monotonic()
            return True

    def read_after(self, after: int) -> tuple[list[dict], bool, int]:
        """(events with seq > after in order, terminal published, first retained seq)."""
        with self._lock:
            self.last_touch = time.monotonic()
            events = [dict(e) for e in self._events if e["seq"] > after]
            first = self._events[0]["seq"] if self._events else self._next_seq
            return events, self._terminal, first

    @property
    def terminal(self) -> bool:
        with self._lock:
            return self._terminal

    def expired(self, now: float) -> bool:
        with self._lock:
            if self._terminal and self._finished_at is not None:
                return now - self._finished_at > TERMINAL_RETENTION_SECONDS
            return now - self.last_touch > IDLE_RETENTION_SECONDS

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


_REGISTRY: dict[int, RunEventBuffer] = {}
_REGISTRY_LOCK = threading.Lock()


def _evict_locked(now: float) -> None:
    for run_id in [rid for rid, buf in _REGISTRY.items() if buf.expired(now)]:
        del _REGISTRY[run_id]
    overflow = len(_REGISTRY) - MAX_RETAINED_RUNS
    if overflow > 0:
        # Finished runs go first (oldest first); unfinished ones are never evicted by count.
        finished = sorted((b for b in _REGISTRY.values() if b.terminal), key=lambda b: b.last_touch)
        for buf in finished[:overflow]:
            _REGISTRY.pop(buf.run_id, None)


def open_run(run_id: int) -> RunEventBuffer:
    """The run's buffer, created if needed (a stream may attach before execution
    starts, so an existing buffer is reused)."""
    with _REGISTRY_LOCK:
        _evict_locked(time.monotonic())
        buf = _REGISTRY.get(run_id)
        if buf is None:
            buf = RunEventBuffer(run_id)
            _REGISTRY[run_id] = buf
        return buf


def get_buffer(run_id: int) -> RunEventBuffer | None:
    with _REGISTRY_LOCK:
        _evict_locked(time.monotonic())
        return _REGISTRY.get(run_id)


def publish_run_started(run_id: int) -> None:
    open_run(run_id).publish({"type": "run_started", "status": "RUNNING", "timestamp": _utc_now()})


def finish_run(run_id: int, status: str | None) -> bool:
    """Publishes the terminal event for the run's final database status, once."""
    return open_run(run_id).publish_terminal(status or "FAILED")


def reset_registry() -> None:  # tests
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


# --------------------------------------------------------------------------
# events.jsonl tailer
# --------------------------------------------------------------------------

class RunEventTailer:
    """Reads new complete lines of one run's events.jsonl and publishes them.

    One tailer per run, bound to that run's id and directory, so events of
    different runs cannot mix. The file is read by byte offset and never twice. A
    trailing line without its newline is kept for the next poll."""

    def __init__(self, run_id: int, path: Path, secrets_fn: Callable[[], Iterable[str]] | None = None):
        self.run_id = run_id
        self._path = Path(path)
        self._dir = self._path.parent
        self._secrets_fn = secrets_fn
        self._offset = 0
        self._partial = b""
        self._tests: dict[str, tuple[str, str]] = {}   # raw nodeid -> (public id, display name)
        self.malformed = 0
        self._buffer = open_run(run_id)

    # -- reading ----------------------------------------------------------
    def _readable(self) -> bool:
        try:
            # The upload runs in this directory: never follow a link it planted.
            return (
                not self._path.is_symlink()
                and self._path.is_file()
                and self._path.resolve().parent == self._dir.resolve()
            )
        except OSError:
            return False

    def poll(self) -> int:
        """Publishes whatever complete lines arrived since the last call. Never raises."""
        try:
            return self._poll_once()
        except Exception:
            logger.exception("Run %s: event tailing failed", self.run_id)
            return 0

    def drain(self) -> int:
        """Final read after the subprocess exited (or was killed): everything complete."""
        total = 0
        for _ in range(1000):
            before = self._offset
            total += self.poll()
            if self._offset == before:
                break
        return total

    def _poll_once(self) -> int:
        if not self._readable():
            return 0
        size = self._path.stat().st_size
        if size < self._offset:  # truncated/replaced by the upload: never re-read old data
            self._offset = size
            self._partial = b""
            return 0
        if size == self._offset:
            return 0
        with open(self._path, "rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read(min(size - self._offset, MAX_READ_BYTES))
        self._offset += len(chunk)

        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()          # b"" when the chunk ended on a newline
        if len(self._partial) > MAX_LINE_BYTES:
            self._partial = b""
            self.malformed += 1

        records = []
        for raw in lines:
            if not raw.strip():
                continue
            if len(raw) > MAX_LINE_BYTES:
                self.malformed += 1
                continue
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                self.malformed += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                self.malformed += 1
        if not records:
            return 0

        try:
            secrets = tuple(self._secrets_fn()) if self._secrets_fn else ()
        except Exception:
            secrets = ()

        published = 0
        for record in records:
            public = self._to_public(record, secrets)
            if public is None:
                self.malformed += 1
                continue
            self._buffer.publish(public)
            published += 1
        return published

    # -- validation + sanitization ---------------------------------------
    def _test_identity(self, nodeid: str, secrets: tuple[str, ...]) -> tuple[str, str] | None:
        known = self._tests.get(nodeid)
        if known:
            return known
        if len(self._tests) >= MAX_TESTS_PER_RUN:
            return None
        identity = (f"t{len(self._tests) + 1}", _public_test_name(nodeid, secrets))
        self._tests[nodeid] = identity
        return identity

    def _to_public(self, record: dict, secrets: tuple[str, ...]) -> dict | None:
        event_type = record.get("type")
        nodeid = record.get("nodeid")
        if not isinstance(nodeid, str) or not nodeid or len(nodeid) > 500:
            return None

        timestamp = record.get("timestamp")
        if not (isinstance(timestamp, str) and _TIMESTAMP_RE.match(timestamp)):
            timestamp = _utc_now()

        if event_type == "test_started":
            identity = self._test_identity(nodeid, secrets)
            if identity is None:
                return None
            return {"type": event_type, "test_id": identity[0], "test": identity[1],
                    "status": "RUNNING", "timestamp": timestamp}

        if event_type == "test_finished":
            identity = self._test_identity(nodeid, secrets)
            if identity is None:
                return None
            status = record.get("status")
            event = {"type": event_type, "test_id": identity[0], "test": identity[1],
                     "status": status if status in _TEST_STATUSES else "FAILED", "timestamp": timestamp}
            duration = _clean_duration(record.get("duration"))
            if duration is not None:
                event["duration"] = duration
            return event

        if event_type in _STEP_TYPES:
            index = record.get("step_index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= MAX_STEP_INDEX:
                return None
            identity = self._test_identity(nodeid, secrets)
            if identity is None:
                return None
            number = record.get("step_number")
            action = record.get("action")
            target = record.get("target")
            event = {
                "type": event_type,
                "test_id": identity[0],
                "test": identity[1],
                "step_id": f"{identity[0]}-s{index}",
                "step_index": index,
                "step_number": number if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= MAX_STEP_INDEX else None,
                "action": _clean_action(action, secrets),
                "target": target if isinstance(target, str) and _TAG_RE.match(target) else "",
                "status": _STEP_TYPES[event_type],
                "timestamp": timestamp,
            }
            if event_type != "step_started":
                duration = _clean_duration(record.get("duration"))
                if duration is not None:
                    event["duration"] = duration
            if event_type == "step_failed":
                category = record.get("error_category")
                event["error_category"] = category if isinstance(category, str) and _CATEGORY_RE.match(category) else "Error"
            event.update(_descriptive_fields(record, secrets))
            return event

        return None  # unknown type: never forwarded


def _public_test_name(nodeid: str, secrets: tuple[str, ...]) -> str:
    """Display name of a test. The bracketed parametrize id can hold arbitrary data
    (credentials passed as parameters) that no sanitizer can recognise, so it is not
    shown: "t.py::test_login[admin-Pw1]" becomes "t.py::test_login[…]". The public
    test_id still tells variants apart."""
    base, bracket, _ = nodeid.partition("[")
    name = sanitize_identifier(base, secrets) or ""
    return (name + ("[…]" if bracket else ""))[:200]


def _clean_text(value, limit: int, secrets: tuple[str, ...], strict: bool = True) -> str | None:
    """A short piece of display text, or None. Anything the sanitizer would change
    (a known secret, key=value, a URL with credentials), anything secret-looking, and
    anything over the limit is dropped, not edited: a half-redacted label is still a
    guess."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit or _CONTROL_RE.search(text):
        return None
    if strict and ("@" in text or "<" in text or ">" in text or _LONG_DIGITS_RE.search(text) or re.search(r"[A-Za-z0-9+/_=-]{20,}", text)):
        return None
    return text if sanitize_identifier(text, secrets) == text else None


def _locator_fields(by, value, secrets: tuple[str, ...]) -> dict:
    if by not in _LOCATOR_BY or not isinstance(value, str):
        return {}
    text = _clean_text(value, 80, secrets, strict=False)
    if not text or _LONG_DIGITS_RE.search(text) or re.search(r"[A-Za-z0-9+/_=-]{32,}", text):
        return {}
    return {"locator_by": by, "locator_value": text}


def safe_page(url) -> str:
    """host[:port] + path of an http(s) URL; credentials, query, fragment and
    token-like path segments are removed. Empty when nothing safe remains."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(str(url).strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        path = "/".join("…" if _TOKEN_SEGMENT_RE.search(seg) else seg for seg in parts.path.split("/"))
        page = host + path
        return page if len(page) <= 100 and not _CONTROL_RE.search(page) else ""
    except Exception:
        return ""


def _descriptive_fields(record: dict, secrets: tuple[str, ...]) -> dict:
    """The optional, human-readable facts of a step. Each is validated on its own and
    left out if it fails; none of them can carry typed text (the runner never reads it)."""
    out: dict = {}
    sensitive = record.get("sensitive") is True
    if sensitive:
        out["sensitive"] = True

    role = record.get("element_role")
    if isinstance(role, str) and _ROLE_RE.match(role):
        out["element_role"] = role
    label = _clean_text(record.get("element_label"), 40, secrets)
    if label:
        out["element_label"] = label
    hint = _clean_text(record.get("element_hint"), 40, secrets)
    if hint and not sensitive:  # a credential field's placeholder is never shown
        out["element_hint"] = hint

    out.update(_locator_fields(record.get("locator_by"), record.get("locator_value"), secrets))

    description = _clean_text(record.get("description"), 100, secrets)
    if description:
        out["description"] = description

    page = record.get("page")
    if isinstance(page, str) and _PAGE_RE.match(page) and len(page) <= 100:
        page = _clean_text(page, 100, secrets, strict=False)
        if page:
            out["page"] = page

    condition = record.get("wait_condition")
    if isinstance(condition, str) and _NAME_RE.match(condition):
        out["wait_condition"] = condition
    timeout = record.get("wait_timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 0 <= timeout <= 3600:
        out["wait_timeout"] = round(float(timeout), 2)
    if record.get("wait_until_not") is True:
        out["wait_until_not"] = True

    size = record.get("window_size")
    if isinstance(size, str) and _WINDOW_RE.match(size):
        out["window_size"] = size
    line = record.get("source_line")
    if isinstance(line, int) and not isinstance(line, bool) and 0 < line < 1_000_000:
        out["source_line"] = line
    return out


def _clean_duration(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return round(float(value), 4)


def _clean_action(value, secrets: tuple[str, ...]) -> str:
    if isinstance(value, str) and _ACTION_RE.match(value):
        return (sanitize_identifier(value, secrets) or "step")[:60]
    return "step"


# --------------------------------------------------------------------------
# Replay for runs without a buffer
# --------------------------------------------------------------------------

_ERROR_CLASS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]{0,80})(?:\(|:|$)")


def replay_from_results(db, run, project_file_contents: Iterable[str] = ()) -> RunEventBuffer:
    """A detached buffer rebuilt from the persisted result rows of a finished run.

    Only completion events exist in the database, so no step_started is invented.
    Persisted text is sanitized again on the way out (rows written before the
    sanitizer existed); messages are not forwarded, only the exception class."""
    from sqlalchemy import select

    from app.models.step_result import StepResult
    from app.models.test_result import TestResult
    from app.services.failure_sanitizer import collect_known_secrets

    secrets = collect_known_secrets(project_file_contents)
    buf = RunEventBuffer(run.id)

    tests = db.scalars(
        select(TestResult).where(TestResult.run_id == run.id).order_by(TestResult.id).limit(MAX_TESTS_PER_RUN)
    ).all()
    for ordinal, test in enumerate(tests, start=1):
        test_id = f"t{ordinal}"
        name = _public_test_name(test.nodeid, secrets)
        buf.publish({"type": "test_started", "test_id": test_id, "test": name, "status": "RUNNING",
                     "timestamp": _iso(test.started_at)})
        steps = db.scalars(
            select(StepResult).where(StepResult.test_result_id == test.id).order_by(StepResult.sequence_index)
        ).all()
        for index, step in enumerate(steps):
            failed = (step.status or "").upper().startswith("FAIL")
            event = {
                "type": "step_failed" if failed else "step_passed",
                "test_id": test_id,
                "test": name,
                "step_id": f"{test_id}-s{index}",
                "step_index": index,
                "step_number": step.step_number if isinstance(step.step_number, int) else None,
                "action": _clean_action(step.action, secrets),
                "target": step.target if isinstance(step.target, str) and _TAG_RE.match(step.target) else "",
                "status": "FAILED" if failed else "PASSED",
                "timestamp": _iso(step.occurred_at),
            }
            persisted = step.target if isinstance(step.target, str) else ""
            if (step.action or "").lower() == "open":
                page = safe_page(persisted)
                if page:
                    event["page"] = page
            elif "=" in persisted and not _TAG_RE.match(persisted):
                kind, _, rest = persisted.partition("=")
                event.update(_locator_fields(_IDE_BY.get(kind.strip().lower()), rest, secrets))
            if step.duration_seconds is not None and _clean_duration(step.duration_seconds) is not None:
                event["duration"] = _clean_duration(step.duration_seconds)
            if failed:
                match = _ERROR_CLASS_RE.match(step.error or "")
                event["error_category"] = match.group(1) if match else "Error"
            buf.publish(event)
        status = test.status.upper() if isinstance(test.status, str) else "FAILED"
        buf.publish({"type": "test_finished", "test_id": test_id, "test": name,
                     "status": {"PASSED": "PASSED", "SKIPPED": "SKIPPED"}.get(status, "FAILED"),
                     "timestamp": _iso(test.completed_at)})
    buf.publish_terminal(run.status)
    return buf


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"
    return _utc_now()


# --------------------------------------------------------------------------
# Server-Sent Events
# --------------------------------------------------------------------------

def format_sse(event: dict) -> str:
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"


async def sse_stream(
    buffer: RunEventBuffer,
    after: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    status_probe: Callable[[], Awaitable[str | None]] | None = None,
    max_seconds: float = 600.0,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    probe_seconds: float = STATUS_PROBE_SECONDS,
):
    """Yields SSE text: events with seq > after, then new ones as they arrive.

    Ends once the terminal event has been delivered and everything before it is
    drained, when the client disconnects, or after max_seconds (a client resumes
    with its last id). Purely asynchronous: waiting is asyncio.sleep, and the only
    database access (status_probe) is supplied by the caller and runs off the loop."""
    started = time.monotonic()
    last_sent = after
    last_write = time.monotonic()
    last_probe = time.monotonic()
    warned_gap = False

    yield ": connected\n\n"
    while True:
        events, terminal, first_seq = buffer.read_after(last_sent)
        if events:
            if not warned_gap and last_sent + 1 < first_seq and events[0]["seq"] == first_seq:
                warned_gap = True
                yield f"event: gap\ndata: {json.dumps({'first_seq': first_seq})}\n\n"
            for event in events:
                yield format_sse(event)
                last_sent = event["seq"]
            last_write = time.monotonic()
            if events[-1]["type"] in _TERMINAL_EVENT_TYPES:
                return
        elif terminal:
            return

        now = time.monotonic()
        if now - started > max_seconds:
            return
        if await is_disconnected():
            return

        # The terminal event normally comes from the execution thread. If it never
        # does (cancelled while still queued, or the backend restarted mid-run), the
        # database status decides.
        if status_probe is not None and not terminal and now - last_probe >= probe_seconds:
            last_probe = now
            try:
                status = await status_probe()
            except Exception:
                status = None
            if status in TERMINAL_TYPES:
                buffer.publish_terminal(status)
                continue

        if now - last_write >= heartbeat_seconds:
            last_write = now
            yield ": heartbeat\n\n"
        await asyncio.sleep(poll_interval)
