import { useEffect, useMemo, useReducer, useState } from "react";

import { followRunEvents } from "../services/runEventsApi.js";
import { describeError } from "../services/authedFetch.js";
import { initialTimeline, timelineReducer } from "../utils/runTimeline.js";

/*
 * Live step timeline of ONE run, from the backend's event stream.
 *
 * - Starts when the run is RUNNING (status comes from the page's existing polling),
 *   and keeps going until the stream delivers the run's terminal event - even if the
 *   poll sees COMPLETED/FAILED/CANCELLED first, so the last steps are not lost.
 * - A run that is already finished when the page opens does not open a stream: its
 *   outcome and steps come from the existing results page / API.
 * - Reconnects resume from the last processed seq; events are deduplicated by seq
 *   and merged into one row per public step_id (see utils/runTimeline.js).
 * - Everything is scoped to (projectId, runId): switching runs aborts the stream,
 *   clears the history, and drops anything still in flight for the old run.
 *
 * The hook never touches the Run API, the poll, or the live-browser viewer.
 */
export default function useRunEvents(projectId, runId, status) {
  const key = String(runId);
  const [timeline, dispatch] = useReducer(timelineReducer, key, initialTimeline);
  const [connection, setConnection] = useState({ runId: key, state: "idle", error: "" });
  const [watching, setWatching] = useState(null); // run id this page has seen RUNNING

  const active = watching === key;

  useEffect(() => {
    dispatch({ type: "reset", runId: key });
    setConnection({ runId: key, state: "idle", error: "" });
    setWatching(null);
  }, [key]);

  useEffect(() => {
    if (status === "RUNNING") setWatching(key);
  }, [status, key]);

  useEffect(() => {
    if (!projectId || !runId || !active) return undefined;

    let cancelled = false;
    const controller = new AbortController();
    const set = (state, error = "") => {
      if (!cancelled) setConnection({ runId: key, state, error });
    };

    followRunEvents(projectId, runId, {
      signal: controller.signal,
      onConnection: (state) => set(state),
      onEvent: (event) => {
        if (!cancelled) dispatch({ type: "event", runId: key, event });
      },
      onGap: () => {
        if (!cancelled) dispatch({ type: "gap", runId: key });
      },
    }).then((outcome) => {
      if (cancelled) return;
      if (outcome.reason === "rejected") set("error", describeError(outcome.error));
      else if (outcome.reason === "gave-up") {
        set("error", "Live step updates are unavailable. The run status still refreshes.");
      }
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [projectId, runId, key, active]);

  // State that belongs to a run that is no longer on screen is never exposed.
  const current = timeline.runId === key ? timeline : initialTimeline(key);
  const link = connection.runId === key ? connection : { state: "idle", error: "" };

  return useMemo(
    () => ({ timeline: current, connection: link.state, error: link.error, engaged: active }),
    [current, link.state, link.error, active]
  );
}
