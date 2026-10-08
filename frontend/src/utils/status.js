/*
 * Central status mapping. Values come from the backend (verified against its
 * OpenAPI schema):
 *   run.status    QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
 *   test.status   PASSED | FAILED | SKIPPED | ERROR (free string; steps: PASS | FAIL)
 * The backend never emits FLAKY, so it is only mapped, never derived.
 */
export const RUN_STATUS = ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"];

export function normalizeRunStatus(status) {
  const value = String(status || "").toUpperCase();
  return RUN_STATUS.includes(value) ? value : "UNKNOWN";
}

const TEST_KIND = {
  PASSED: "pass",
  PASS: "pass",
  FAILED: "fail",
  FAIL: "fail",
  ERROR: "error",
  SKIPPED: "skip",
  SKIP: "skip",
  FLAKY: "flaky",
};

// -> "pass" | "fail" | "error" | "skip" | "flaky" | "neutral"
export function normalizeTestStatus(status) {
  return TEST_KIND[String(status || "").toUpperCase()] || "neutral";
}
