import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { initialTimeline, timelineReducer } from "./runTimeline.js";
import { describeStep, failureSummary, genericTitle, kindOf, locatorText } from "./stepDescription.js";

let seq = 0;
const base = (type, index, fields = {}) => ({
  seq: ++seq, run_id: 7, type, test_id: "t1", test: "t.py::test_a", step_id: `t1-s${index}`, step_index: index,
  step_number: null, action: "click", target: "button", timestamp: "2026-10-12T10:00:00.000Z", ...fields,
});

// Rows are built through the real reducer, so these tests see exactly what the page sees.
function rowFor(events) {
  const state = events.reduce((s, e) => timelineReducer(s, { type: "event", runId: 7, event: e }), initialTimeline(7));
  return state.rows;
}
const one = (fields, action = "click", target = "button") =>
  rowFor([base("step_passed", 0, { action, target, status: "PASSED", ...fields })])[0];

test("a named button reads as Click \"name\"; an unnamed one is honest about it", () => {
  assert.equal(genericTitle(one({ element_label: "Sign In", element_role: "button" })), 'Click "Sign In"');
  assert.equal(genericTitle(one({ element_role: "button" })), "Click button");        // no guessing a business meaning
  assert.equal(genericTitle(one({ element_role: "link" }, "click", "a")), "Click link");
  assert.equal(genericTitle(one({}, "click", "div")), "Click element");
  assert.equal(genericTitle(one({ element_role: "button", locator_by: "id", locator_value: "go" })), "Click button (#go)");
});

test("typing is described by the field's name, never its value", () => {
  assert.equal(genericTitle(one({ element_label: "Email address", element_role: "textbox" }, "type", "input")), "Enter email address");
  assert.equal(genericTitle(one({ element_hint: "Enter your email" }, "type", "input")), "Type into \"Enter your email\" field");
  assert.equal(genericTitle(one({ element_label: "SSN" }, "type", "input")), "Enter SSN");           // acronym kept
  assert.equal(genericTitle(one({ sensitive: true }, "type", "input")), "Enter sensitive value");
  assert.equal(genericTitle(one({ locator_by: "css selector", locator_value: "input.email" }, "type", "input")), "Type into field (input.email)");
});

test("clear, open, refresh, back, forward, resize and action chains", () => {
  assert.equal(genericTitle(one({ element_label: "Search" }, "clear", "input")), "Clear search");
  assert.equal(genericTitle(one({ page: "example.test/login" }, "open", "")), "Open example.test/login");
  assert.equal(genericTitle(one({}, "open", "")), "Open page");
  assert.equal(genericTitle(one({}, "refresh", "")), "Refresh page");
  assert.equal(genericTitle(one({}, "back", "")), "Go back");
  assert.equal(genericTitle(one({}, "forward", "")), "Go forward");
  assert.equal(genericTitle(one({ window_size: "1366x768" }, "setWindowSize", "")), "Resize window to 1366×768");
  assert.equal(genericTitle(one({}, "actionChains", "")), "Perform mouse or keyboard actions");
});

test("waits, failed assertions and element-not-found", () => {
  const wait = one({ wait_condition: "visibility_of_element_located", wait_timeout: 10, locator_by: "id", locator_value: "go" }, "wait", "");
  assert.equal(genericTitle(wait), "Wait for element to be visible (#go)");
  assert.match(describeStep(wait).subtitle, /up to 10 s/);
  assert.equal(genericTitle(one({ wait_condition: "custom" }, "wait", "")), "Wait for a condition");
  assert.equal(genericTitle(one({ wait_until_not: true }, "wait", "")), "Wait until the condition is no longer met");
  assert.equal(genericTitle(one({ source_line: 27 }, "assert", "")), "Assertion failed (line 27)");
  assert.equal(genericTitle(one({ locator_by: "id", locator_value: "missing" }, "find", "")), "Element not found (#missing)");
});

test("an author's description is the title; the generic sentence moves to the subtitle", () => {
  const row = one({ description: "Select username field", element_label: "Username", element_role: "textbox" }, "click", "input");
  const described = describeStep(row);
  assert.equal(described.title, "Select username field");
  assert.equal(described.subtitle, 'Click "Username"');
  assert.equal(describeStep(one({ element_label: "Sign In" })).subtitle, "");
});

test("unknown actions get a neutral fallback", () => {
  assert.equal(genericTitle(one({}, "select", "")), "Select");
  assert.equal(genericTitle(one({}, "", "")), "Step");
  assert.equal(kindOf("sendKeys"), "type");
});

test("technical details: action, element, locator, and a typed value that is always hidden", () => {
  const typed = describeStep(one({ element_label: "Email address", element_role: "textbox", locator_by: "id", locator_value: "email" }, "type", "input"));
  const map = Object.fromEntries(typed.details.map((d) => [d.label, d.value]));
  assert.equal(map.Action, "send_keys");
  assert.equal(map.Element, "input, role textbox");
  assert.equal(map.Locator, "#email");
  assert.equal(map["Typed value"], "hidden");
  const password = describeStep(one({ sensitive: true, element_label: "Password" }, "type", "input"));
  assert.ok(password.details.some((d) => d.label === "Field" && /sensitive/.test(d.value)));
  assert.ok(!describeStep(one({}, "click", "button")).details.some((d) => d.label === "Typed value"));
});

