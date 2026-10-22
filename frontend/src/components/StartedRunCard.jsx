import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import RunStatusBadge from "./RunStatusBadge.jsx";
import RunStateMessage from "./RunStateMessage.jsx";
import CancelRunControl from "./CancelRunControl.jsx";
import useRunPolling from "../hooks/useRunPolling.js";
import { formatDateTime } from "../utils/format.js";
import { ACTIVE_STATUSES, isActive } from "../utils/runStatus.js";
import { frameworkLabel } from "../utils/framework.js";

/*
 * One run started from the Run tab. Each card owns its run's state and its own
 * polling loop (scoped to that run id), so several runs can execute and be
 * followed side by side without sharing state. onSettled() fires when this run
 * leaves RUNNING so the parent can refresh the recent-runs list.
 */
export default function StartedRunCard({ projectId, initialRun, onSettled }) {
  const [run, setRun] = useState(initialRun);

  const apply = useCallback(
    (fresh) => {
      if (String(fresh?.id) !== String(initialRun.id)) return; // never take another run's data
      setRun(fresh);
      if (!isActive(fresh.status)) onSettled?.(fresh);
    },
    [initialRun.id, onSettled]
  );

  const polling = useRunPolling(projectId, run.id, run.status, apply, { activeStatuses: ACTIVE_STATUSES });

  return (
    <section className="home-run-created" aria-label={`Started run ${run.id}`}>
      <h4>Run #{run.id}</h4>
      <dl className="home-config-card">
        <div><dt>Run ID</dt><dd>#{run.id}</dd></div>
        <div><dt>Status</dt><dd><RunStatusBadge status={run.status} /></dd></div>
        <div><dt>Framework</dt><dd>{frameworkLabel(run)}</dd></div>
        <div><dt>Browser</dt><dd>{run.browser}</dd></div>
        <div><dt>Started</dt><dd>{formatDateTime(run.started_at, "Not started")}</dd></div>
      </dl>

      <div className="home-run-actions">
        <CancelRunControl projectId={projectId} run={run} onUpdate={apply} />
        <Link className="home-secondary-button" to={`/projects/${projectId}/runs/${run.id}`}>
          View run details
        </Link>
      </div>

      <RunStateMessage run={run} refreshError={polling.refreshError} stopped={polling.stopped} />
    </section>
  );
}
