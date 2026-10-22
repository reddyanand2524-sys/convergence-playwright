import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SCHEDULE_BROWSERS,
  defaultScheduleBrowser,
  scheduleBrowserOptions,
  showsPlaywrightScheduleHint,
} from "./scheduleBrowser.js";

const playwrightProject = { id: 1, framework: "playwright", browser: "chrome" };
const seleniumProject = { id: 2, framework: "selenium", browser: "Firefox" };
const legacyProject = { id: 3, browser: "Edge" }; // no framework field: treated as Selenium

const values = (options) => options.map((option) => option.value);

test("a Playwright schedule offers Chrome only", () => {
  assert.deepEqual(values(scheduleBrowserOptions(playwrightProject, "chrome")), ["chrome"]);
  assert.equal(scheduleBrowserOptions(playwrightProject, "chrome")[0].label, "Chrome");
});

test("Selenium (and legacy) projects keep the existing three browsers", () => {
  assert.deepEqual(values(scheduleBrowserOptions(seleniumProject, "firefox")), ["chrome", "firefox", "edge"]);
  assert.deepEqual(scheduleBrowserOptions(seleniumProject, "chrome"), SCHEDULE_BROWSERS);
  assert.deepEqual(scheduleBrowserOptions(legacyProject, "edge"), SCHEDULE_BROWSERS);
  assert.deepEqual(scheduleBrowserOptions(null, "chrome"), SCHEDULE_BROWSERS);
});

test("a Selenium schedule with an unknown stored browser still shows it first (unchanged behavior)", () => {
  assert.deepEqual(values(scheduleBrowserOptions(seleniumProject, "safari")), ["safari", "chrome", "firefox", "edge"]);
});

test("a legacy Firefox or Edge schedule on a Playwright project stays visible and is not replaced", () => {
  for (const stored of ["firefox", "edge"]) {
    const options = scheduleBrowserOptions(playwrightProject, stored);
    assert.deepEqual(values(options), [stored, "chrome"]);
    assert.equal(options[0].label, stored);
    // the select's value is the stored one, so nothing changes unless the user picks Chrome
    assert.equal(options.some((option) => option.value === stored), true);
  }
});

test("a new Playwright schedule defaults to Chrome, whatever the project browser says", () => {
  assert.equal(defaultScheduleBrowser(playwrightProject), "chrome");
  assert.equal(defaultScheduleBrowser({ framework: "playwright", browser: "firefox" }), "chrome");
  assert.equal(defaultScheduleBrowser({ framework: "playwright" }), "chrome");
});

test("a new Selenium schedule defaults exactly as before", () => {
  assert.equal(defaultScheduleBrowser(seleniumProject), "firefox"); // "Firefox" lower-cased
  assert.equal(defaultScheduleBrowser(legacyProject), "edge");
  assert.equal(defaultScheduleBrowser({ framework: "selenium", browser: "Chrome" }), "chrome");
  assert.equal(defaultScheduleBrowser({ framework: "selenium", browser: "safari" }), "chrome");
  assert.equal(defaultScheduleBrowser(undefined), "chrome");
});

test("the hint follows the project's framework, not the browser", () => {
  assert.equal(showsPlaywrightScheduleHint({ framework: "playwright", browser: "firefox" }), true);
  assert.equal(showsPlaywrightScheduleHint({ framework: "playwright", browser: "chrome" }), true);
  assert.equal(showsPlaywrightScheduleHint({ framework: "selenium", browser: "chrome" }), false);
  assert.equal(showsPlaywrightScheduleHint(legacyProject), false);
  assert.equal(showsPlaywrightScheduleHint(null), false);
});
