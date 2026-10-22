import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_FRAMEWORK, FRAMEWORK_OPTIONS, frameworkLabel, frameworkOf, isPlaywright } from "./framework.js";

test("the options use the exact lowercase backend values", () => {
  assert.deepEqual(
    FRAMEWORK_OPTIONS.map((option) => option.value),
    ["selenium", "playwright"]
  );
  assert.equal(DEFAULT_FRAMEWORK, "selenium");
});

test("a missing framework is shown as Selenium without being invented on the object", () => {
  const legacy = { id: 1, name: "old", browser: "chrome" };
  assert.equal(frameworkOf(legacy), "selenium");
  assert.equal(frameworkLabel(legacy), "Selenium");
  assert.equal("framework" in legacy, false);

  for (const item of [null, undefined, {}, { framework: null }, { framework: "" }, { framework: "  " }, { framework: 5 }]) {
    assert.equal(frameworkOf(item), "selenium");
  }
});

test("known frameworks are labelled and detected", () => {
  assert.equal(frameworkLabel({ framework: "selenium" }), "Selenium");
  assert.equal(frameworkLabel({ framework: "playwright" }), "Playwright");
  assert.equal(frameworkLabel({ framework: "Playwright" }), "Playwright");
  assert.equal(isPlaywright({ framework: "playwright" }), true);
  assert.equal(isPlaywright({ framework: "selenium" }), false);
  assert.equal(isPlaywright({}), false);
});

test("an unrecognised framework is shown as sent, not relabelled Selenium", () => {
  assert.equal(frameworkOf({ framework: "cypress" }), "cypress");
  assert.equal(frameworkLabel({ framework: "cypress" }), "Cypress");
  assert.equal(isPlaywright({ framework: "cypress" }), false);
});
