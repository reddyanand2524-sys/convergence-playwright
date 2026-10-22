import assert from "node:assert/strict";
import { test } from "node:test";

import { BROWSER_OPTIONS } from "../constants/projectOptions.js";
import {
  browserAfterFrameworkChange,
  browserOptionsFor,
  buildCreatePayload,
  buildProjectPatch,
} from "./projectForm.js";

const base = { name: "Checkout", frequency: "5 minutes" };

test("a project created without a framework sends none (the backend defaults to Selenium)", () => {
  assert.deepEqual(buildCreatePayload({ ...base, browser: "Chrome" }), { ...base, browser: "Chrome" });
});

test("Selenium sends its framework and the browser exactly as chosen", () => {
  assert.deepEqual(buildCreatePayload({ ...base, browser: "Firefox", framework: "selenium" }), {
    ...base,
    browser: "Firefox",
    framework: "selenium",
  });
});

test("Playwright sends framework playwright and the lowercase Chrome browser, whatever was selected", () => {
  for (const browser of ["Chrome", "Firefox", "chrome"]) {
    assert.deepEqual(buildCreatePayload({ ...base, browser, framework: "playwright" }), {
      ...base,
      browser: "chrome",
      framework: "playwright",
    });
  }
});

test("Playwright offers Chrome only; Selenium keeps the existing list", () => {
  assert.deepEqual(browserOptionsFor("playwright", "Firefox"), [{ value: "chrome", label: "Chrome" }]);
  assert.deepEqual(
    browserOptionsFor("selenium").map((o) => o.value),
    BROWSER_OPTIONS
  );
  // A stored non-standard browser stays visible instead of being dropped.
  assert.equal(browserOptionsFor("selenium", "chrome")[0].value, "chrome");
});

test("switching framework keeps the browser consistent", () => {
  assert.equal(browserAfterFrameworkChange("playwright", "Firefox", null), "chrome");
  // back to Selenium: a valid choice is kept, "chrome" (Playwright's) is not a Selenium option
  assert.equal(browserAfterFrameworkChange("selenium", "Edge", null), "Edge");
  assert.equal(browserAfterFrameworkChange("selenium", "chrome", null), "Chrome");
  // an existing Selenium project gets its own browser back
  const project = { framework: "selenium", browser: "Firefox" };
  assert.equal(browserAfterFrameworkChange("selenium", "chrome", project), "Firefox");
  // a legacy project (no framework) counts as Selenium
  assert.equal(browserAfterFrameworkChange("selenium", "chrome", { browser: "Edge" }), "Edge");
  // a Playwright project has no Selenium browser to restore
  assert.equal(browserAfterFrameworkChange("selenium", "chrome", { framework: "playwright", browser: "chrome" }), "Chrome");
});

const seleniumProject = { id: 1, name: "Checkout", frequency: "5 minutes", browser: "Firefox", framework: "selenium" };
const draftOf = (project, extra = {}) => ({
  name: project.name,
  frequency: project.frequency,
  browser: project.browser,
  framework: project.framework || "selenium",
  ...extra,
});

test("an untouched form sends an empty patch", () => {
  assert.deepEqual(buildProjectPatch(seleniumProject, draftOf(seleniumProject)), {});
});

test("a legacy project without a framework is not rewritten by merely opening and saving it", () => {
  const legacy = { id: 2, name: "Old", frequency: "Daily", browser: "chrome" };
  assert.deepEqual(buildProjectPatch(legacy, draftOf(legacy)), {});
  assert.deepEqual(buildProjectPatch(legacy, draftOf(legacy, { name: " Older " })), { name: "Older" });
});

test("patch keeps its partial semantics: only changed fields", () => {
  assert.deepEqual(buildProjectPatch(seleniumProject, draftOf(seleniumProject, { frequency: "Daily" })), {
    frequency: "Daily",
  });
  assert.deepEqual(buildProjectPatch(seleniumProject, draftOf(seleniumProject, { browser: "Edge" })), {
    browser: "Edge",
  });
});

test("changing to Playwright sends framework and the Chrome browser together", () => {
  assert.deepEqual(
    buildProjectPatch(seleniumProject, draftOf(seleniumProject, { framework: "playwright", browser: "chrome" })),
    { framework: "playwright", browser: "chrome" }
  );
});

test("changing back to Selenium sends framework and the Selenium browser", () => {
  const playwright = { id: 3, name: "P", frequency: "Daily", browser: "chrome", framework: "playwright" };
  assert.deepEqual(
    buildProjectPatch(playwright, draftOf(playwright, { framework: "selenium", browser: "Chrome" })),
    { framework: "selenium", browser: "Chrome" }
  );
});

test("an unchanged Playwright project sends nothing even though its stored browser is lowercase", () => {
  const playwright = { id: 3, name: "P", frequency: "Daily", browser: "chrome", framework: "playwright" };
  assert.deepEqual(buildProjectPatch(playwright, draftOf(playwright)), {});
});
