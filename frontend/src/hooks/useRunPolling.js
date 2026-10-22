import { useEffect, useRef, useState } from "react";

import { getRun } from "../services/runsApi.js";
import { pollRun } from "../utils/pollRun.js";

const DEFAULT_ACTIVE = ["RUNNING"];
const DEFAULT_STATUS_OF = (result) => result.status;

/*
 * Refreshes ONE run from GET /projects/{pid}/runs/{rid} while it is RUNNING
 * (or, for the results page, the results endpoint while QUEUED/RUNNING).
 * Single loop per (projectId, runId, status): utils/pollRun.js holds the loop
 * and its stop conditions (terminal status, 401, 403, 404, MAX_POLL_MS); the
 * effect tears it down on unmount and on project/run change.
 *
 * onUpdate(run) receives each fresh backend RunResponse.
 * Returns { refreshError, stopped }. refreshError is also set for the final
 * 403/404 (polling has stopped); stopped means the time limit was reached.
 */
export default function useRunPolling(
  projectId,
  runId,
  status,
  onUpdate,
  {
    fetcher = getRun,
    activeStatuses = DEFAULT_ACTIVE,
    statusOf = DEFAULT_STATUS_OF,
  } = {}
) {
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  const [refreshError, setRefreshError] = useState("");
  const [stopped, setStopped] = useState(false);

  useEffect(() => {
    setRefreshError("");
    setStopped(false);

    if (!projectId || !runId || !activeStatuses.includes(status)) return undefined;

    const controller = new AbortController();
    const { signal } = controller;

    pollRun({
      fetcher: (pollSignal) => fetcher(projectId, runId, pollSignal),
      onUpdate: (run) => {
        if (!signal.aborted) onUpdateRef.current(run);
      },
      onError: (message) => {
        if (!signal.aborted) setRefreshError(message);
      },
      isActive: (value) => activeStatuses.includes(value),
      statusOf,
      signal,
    }).then((outcome) => {
      if (!signal.aborted && outcome.reason === "timeout") setStopped(true);
    });

    return () => controller.abort();
  }, [projectId, runId, status]);

  return { refreshError, stopped };
}
