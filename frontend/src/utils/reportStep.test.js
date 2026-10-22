import assert from "node:assert/strict";
import { test } from "node:test";

import { describeReportError, reportStepView } from "./reportStep.js";

// A complete step as the JSON report (docs/json-execution-report.md) writes it.
const full = {
  testId: "t1",
  stepNumber: 3,
  stepName: "Click the login button",
  command: "click",
  value: "admin",
  status: "FAILED",
  attempted: true,
  skipReason: null,
  executionTimeMs: 420,
  error: { type: "NoSuchElementException", category: "Locator", message: "element not found" },
  screenshot: "screenshots/step-3.png",
};

test("a complete report step renders exactly the values the report carries (Selenium unchanged)", () => {
  assert.deepEqual(reportStepView(full, 0), {
    number: 3,
    label: "CLICK",
    name: "Click the login button",
    status: "FAILED",
    notAttempted: false,
    executionTimeMs: 420,
    value: "admin",
    hasError: true,
    errorText: "NoSuchElementException (Locator): element not found",
    skipReason: "",
    screenshot: "screenshots/step-3.png",
  });
});

test("a step with no command or stepName does not throw and gets neutral labels", () => {
  const view = reportStepView({ stepNumber: 1, status: "PASSED" }, 0);
  assert.equal(view.label, "STEP");
  assert.equal(view.name, "");
  assert.equal(view.notAttempted, false);
  assert.equal(view.errorText, "");
  assert.equal(view.hasError, false);
});

test("an empty, null or non-object step does not throw", () => {
  for (const step of [undefined, null, {}, "text", 5, []]) {
    const view = reportStepView(step, 2);
    assert.equal(view.number, 3); // index + 1 when stepNumber is absent
    assert.equal(view.label, "STEP");
  }
});

test("label precedence follows the report schema: command, then its documented alias action", () => {
  assert.equal(reportStepView({ command: "click", action: "type" }).label, "CLICK");
  assert.equal(reportStepView({ action: "type" }).label, "TYPE");
  assert.equal(reportStepView({ command: "  ", action: "type" }).label, "TYPE");
  assert.equal(reportStepView({ command: 7 }).label, "STEP");
});

test("name precedence: stepName, then its alias name; API-only fields are never guessed", () => {
  assert.equal(reportStepView({ stepName: "A", name: "B" }).name, "A");
  assert.equal(reportStepView({ name: "B" }).name, "B");
  // result-API step fields (target / step_number) are a different shape and are not used
  const api = reportStepView({ action: "click", target: "#btn", step_number: 4 }, 0);
  assert.equal(api.name, "");
  assert.equal(api.number, 1);
});

test("only an explicit attempted:false means 'not attempted'", () => {
  assert.equal(reportStepView({ attempted: false }).notAttempted, true);
  assert.equal(reportStepView({ attempted: true }).notAttempted, false);
  assert.equal(reportStepView({}).notAttempted, false);
});

test("duration is shown only when it is a number", () => {
  assert.equal(reportStepView({ executionTimeMs: 0 }).executionTimeMs, 0);
  assert.equal(reportStepView({ executionTimeMs: "12" }).executionTimeMs, null);
  assert.equal(reportStepView({}).executionTimeMs, null);
});

test("values are shown as text, including numbers, but not when empty", () => {
  assert.equal(reportStepView({ value: 42 }).value, "42");
  assert.equal(reportStepView({ value: "" }).value, "");
  assert.equal(reportStepView({ value: null }).value, "");
});

test("report errors use whichever parts exist", () => {
  assert.equal(describeReportError({ type: "A", category: "B", message: "C" }), "A (B): C");
  assert.equal(describeReportError({ type: "A", message: "C" }), "A: C");
  assert.equal(describeReportError({ category: "B", message: "C" }), "B: C");
  assert.equal(describeReportError({ message: "C" }), "C");
  assert.equal(describeReportError({ type: "A" }), "A");
  assert.equal(describeReportError("plain failure"), "plain failure");
  for (const bad of [undefined, null, {}, 5, []]) assert.equal(describeReportError(bad), "");
});

test("a step whose error object has no usable text still counts as an error", () => {
  const view = reportStepView({ error: {} });
  assert.equal(view.hasError, true);
  assert.equal(view.errorText, "");
});
