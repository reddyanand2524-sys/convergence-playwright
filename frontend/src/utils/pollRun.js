import { abortableSleep } from "../services/runEventsApi.js";

export const POLL_INTERVAL_MS = 3000;
export const MAX_POLL_MS = 15 * 60 * 1000;

export const NOT_FOUND_MESSAGE = "Project or Run not found.";
export const FORBIDDEN_MESSAGE = "You do not have access to this run. Automatic refresh stopped.";

/*
 * The polling loop behind useRunPolling, free of React so it can be tested.
 * Chained (never overlapping) requests, the first one after intervalMs.
 *
 * Resolves with the reason it stopped:
 *   "terminal"     the fetched status is no longer active
 *   "unauthorized" 401 - authedFetch already redirects to /login; no message
 *   "not-found"    404 - onError(NOT_FOUND_MESSAGE)
 *   "forbidden"    403 - onError(FORBIDDEN_MESSAGE); retrying cannot help
 *   "timeout"      still active after maxMs
 *   "aborted"      the signal fired (unmount / run change)
 * Any other failure (network error, 5xx, ...) is transient: onError shows a
 * "retrying" message and polling continues.
 *
 * onUpdate(run) gets each fresh response; onError(message) is also called with
 * "" to clear a previous message.
 */
export async function pollRun({
  fetcher,
  onUpdate,
  onError,
  isActive,
  statusOf,
  signal,
  intervalMs = POLL_INTERVAL_MS,
  maxMs = MAX_POLL_MS,
  sleep = abortableSleep,
  now = Date.now,
}) {
  const startedAt = now();

  for (;;) {
    await sleep(intervalMs, signal);
    if (signal.aborted) return { reason: "aborted" };

    try {
      const run = await fetcher(signal);
      if (signal.aborted) return { reason: "aborted" };
      onError("");
      onUpdate(run);
      if (!isActive(statusOf(run))) return { reason: "terminal" };
    } catch (err) {
      if (signal.aborted || err?.name === "AbortError") return { reason: "aborted" };
      if (err?.status === 401) return { reason: "unauthorized" };
      if (err?.status === 404) {
        onError(NOT_FOUND_MESSAGE);
        return { reason: "not-found" };
      }
      if (err?.status === 403) {
        onError(FORBIDDEN_MESSAGE);
        return { reason: "forbidden" };
      }
      onError(
        err?.status === 0
          ? "Unable to connect to the backend. Retrying..."
          : "Could not refresh run status. Retrying..."
      );
    }

    if (now() - startedAt >= maxMs) return { reason: "timeout" };
  }
}
