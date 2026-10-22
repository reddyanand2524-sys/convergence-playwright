/*
 * Execution framework of a project or run. The backend sends the lowercase values
 * "selenium" | "playwright"; responses from before the field existed carry none.
 * A missing value is shown as Selenium (presentation only - nothing here is ever
 * written back to the backend). An unrecognised value is shown as sent.
 */
export const DEFAULT_FRAMEWORK = "selenium";

export const FRAMEWORK_OPTIONS = [
  { value: "selenium", label: "Selenium" },
  { value: "playwright", label: "Playwright" },
];

/** Lowercase framework of a project / run / history row; "selenium" when absent. */
export function frameworkOf(item) {
  const value = typeof item?.framework === "string" ? item.framework.trim().toLowerCase() : "";
  return value || DEFAULT_FRAMEWORK;
}

export function frameworkLabel(item) {
  const value = frameworkOf(item);
  const known = FRAMEWORK_OPTIONS.find((option) => option.value === value);
  return known ? known.label : value.charAt(0).toUpperCase() + value.slice(1);
}

export const isPlaywright = (item) => frameworkOf(item) === "playwright";
