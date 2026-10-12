import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import RunStatusBadge from "../../components/RunStatusBadge.jsx";
import ReportActions from "../../components/ReportActions.jsx";
import RunResultsSummary from "../../components/results/RunResultsSummary.jsx";
import TestResultCard from "../../components/results/TestResultCard.jsx";
import useRunPolling from "../../hooks/useRunPolling.js";
import { getRunResults } from "../../services/runsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { formatDuration, formatSeconds } from "../../utils/format.js";

const ACTIVE = ["QUEUED", "RUNNING"];
const statusOf = (results) => results.run.status;

const WAITING_MESSAGE = {
  QUEUED: "Run is queued. Results will appear after execution starts.",
  RUNNING: "Run is currently executing. Results will appear when execution completes.",
  CANCELLED: "Run was cancelled. No execution results are available.",
};

/*
 * /projects/:projectId/runs/:runId/results
 * GET .../results returns summary/tests = null until the run is COMPLETED or
 * FAILED. While QUEUED/RUNNING the shared polling hook refreshes it.
 */
export default function RunResultsPage() {
  const { project } = useOutletContext();
  const { runId } = useParams();

  // status: "loading" | "ready" | "notfound" | "error"
  const [state, setState] = useState({ runId, status: "loading", data: null, error: "" });

  const load = useCallback(
    async (signal) => {
      setState({ runId, status: "loading", data: null, error: "" });
      try {
        const data = await getRunResults(project.id, runId, signal);
        setState({ runId, status: "ready", data, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({
          runId,
          status: err?.status === 404 ? "notfound" : "error",
          data: null,
          error: describeError(err),
        });
      }
    },
    [project.id, runId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // Results are tagged with the run they belong to: after switching runs the
  // previous run's results are never shown, and a late reply is dropped.
  const currentRunId = useRef(runId);
  currentRunId.current = runId;
  const applyResults = useCallback((fresh) => {
    if (String(fresh?.run?.id) !== String(currentRunId.current)) return;
    setState({ runId: currentRunId.current, status: "ready", data: fresh, error: "" });
  }, []);

  const view = state.runId !== runId ? { status: "loading", data: null, error: "" } : state;
  const { data } = view;

  const polling = useRunPolling(
    project.id,
    runId,
    view.status === "ready" ? data.run.status : null,
    applyResults,
    { fetcher: getRunResults, activeStatuses: ACTIVE, statusOf }
  );

  const runPath = `/projects/${project.id}/runs/${runId}`;

  return (
    <div className="home-runs">
      <Link className="home-back-link" to={runPath}>
        <ArrowLeft size={16} aria-hidden="true" />
        Back to Run
      </Link>

      {view.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="converge" label="Loading results…" />
        </div>
      )}

      {view.status === "notfound" && (
        <div className="home-empty-state" role="alert">
          <h3>Run not found</h3>
          <p>This run or its results do not exist, or you do not have access to them.</p>
          <Link className="home-secondary-button" to={`/projects/${project.id}/run`}>
            Back to Runs
          </Link>
        </div>
      )}

      {view.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load results</h3>
          <p>{view.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {view.status === "ready" && <ResultsBody data={data} projectId={project.id} polling={polling} />}
    </div>
  );
}

// Why a finished run has no test results (from pytest's real exit code and output; never a result).
function RunIssueNotice({ issue }) {
  return (
    <div className="home-notice is-error cv-run-issue" role="alert">
      <h4>{issue.title}</h4>
      <p>{issue.detail}</p>
      {issue.hints?.length > 0 && (
        <ul>
          {issue.hints.map((hint) => <li key={hint}>{hint}</li>)}
        </ul>
      )}
      {issue.exit_code != null && <p className="cv-run-issue-code">pytest exit code: {issue.exit_code}</p>}
      {issue.output_tail && (
        <details>
          <summary>Output</summary>
          <pre>{issue.output_tail}</pre>
        </details>
      )}
    </div>
  );
}

function ResultsBody({ data, projectId, polling }) {
  const { run, summary, tests } = data;
  const duration =
    formatSeconds(summary?.duration_seconds) ?? formatDuration(run.started_at, run.completed_at);

  return (
    <>
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Results</span>
          <h3>Run #{run.id}</h3>
        </div>
        <div className="home-results-meta">
          <RunStatusBadge status={run.status} />
          <span>{run.browser}</span>
          {duration && <span>{duration}</span>}
        </div>
      </div>

      <div className="home-run-actions">
        <ReportActions key={run.id} projectId={projectId} run={run} />
      </div>

      {polling.refreshError && <p className="home-notice is-error" role="alert">{polling.refreshError}</p>}
      {polling.stopped && (
        <p className="home-notice is-info" role="status">
          Automatic refresh stopped. Reload the page to check the current status.
        </p>
      )}

      {summary === null && tests === null && (
        <p className="home-notice is-info" role="status">
          {WAITING_MESSAGE[run.status] || "Results are not available for this run."}
        </p>
      )}

      {summary && <RunResultsSummary summary={summary} />}

      {Array.isArray(tests) && tests.length === 0 && (
        data.issue ? (
          <RunIssueNotice issue={data.issue} />
        ) : (
          <div className="home-empty-state">
            <p>No test results were captured for this run.</p>
          </div>
        )
      )}

      {Array.isArray(tests) && tests.length > 0 && (
        <div className="home-test-list">
          {tests.map((test) => (
            <TestResultCard key={test.id} test={test} projectId={projectId} runId={run.id} />
          ))}
        </div>
      )}

      <p className="home-results-note">
        <Link to={`/projects/${projectId}/runs/${run.id}`}>Run details</Link>
      </p>
    </>
  );
}
