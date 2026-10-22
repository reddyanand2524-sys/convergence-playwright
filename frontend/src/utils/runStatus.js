/*
 * Central run-status predicates. Statuses are the ones the backend emits today:
 *   QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
 * Matching is exact (the backend sends upper-case); anything else - unknown,
 * lower-case, null, undefined - is never active, terminal, reportable or
 * retryable, so a new or malformed status cannot be mistaken for a finished run.
 */
export const ACTIVE_STATUSES = ["QUEUED", "RUNNING"];
export const TERMINAL_STATUSES = ["COMPLETED", "FAILED", "CANCELLED"];

// Runs that produce results and reports. CANCELLED is terminal but has neither.
const REPORT_STATUSES = ["COMPLETED", "FAILED"];

/** Still waiting for, or in, execution. */
export const isActive = (status) => ACTIVE_STATUSES.includes(status);

/** Finished for good: no further status changes are expected. */
export const isTerminal = (status) => TERMINAL_STATUSES.includes(status);

/** Results / report endpoints are available (the backend answers 409 otherwise). */
export const hasReport = (status) => REPORT_STATUSES.includes(status);

/** Retry / Rerun is offered; mirrors the backend's COMPLETED/FAILED rule. */
export const isRetryable = (status) => REPORT_STATUSES.includes(status);

/**
 * After a refused start / cancel / retry (409): did the run really change underneath us?
 * If it did, the refusal is "the state moved on" and the fresh run is shown. If it did not,
 * the 409 meant something else (e.g. the runtime is unavailable) and the backend's own
 * message must be shown instead.
 */
export const runStateChanged = (before, after) =>
  Boolean(before && after) &&
  (before.status !== after.status || Boolean(before.cancel_requested) !== Boolean(after.cancel_requested));
