import { Activity, CheckCircle2, Clock, History, Layers, XCircle } from "lucide-react";
import { Link } from "react-router-dom";

import MetricCard from "./MetricCard.jsx";
import RunStatusBadge from "./RunStatusBadge.jsx";
import LoadingState from "./brand/LoadingState.jsx";
import useWorkspaceMetrics from "../hooks/useWorkspaceMetrics.js";
import { formatDateTime, formatSeconds } from "../utils/format.js";

const percent = (value) => (value == null ? "—" : `${Math.round(value * 10) / 10}%`);

export default function DashboardMetrics({ projects }) {
  const metrics = useWorkspaceMetrics(projects);

  if (metrics.status === "idle") return null;

  return (
    <section className="home-metrics" aria-label="Run metrics">
      {metrics.status === "loading" && !metrics.data && (
        <LoadingState type="flow" inline label="Loading run metrics…" />
      )}

      {metrics.status === "error" && (
        <p className="home-notice is-error" role="alert">
          Could not load run metrics: {metrics.error}
        </p>
      )}

      {metrics.data && <MetricGrid data={metrics.data} />}
    </section>
  );
}

function MetricGrid({ data }) {
  const none = data.totalRuns === 0;
  const scope = data.truncated ? "Latest 100 runs per project" : "All runs";
  const rateTone =
    data.passRate == null ? "neutral" : data.passRate >= 90 ? "success" : data.passRate >= 70 ? "warning" : "critical";

  return (
    <>
      <div className="cv-metric-grid">
        <MetricCard
          label="Test pass rate"
          icon={CheckCircle2}
          tone={rateTone}
          value={percent(data.passRate)}
          hint={data.tests ? `${data.tests} test executions in finished runs` : "No finished runs yet"}
          mono
        />
        <MetricCard
          label="Total runs"
          icon={History}
          value={data.totalRuns}
          hint={none ? "No runs yet" : `${data.completed} completed · ${data.failed} failed`}
          mono
        />
        <MetricCard
          label="Active runs"
          icon={Activity}
          tone={data.running ? "running" : "neutral"}
          value={data.running + data.queued}
          hint={`${data.running} running · ${data.queued} queued`}
          mono
        />
        <MetricCard
          label="Failed runs"
          icon={XCircle}
          tone={data.failed ? "critical" : "neutral"}
          value={data.failed}
          hint={scope}
          mono
        />
        <MetricCard
          label="Avg duration"
          icon={Clock}
          value={formatSeconds(data.avgDuration) || "—"}
          hint="Per run with a recorded duration"
          mono
        />
        <MetricCard label="Projects" icon={Layers} value={data.projects} hint="In this workspace" mono />
      </div>

      {data.last && (
        <p className="cv-metric-last">
          <span>Last run</span>
          <Link to={`/projects/${data.last.project_id}/runs/${data.last.id}`}>
            {data.last.projectName} · #{data.last.id}
          </Link>
          <RunStatusBadge status={data.last.status} />
          <time className="mono">{formatDateTime(data.last.created_at)}</time>
        </p>
      )}
    </>
  );
}
