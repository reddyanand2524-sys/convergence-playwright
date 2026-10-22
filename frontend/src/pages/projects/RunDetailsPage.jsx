import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import RunStatusBadge from "../../components/RunStatusBadge.jsx";
import RunStateMessage from "../../components/RunStateMessage.jsx";
import CancelRunControl from "../../components/CancelRunControl.jsx";
import RetryRunControl from "../../components/RetryRunControl.jsx";
import ReportActions from "../../components/ReportActions.jsx";
import LiveBrowserViewer from "../../components/LiveBrowserViewer.jsx";
import StartRunControl from "../../components/StartRunControl.jsx";
import StepTimeline from "../../components/StepTimeline.jsx";
import useRunPolling from "../../hooks/useRunPolling.js";
import useRunEvents from "../../hooks/useRunEvents.js";
import { getRun } from "../../services/runsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { formatDateTime, formatDuration } from "../../utils/format.js";
import { ACTIVE_STATUSES, hasReport } from "../../utils/runStatus.js";
import { frameworkLabel, frameworkOf, isPlaywright } from "../../utils/framework.js";

export default function RunDetailsPage() {
  const { project } = useOutletContext();
  const { runId } = useParams();

  // State is tagged with the run it belongs to, so a page that has just
  // switched to another run can never show (or act on) the previous run.
  const [state, setState] = useState({ runId, status: "loading", run: null, error: "" });
  const currentRunId = useRef(runId);
  currentRunId.current = runId;

  const load = useCallback(
    async (signal) => {
      setState({ runId, status: "loading", run: null, error: "" });
      try {
        const run = await getRun(project.id, runId, signal);
        setState({ runId, status: "ready", run, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({
          runId,
          status: err?.status === 404 ? "notfound" : "error",
          run: null,
          error: describeError(err),
        });
      }
    },
    [project.id, runId]
  );

  // Any response (poll, start, cancel, 409 refresh) is only applied if it is
  // for the run currently on screen - e.g. a slow cancel reply for run A must
  // not overwrite run B after navigating A -> B.
  const applyRun = useCallback((fresh) => {
    if (String(fresh?.id) !== String(currentRunId.current)) return;
    setState({ runId: currentRunId.current, status: "ready", run: fresh, error: "" });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const stale = state.runId !== runId; // first render after the route changed
  const view = stale ? { status: "loading", run: null, error: "" } : state;
  const { run } = view;
  const retriedFrom = useLocation().state?.retriedFrom;

  const polling = useRunPolling(
    project.id,
    runId,
    view.status === "ready" ? run.status : null,
    applyRun,
    { activeStatuses: ACTIVE_STATUSES }
  );

  // Live step events for the run on screen; independent of the poll above and of the viewer.
  const events = useRunEvents(project.id, runId, view.status === "ready" ? run.status : null);

  return (
    <div className="home-runs">
      <Link className="home-back-link" to={`/projects/${project.id}/run`}>
        <ArrowLeft size={16} aria-hidden="true" />
        Runs
      </Link>

      {view.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading run..." />
        </div>
      )}

      {view.status === "notfound" && (
        <div className="home-empty-state" role="alert">
          <h3>Run not found</h3>
          <p>Project or Run not found.</p>
        </div>
      )}

      {view.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load run</h3>
          <p>{view.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {view.status === "ready" && (
        <>
          <div className="home-section-heading">
            <div>
              <span className="home-eyebrow">Run</span>
              <h3>Run #{run.id}</h3>
            </div>
            <RunStatusBadge status={run.status} />
          </div>

          <dl className="home-config-card">
            <div><dt>Run ID</dt><dd>{run.id}</dd></div>
            <div><dt>Project ID</dt><dd>{run.project_id}</dd></div>
            <div><dt>Framework</dt><dd>{frameworkLabel(run)}</dd></div>
            <div><dt>Status</dt><dd>{run.status}</dd></div>
            <div><dt>Browser (captured at creation)</dt><dd>{run.browser}</dd></div>
            <div><dt>Created</dt><dd>{formatDateTime(run.created_at)}</dd></div>
            <div><dt>Started</dt><dd>{formatDateTime(run.started_at, "Not started")}</dd></div>
            <div><dt>Completed</dt><dd>{formatDateTime(run.completed_at, "Not completed")}</dd></div>
            <div><dt>Duration</dt><dd>{formatDuration(run.started_at, run.completed_at) || "Not available"}</dd></div>
            <div><dt>Cancel requested</dt><dd>{run.cancel_requested ? "Yes" : "No"}</dd></div>
          </dl>

          {/* One slot, in the position the old loader had, so LiveBrowserViewer below keeps its place in the tree. */}
          {run.status === "RUNNING" || events.engaged ? (
            events.connection === "error" &&
            (isPlaywright(run) ? events.timeline.tests.length === 0 : events.timeline.rows.length === 0) ? (
              <LoadingState type="converge" label="Run in progress — results appear when it completes" className="cv-run-loader" />
            ) : (
              <StepTimeline
                timeline={events.timeline}
                connection={events.connection}
                error={events.error}
                runStatus={run.status}
                framework={frameworkOf(run)}
                resultsHref={run.status === "FAILED" ? `/projects/${project.id}/runs/${run.id}/results` : undefined}
              />
            )
          ) : null}

          {/* The viewer is the Selenium noVNC display; Playwright runs do not use it. */}
          {!isPlaywright(run) && <LiveBrowserViewer run={run} />}

          {retriedFrom && (
            <p className="home-notice is-success" role="status">
              New run created from <Link to={`/projects/${project.id}/runs/${retriedFrom}`}>Run #{retriedFrom}</Link>.
              {run.status === "QUEUED" && " It has not been started yet."}
            </p>
          )}

          <div className="home-run-actions" key={run.id}>
            {hasReport(run.status) && (
              <Link className="home-primary-button" to={`/projects/${project.id}/runs/${run.id}/results`}>
                View Results
              </Link>
            )}

            <StartRunControl projectId={project.id} run={run} onUpdate={applyRun} />
            <CancelRunControl projectId={project.id} run={run} onUpdate={applyRun} />
            <RetryRunControl projectId={project.id} run={run} onUpdate={applyRun} />
            <ReportActions key={run.id} projectId={project.id} run={run} />
          </div>

          <RunStateMessage run={run} refreshError={polling.refreshError} stopped={polling.stopped} />
        </>
      )}
    </div>
  );
}
