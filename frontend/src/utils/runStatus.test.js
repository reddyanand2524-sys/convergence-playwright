import assert from "node:assert/strict";
import { test } from "node:test";

import { hasReport, isActive, isRetryable, isTerminal, runStateChanged } from "./runStatus.js";

const EXPECTED = {
  QUEUED: { active: true, terminal: false, report: false },
  RUNNING: { active: true, terminal: false, report: false },
  COMPLETED: { active: false, terminal: true, report: true },
  FAILED: { active: false, terminal: true, report: true },
  CANCELLED: { active: false, terminal: true, report: false },
};

for (const [status, want] of Object.entries(EXPECTED)) {
  test(`${status} helpers`, () => {
    assert.equal(isActive(status), want.active);
    assert.equal(isTerminal(status), want.terminal);
    assert.equal(hasReport(status), want.report);
    assert.equal(isRetryable(status), want.report);
  });
}

test("unknown, malformed and missing statuses are never active, terminal or reportable", () => {
  for (const status of ["TIMED_OUT", "PARTIAL", "completed", " COMPLETED", "", null, undefined, 0, {}, []]) {
    assert.equal(isActive(status), false, `isActive(${JSON.stringify(status)})`);
    assert.equal(isTerminal(status), false, `isTerminal(${JSON.stringify(status)})`);
    assert.equal(hasReport(status), false, `hasReport(${JSON.stringify(status)})`);
    assert.equal(isRetryable(status), false, `isRetryable(${JSON.stringify(status)})`);
  }
});

test("active and terminal are mutually exclusive", () => {
  for (const status of Object.keys(EXPECTED)) {
    assert.notEqual(isActive(status), isTerminal(status));
  }
});

test("runStateChanged tells a moved-on run from a refusal that changed nothing", () => {
  const queued = { id: 1, status: "QUEUED", cancel_requested: false };
  assert.equal(runStateChanged(queued, { ...queued, status: "RUNNING" }), true);
  assert.equal(runStateChanged(queued, { ...queued, status: "CANCELLED" }), true);
  assert.equal(runStateChanged({ status: "RUNNING", cancel_requested: false }, { status: "RUNNING", cancel_requested: true }), true);
  // same status and flag: the 409 meant something else (e.g. runtime unavailable) - show its message
  assert.equal(runStateChanged(queued, { ...queued }), false);
  assert.equal(runStateChanged({ status: "QUEUED" }, { status: "QUEUED", cancel_requested: false }), false);
  assert.equal(runStateChanged(null, queued), false);
  assert.equal(runStateChanged(queued, undefined), false);
});
