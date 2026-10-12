/*
 * Timeline state for one run, built only from the events the backend publishes
 * (GET /projects/{pid}/runs/{rid}/events). Pure functions, no React.
 *
 * Event fields used (verified against the backend's run_event_service): seq, run_id,
 * type, test_id, test, step_id, step_index, step_number, action, target, status,
 * duration, error_category, timestamp. Nothing else is read, so nothing else can be
 * shown. Rows are keyed by the public step_id ("t1-s0"), so a step_started followed by
 * step_passed / step_failed updates one row. Steps that were never announced are never
 * invented: there are no "waiting" rows because the backend does not publish steps
 * before they start.
 */

export const TERMINAL_EVENT_TYPES = ["run_completed", "run_failed", "run_cancelled"];

export function initialTimeline(runId) {
  return {
    runId: String(runId),
    lastSeq: 0,
    rows: [],
    tests: [],
    terminal: null,
    gap: false,
  };
}

const STEP_STATUS = { step_started: "RUNNING", step_passed: "PASSED", step_failed: "FAILED" };

// A step that was still running when its test or the run ended did not pass or fail.
function interruptRunning(rows, testId) {
  let changed = false;
  const next = rows.map((row) => {
    if (row.status === "RUNNING" && (testId === undefined || row.testId === testId)) {
      changed = true;
      return { ...row, status: "INTERRUPTED" };
    }
    return row;
  });
  return changed ? next : rows;
}

function upsertTest(tests, testId, patch) {
  const index = tests.findIndex((t) => t.id === testId);
  if (index === -1) return [...tests, { id: testId, name: "", status: "RUNNING", ...patch }];
  const next = tests.slice();
  next[index] = { ...next[index], ...patch };
  return next;
}

function str(value, limit = 200) {
  return typeof value === "string" ? value.slice(0, limit) : "";
}

const LOCATOR_BY = ["id", "name", "xpath", "link text", "partial link text", "tag name", "class name", "css selector"];

// The optional, human-readable facts of a step (all text; rendered as text, never as HTML).
function factsOf(event) {
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return {
    role: str(event.element_role, 20),
    label: str(event.element_label, 40),
    hint: str(event.element_hint, 40),
    sensitive: event.sensitive === true,
    locatorBy: LOCATOR_BY.includes(event.locator_by) ? event.locator_by : "",
    locatorValue: LOCATOR_BY.includes(event.locator_by) ? str(event.locator_value, 80) : "",
    description: str(event.description, 100),
    page: str(event.page, 100),
    waitCondition: str(event.wait_condition, 60),
    waitTimeout: num(event.wait_timeout),
    waitUntilNot: event.wait_until_not === true,
    windowSize: str(event.window_size, 12),
    sourceLine: Number.isInteger(event.source_line) ? event.source_line : null,
  };
}

// A step's start and end events carry the same facts; replayed history may carry them only on
// the end event. Keep what the row already has and fill in only what is missing.
function mergeFacts(existing, incoming) {
  const merged = { ...existing };
  for (const [key, value] of Object.entries(incoming)) {
    const have = merged[key];
    if (have === undefined || have === "" || have === null || have === false) merged[key] = value;
  }
  return merged;
}

export function applyEvent(state, event) {
  if (!event || typeof event !== "object") return state;
  const { seq } = event;
  if (!Number.isInteger(seq) || seq <= state.lastSeq) return state; // duplicate or replayed
  // Another run's event is never shown (and never moves the cursor).
  if (event.run_id !== undefined && String(event.run_id) !== state.runId) return state;

  const base = { ...state, lastSeq: seq };
  const type = event.type;

  if (TERMINAL_EVENT_TYPES.includes(type)) {
    return {
      ...base,
      rows: interruptRunning(base.rows),
      tests: base.tests.map((t) => (t.status === "RUNNING" ? { ...t, status: "INTERRUPTED" } : t)),
      terminal: { type, status: str(event.status, 20) || type.replace("run_", "").toUpperCase() },
    };
  }

  if (type === "test_started" && typeof event.test_id === "string") {
    return { ...base, tests: upsertTest(base.tests, event.test_id, { name: str(event.test), status: "RUNNING" }) };
  }

  if (type === "test_finished" && typeof event.test_id === "string") {
    return {
      ...base,
      rows: interruptRunning(base.rows, event.test_id),
      tests: upsertTest(base.tests, event.test_id, {
        name: str(event.test),
        status: str(event.status, 20) || "FAILED",
        duration: typeof event.duration === "number" ? event.duration : undefined,
      }),
    };
  }

  if (STEP_STATUS[type] && typeof event.step_id === "string") {
    const status = STEP_STATUS[type];
    const testId = str(event.test_id, 40);
    const tests = testId && !base.tests.some((t) => t.id === testId)
      ? upsertTest(base.tests, testId, { name: str(event.test), status: "RUNNING" })
      : base.tests;
    const existing = base.rows.findIndex((r) => r.id === event.step_id);
    const row = {
      id: event.step_id,
      testId,
      index: Number.isInteger(event.step_index) ? event.step_index : 0,
      number: Number.isInteger(event.step_number) ? event.step_number : null,
      action: str(event.action, 60) || "step",
      target: str(event.target, 40),
      status,
      duration: typeof event.duration === "number" ? event.duration : null,
      errorCategory: type === "step_failed" ? str(event.error_category, 80) || "Error" : null,
      ...factsOf(event),
    };

    if (existing === -1) return { ...base, tests, rows: [...base.rows, row] };

    const rows = base.rows.slice();
    // Keep the identity fields from the first event; take the outcome from the latest.
    rows[existing] = {
      ...mergeFacts(rows[existing], factsOf(event)),
      status,
      duration: row.duration ?? rows[existing].duration,
      errorCategory: row.errorCategory,
    };
    return { ...base, tests, rows };
  }

  return base; // run_started and anything unknown: only the cursor advances
}

export function timelineReducer(state, action) {
  switch (action.type) {
    case "reset":
      return initialTimeline(action.runId);
    case "event":
      // An event dispatched for a run that is no longer on screen is dropped.
      return String(action.runId) === state.runId ? applyEvent(state, action.event) : state;
    case "gap":
      return String(action.runId) === state.runId ? { ...state, gap: true } : state;
    default:
      return state;
  }
}

export function groupRowsByTest(timeline) {
  const groups = [];
  const byId = new Map();
  for (const test of timeline.tests) {
    const group = { id: test.id, name: test.name, status: test.status, rows: [] };
    byId.set(test.id, group);
    groups.push(group);
  }
  for (const row of timeline.rows) {
    let group = byId.get(row.testId);
    if (!group) {
      group = { id: row.testId, name: "", status: "RUNNING", rows: [] };
      byId.set(row.testId, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

export const STATUS_LABEL = {
  RUNNING: "RUNNING",
  PASSED: "PASSED",
  FAILED: "FAILED",
  INTERRUPTED: "STOPPED",
};

export function formatStepDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`;
}

const OUTCOME_TEXT = {
  run_completed: "Run completed",
  run_failed: "Run failed",
  run_cancelled: "Run cancelled",
};
const STATUS_OUTCOME = { COMPLETED: "run_completed", FAILED: "run_failed", CANCELLED: "run_cancelled" };

/** Final outcome line: the stream's terminal event, else the polled run status. */
export function outcomeOf(timeline, runStatus) {
  const type = timeline.terminal?.type || STATUS_OUTCOME[runStatus];
  return type ? { type, text: OUTCOME_TEXT[type] } : null;
}