test("long locators stay whole in the data (the layout wraps them) and are capped", () => {
  const long = "//div[@class='" + "a".repeat(60) + "']";
  const row = one({ locator_by: "xpath", locator_value: long });
  assert.equal(locatorText(row), `xpath ${long}`);
  const huge = one({ locator_by: "xpath", locator_value: "x".repeat(500) });
  assert.equal(huge.locatorValue.length, 80);
  const css = readFileSync(new URL("../styles/convergence_style.css", import.meta.url), "utf8");
  assert.match(css, /\.cv-timeline-details dd \{[^}]*overflow-wrap: anywhere/);
});

test("a started step keeps its facts when it passes or fails, in the same row", () => {
  seq = 0;
  const facts = { element_label: "Sign In", element_role: "button", locator_by: "id", locator_value: "login" };
  const rows = rowFor([base("step_started", 0, facts), base("step_passed", 0, { ...facts, duration: 0.18 })]);
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].status, rows[0].label, rows[0].locatorValue, rows[0].duration], ["PASSED", "Sign In", "login", 0.18]);
  seq = 0;
  const failed = rowFor([base("step_started", 0, facts), base("step_failed", 0, { error_category: "TimeoutException" })]);
  assert.equal(failed.length, 1);
  assert.equal(failed[0].status, "FAILED");
  assert.equal(failed[0].label, "Sign In");                    // facts from the start event survive an end event without them
  assert.equal(failureSummary(failed[0].errorCategory), "Timed out waiting");
  assert.equal(failureSummary("SomethingNew"), "The step failed");
});

test("a step interrupted by the end of the run is stopped, never passed, and keeps its description", () => {
  seq = 0;
  const state = [
    base("step_started", 0, { element_label: "Sign In", element_role: "button" }),
    { seq: ++seq, run_id: 7, type: "run_cancelled", status: "CANCELLED" },
  ].reduce((s, e) => timelineReducer(s, { type: "event", runId: 7, event: e }), initialTimeline(7));
  assert.equal(state.rows[0].status, "INTERRUPTED");
  assert.equal(genericTitle(state.rows[0]), 'Click "Sign In"');
});

test("historical replay: end events only, with whatever was persisted", () => {
  seq = 0;
  const rows = rowFor([
    base("step_passed", 0, { action: "open", target: "", page: "example.test/login", duration: 0.5 }),
    base("step_passed", 1, { action: "click", target: "", locator_by: "id", locator_value: "login" }),
    base("step_failed", 2, { action: "type", target: "input", error_category: "ElementNotInteractableException" }),
  ]);
  assert.deepEqual(rows.map((r) => genericTitle(r)), ["Open example.test/login", "Click element (#login)", "Type into field"]);
  assert.ok(rows.every((r) => r.description === "" && r.label === ""));    // nothing was recorded, nothing is invented
});

test("several tests keep their own steps and descriptions", () => {
  seq = 0;
  const rows = rowFor([
    base("step_passed", 0, { test_id: "t1", step_id: "t1-s0", element_label: "One" }),
    base("step_passed", 0, { test_id: "t2", step_id: "t2-s0", element_label: "Two" }),
  ]);
  assert.deepEqual(rows.map((r) => [r.testId, genericTitle(r)]), [["t1", 'Click "One"'], ["t2", 'Click "Two"']]);
});

test("invalid or hostile fields never reach a row", () => {
  seq = 0;
  const row = rowFor([base("step_passed", 0, {
    locator_by: "javascript", locator_value: "document.cookie", element_label: "x".repeat(500),
    sensitive: "yes", wait_timeout: "10", source_line: "7", value: "TYPED-PASSWORD-123",
    url: "https://x.test/?token=SECRETTOK", runtime_value: "TYPED-PASSWORD-123",
  })])[0];
  assert.equal(row.locatorBy, "");
  assert.equal(row.locatorValue, "");                        // a value without a valid strategy is dropped
  assert.equal(row.label.length, 40);
  assert.equal(row.sensitive, false);
  assert.equal(row.waitTimeout, null);
  assert.equal(row.sourceLine, null);
  const dumped = JSON.stringify(describeStep(row)) + JSON.stringify(row);
  for (const secret of ["TYPED-PASSWORD-123", "SECRETTOK", "document.cookie"]) assert.ok(!dumped.includes(secret), secret);
});

test("the timeline component uses real buttons for details and never injects HTML", () => {
  const source = readFileSync(new URL("../components/StepTimeline.jsx", import.meta.url), "utf8");
  assert.match(source, /<button\s+type="button"[\s\S]*aria-expanded=\{open\}[\s\S]*aria-controls=\{panelId\}/);
  assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(source));
  const page = readFileSync(new URL("../pages/projects/RunDetailsPage.jsx", import.meta.url), "utf8");
  assert.ok(page.includes("<LiveBrowserViewer run={run} />"));       // the live browser slot is unchanged
});
