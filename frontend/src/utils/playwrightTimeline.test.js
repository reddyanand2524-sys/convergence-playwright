import assert from "node:assert/strict";
import { test } from "node:test";

import { groupRowsByTest, initialTimeline, outcomeOf, timelineReducer } from "./runTimeline.js";

// A Playwright run publishes lifecycle and test events only; there are no step events.
let seq = 0;
const ev = (type, fields = {}) => ({ seq: ++seq, run_id: 9, type, timestamp: "2026-10-21T10:00:00.000Z", ...fields });
const play = (timeline, event) => timelineReducer(timeline, { type: "event", runId: "9", event });

test("test-level events build a test list with no step rows and nothing to wait for", () => {
  let timeline = initialTimeline(9);
  timeline = play(timeline, ev("run_started", { status: "RUNNING" }));
  timeline = play(timeline, ev("test_started", { test_id: "t1", test: "tests/test_a.py::test_login" }));

  assert.deepEqual(timeline.rows, []);
  assert.equal(timeline.tests.length, 1);
  assert.equal(timeline.tests[0].status, "RUNNING");

  timeline = play(timeline, ev("test_finished", { test_id: "t1", test: "tests/test_a.py::test_login", status: "PASSED", duration: 1.5 }));
  assert.equal(timeline.tests[0].status, "PASSED");
  assert.equal(timeline.tests[0].duration, 1.5);
  assert.deepEqual(timeline.rows, []);
});

test("the run's terminal event ends the view even though no step ever arrived", () => {
  let timeline = initialTimeline(9);
  timeline = play(timeline, ev("test_started", { test_id: "t1", test: "t::a" }));
  timeline = play(timeline, ev("run_failed", { status: "FAILED" }));

  assert.equal(outcomeOf(timeline, "RUNNING").text, "Run failed");
  // a test that never reported a finish is shown as stopped, not as forever running
  assert.equal(timeline.tests[0].status, "INTERRUPTED");
});

test("the polled status closes the view when the stream has not delivered the terminal event", () => {
  const timeline = initialTimeline(9);
  assert.equal(outcomeOf(timeline, "COMPLETED").text, "Run completed");
  assert.equal(outcomeOf(timeline, "RUNNING"), null);
});

test("grouping a Playwright timeline yields tests with no rows (they are listed by test, not by step)", () => {
  let timeline = initialTimeline(9);
  timeline = play(timeline, ev("test_started", { test_id: "t1", test: "t::a" }));
  const groups = groupRowsByTest(timeline);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].rows, []);
});
