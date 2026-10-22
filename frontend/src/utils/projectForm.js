import { BROWSER_OPTIONS } from "../constants/projectOptions.js";
import { frameworkOf } from "./framework.js";

/*
 * Framework / browser rules shared by Create Project and Project Configuration.
 *   selenium    the existing browser list (Firefox / Edge / Chrome), sent as before
 *   playwright  Chrome only, sent as the lowercase value "chrome"
 * Whether the backend can actually run Playwright is only known from its API
 * responses (403 / 409 / 422 with a "detail"), never assumed here.
 */
export const PLAYWRIGHT_BROWSER = "chrome";
const SELENIUM_DEFAULT_BROWSER = "Chrome";

/** [{ value, label }] for the Browser select. `current` is kept visible if it is not standard. */
export function browserOptionsFor(framework, current) {
  if (framework === "playwright") return [{ value: PLAYWRIGHT_BROWSER, label: "Chrome" }];

  const values = current && !BROWSER_OPTIONS.includes(current) ? [current, ...BROWSER_OPTIONS] : BROWSER_OPTIONS;
  return values.map((value) => ({ value, label: value }));
}

/**
 * Browser to select after the framework changes. Playwright -> "chrome". Selenium keeps a
 * valid current choice, else restores the project's own Selenium browser, else the default.
 */
export function browserAfterFrameworkChange(nextFramework, currentBrowser, project) {
  if (nextFramework === "playwright") return PLAYWRIGHT_BROWSER;
  if (BROWSER_OPTIONS.includes(currentBrowser)) return currentBrowser;
  if (project && frameworkOf(project) === "selenium" && project.browser) return project.browser;
  return SELENIUM_DEFAULT_BROWSER;
}

/** Body for POST /projects. `framework` is left out when not given (the backend defaults to Selenium). */
export function buildCreatePayload({ name, frequency, browser, framework }) {
  const payload = {
    name,
    frequency,
    browser: framework === "playwright" ? PLAYWRIGHT_BROWSER : browser,
  };
  if (framework !== undefined) payload.framework = framework;
  return payload;
}

/**
 * Body for PATCH /projects/{id}: only fields that changed. A framework change also sends
 * the browser that goes with it, so the backend never has to guess.
 */
export function buildProjectPatch(project, draft) {
  const payload = {};

  ["name", "frequency"].forEach((field) => {
    const value = draft[field].trim();
    if (value !== project[field]) payload[field] = value;
  });

  const framework = draft.framework;
  const browser = framework === "playwright" ? PLAYWRIGHT_BROWSER : draft.browser.trim();

  if (framework !== frameworkOf(project)) {
    payload.framework = framework;
    payload.browser = browser;
  } else if (browser !== project.browser) {
    payload.browser = browser;
  }

  return payload;
}
