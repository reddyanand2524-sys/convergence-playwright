import { isPlaywright } from "./framework.js";

/*
 * Browser choices of the schedule form. Run browsers are lower-case in the API.
 *   selenium    Chrome / Firefox / Edge, exactly as before
 *   playwright  Chrome only (the project itself is Chrome-only)
 * The decision uses the project's framework value, never the browser name. Whether the
 * backend allows a schedule for a Playwright project is the server's call: nothing here
 * claims support, and its 403 / 409 / 422 messages are shown by the form.
 */
export const SCHEDULE_BROWSERS = [
  { value: "chrome", label: "Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "edge", label: "Edge" },
];

const PLAYWRIGHT_SCHEDULE_BROWSERS = [{ value: "chrome", label: "Chrome" }];

/**
 * Options for the Browser select. A stored value that is not an allowed choice (for example a
 * Firefox schedule on a project that has since become Playwright) stays visible, so opening the
 * form never changes it; it is only replaced if the user picks another option and saves.
 */
export function scheduleBrowserOptions(project, current) {
  const allowed = isPlaywright(project) ? PLAYWRIGHT_SCHEDULE_BROWSERS : SCHEDULE_BROWSERS;
  return allowed.some((browser) => browser.value === current)
    ? allowed
    : [{ value: current, label: current }, ...allowed];
}

/** Browser preselected for a NEW schedule. */
export function defaultScheduleBrowser(project) {
  if (isPlaywright(project)) return "chrome";
  const projectBrowser = String(project?.browser).toLowerCase();
  return SCHEDULE_BROWSERS.some((browser) => browser.value === projectBrowser) ? projectBrowser : "chrome";
}

/** The Playwright hint depends on the project's framework only. */
export const showsPlaywrightScheduleHint = (project) => isPlaywright(project);
