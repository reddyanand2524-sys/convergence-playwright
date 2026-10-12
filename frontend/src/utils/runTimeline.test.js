import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyEvent,
  formatStepDuration,
  groupRowsByTest,
  initialTimeline,
  outcomeOf,
  timelineReducer,
} from "./runTimeline.js";

let seq = 0;
const ev = (type, fields = {}) => ({ seq: ++seq, run_id: 7, type, timestamp: "2026-10-12T10:00:00.000Z", ...fields });
const step = (type, index, extra = {}) =>
  ev(type, {
    test_id: "t1", test: "t.py::test_a", step_id: `t1-s${index}`, step_index: index, step_number: index + 1,
    action: "click", target: "button", ...extra,
  });

function run(events, state = initialTimeline(7)) {
  return events.reduce((s, e) => timelineReducer(s, { type: "event", runId: 7, event: e }), state);
}

test("a started step becomes passed or failed in the same row", () => {
  seq = 0;
  const state = run([
    ev("test_started", { test_id: "t1", test: "t.py::test_a" }),
    step("step_started", 0),
    step("step_passed", 0, { duration: 0.25 }),
    step("step_started", 1),
    step("step_failed", 1, { duration: 1.5, error_category: "RuntimeError" }),
  ]);
  assert.equal(state.rows.length, 2);
  assert.deepEqual(state.rows.map((r) => [r.id, r.status, r.duration, r.errorCategory]), [
    ["t1-s0", "PASSED", 0.25, null],
    ["t1-s1", "FAILED", 1.5, "RuntimeError"],
  ]);
});

test("the running step is the one without an outcome yet", () => {
  seq = 0;
  const state = run([step("step_started", 0), step("step_passed", 0), step("step_started", 1)]);
  assert.deepEqual(state.rows.map((r) => r.status), ["PASSED", "RUNNING"]);
});

test("duplicates and replayed events are ignored; order follows seq", () => {
  seq = 0;
  const events = [step("step_started", 0), step("step_passed", 0), step("step_started", 1)];
  const once = run(events);
  const twice = run(events, once); // a reconnect replays everything
  assert.deepEqual(twice, once);
  assert.equal(once.lastSeq, 3);
  assert.equal(applyEvent(once, { ...events[0] }), once);
});

test("replayed history without step_started still produces rows (nothing is invented)", () => {
  seq = 0;
  const state = run([step("step_passed", 0, { duration: 0.1 }), step("step_failed", 1, { error_category: "ValueError" })]);
  assert.deepEqual(state.rows.map((r) => r.status), ["PASSED", "FAILED"]);
});

test("a terminal event ends the run and marks a still-running step as stopped, never passed", () => {
  seq = 0;
  const state = run([
    ev("test_started", { test_id: "t1", test: "x" }),
    step("step_started", 0),
    ev("run_cancelled", { status: "CANCELLED" }),
  ]);
  assert.equal(state.rows[0].status, "INTERRUPTED");
  assert.equal(state.terminal.type, "run_cancelled");
  assert.equal(outcomeOf(state, "RUNNING").text, "Run cancelled");
});

test("a run with no steps still reports its outcome; the polled status is the fallback", () => {
  seq = 0;
  const state = run([ev("run_started"), ev("run_failed", { status: "FAILED" })]);
  assert.equal(state.rows.length, 0);
  assert.equal(outcomeOf(state, "FAILED").type, "run_failed");
  assert.equal(outcomeOf(initialTimeline(7), "COMPLETED").text, "Run completed");
  assert.equal(outcomeOf(initialTimeline(7), "RUNNING"), null);
});

test("events of another run are dropped and do not move the cursor", () => {
  seq = 0;
  const state = run([{ ...step("step_started", 0), run_id: 8 }]);
  assert.equal(state.rows.length, 0);
  assert.equal(state.lastSeq, 0);
});

test("switching runs: history is reset, and events still in flight for the old run are dropped", () => {
  seq = 0;
  const first = run([step("step_started", 0)]);
  assert.equal(first.rows.length, 1);
  const switched = timelineReducer(first, { type: "reset", runId: 9 });
  assert.equal(switched.runId, "9");
  assert.deepEqual(switched.rows, []);
  const late = timelineReducer(switched, { type: "event", runId: 7, event: step("step_passed", 0) });
  assert.equal(late, switched); // dispatched for run 7 while run 9 is on screen
  const retry = timelineReducer(switched, { type: "event", runId: 9, event: { ...step("step_started", 0), run_id: 9, seq: 1 } });
  assert.equal(retry.rows.length, 1); // a retry (new run id) starts from seq 1 with a clean history
});

test("a gap marks the timeline; unknown event types only advance the cursor", () => {
  seq = 0;
  assert.equal(timelineReducer(initialTimeline(7), { type: "gap", runId: 7 }).gap, true);
  const unknown = run([ev("mystery")]);
  assert.equal(unknown.lastSeq, 1);
  assert.equal(unknown.rows.length, 0);
});

test("steps are grouped under their test, in order of first appearance", () => {
  seq = 0;
  const state = run([
    ev("test_started", { test_id: "t1", test: "a" }),
    step("step_passed", 0),
    ev("test_finished", { test_id: "t1", test: "a", status: "PASSED" }),
    ev("test_started", { test_id: "t2", test: "b" }),
    { ...step("step_passed", 0), test_id: "t2", step_id: "t2-s0" },
  ]);
  const groups = groupRowsByTest(state);
  assert.deepEqual(groups.map((g) => [g.id, g.status, g.rows.length]), [["t1", "PASSED", 1], ["t2", "RUNNING", 1]]);
});

test("only whitelisted fields reach the rows: typed values, URLs and error text cannot be shown", () => {
  seq = 0;
  const state = run([
    step("step_failed", 0, {
      value: "TYPED-PASSWORD-123",
      runtime_value: "TYPED-PASSWORD-123",
      url: "https://x.test/?token=SECRETTOK",
      error: "Traceback ... password=hunter2",
      error_category: "Error",
      nodeid: "t.py::secret[hunter2]",
    }),
  ]);
  const dumped = JSON.stringify(state);
  for (const secret of ["TYPED-PASSWORD-123", "SECRETTOK", "hunter2", "Traceback", "nodeid"]) {
    assert.ok(!dumped.includes(secret), secret);
  }
});

test("durations are formatted for people", () => {
  assert.equal(formatStepDuration(0.25), "250 ms");
  assert.equal(formatStepDuration(1.46), "1.5 s");
  assert.equal(formatStepDuration(null), "");
  assert.equal(formatStepDuration(-1), "");
});
