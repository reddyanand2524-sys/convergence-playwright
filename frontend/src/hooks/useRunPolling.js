import { useEffect, useRef, useState } from "react";

import { getRun } from "../services/runsApi.js";

const DEFAULT_ACTIVE = ["RUNNING"];
const DEFAULT_STATUS_OF = (result) => result.status;

const POLL_INTERVAL_MS = 3000;
const MAX_POLL_MS = 15 * 60 * 1000;

/*
 * Refreshes ONE run from GET /projects/{pid}/runs/{rid} while it is RUNNING
 * (or, for the results page, the results endpoint while QUEUED/RUNNING).
 * Single loop per (projectId, runId, status): scheduled with chained
 * timeouts (no overlapping requests), and torn down on terminal status,
 * unmount, project/run change, 404, or after MAX_POLL_MS.
 *
 * onUpdate(run) receives each fresh backend RunResponse.
 * Returns { refreshError, stopped }.
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

    let cancelled = false;
    let timer;
    const controller = new AbortController();
    const startedAt = Date.now();

    async function tick() {
      try {
        const run = await fetcher(projectId, runId, controller.signal);
        if (cancelled) return;
        setRefreshError("");
        onUpdateRef.current(run);
        if (!activeStatuses.includes(statusOf(run))) return;
      } catch (err) {
        if (cancelled || err?.name === "AbortError") return;
        if (err?.status === 401) return; // session redirect handles it
        if (err?.status === 404) {
          setRefreshError("Project or Run not found.");
          return;
        }
        setRefreshError(
          err?.status === 0
            ? "Unable to connect to the backend. Retrying..."
            : "Could not refresh run status. Retrying..."
        );
      }

      if (Date.now() - startedAt >= MAX_POLL_MS) {
        setStopped(true);
        return;
      }

      timer = setTimeout(tick, POLL_INTERVAL_MS);
    }

    timer = setTimeout(tick, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [projectId, runId, status]);

  return { refreshError, stopped };
}
