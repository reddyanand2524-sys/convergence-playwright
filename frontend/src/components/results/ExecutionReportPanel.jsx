import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import ResultStatus from "./ResultStatus.jsx";
import ScreenshotThumb from "./ScreenshotThumb.jsx";
import LoadingState from "../brand/LoadingState.jsx";
import { getExecutionReport } from "../../services/runsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { formatDateTime } from "../../utils/format.js";

const formatMs = (ms) => {
  if (ms === null || ms === undefined) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
};

// "screenshots/<file>" (as written in the report) -> shape ScreenshotThumb expects.
const toShot = (path, reason) => ({
  file_name: String(path).split("/").pop(),
  capture_reason: reason,
});

function StepRow({ step, projectId, runId }) {
  const duration = formatMs(step.executionTimeMs);
  return (
    <li className="home-step" data-testid="report-step">
      <div className="home-step-head">
        <span className="home-step-no">Step {step.stepNumber}</span>
        <strong className="home-step-action">{step.command.toUpperCase()}</strong>
        <ResultStatus status={step.status} />
        {!step.attempted && <span className="home-step-time">not attempted</span>}
        {duration && <span className="home-step-time">{duration}</span>}
      </div>
      <p className="home-step-target">{step.stepName}</p>
      {step.value && <p className="home-step-value">Value: {step.value}</p>}
      {step.error && (
        <p className="home-step-error">
          {step.error.type} ({step.error.category}): {step.error.message}
        </p>
      )}
      {step.skipReason && <p className="home-step-value">Skipped: {step.skipReason}</p>}
      {step.screenshot && (
        <div className="home-step-shots">
          <ScreenshotThumb
            projectId={projectId}
            runId={runId}
            screenshot={toShot(step.screenshot, step.status === "FAILED" ? "STEP_FAILURE" : "STEP")}
          />
        </div>
      )}
    </li>
  );
}

/*
 * Renders the backend's JSON execution report (GET .../report/json) — summary,
 * every defined step's final state, pages visited, attempts and the raw JSON.
 * Everything shown comes from the response; nothing is hard-coded.
 */
export default function ExecutionReportPanel({ projectId, runId }) {
  // status: "loading" | "ready" | "missing" | "error"
  const [state, setState] = useState({ status: "loading", report: null, error: "" });
  const [open, setOpen] = useState(false);
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading", report: null, error: "" });
    getExecutionReport(projectId, runId, controller.signal)
      .then((report) => setState({ status: "ready", report, error: "" }))
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({
          status: err?.status === 404 ? "missing" : "error",
          report: null,
          error: err?.status === 404 && typeof err.detail === "string" ? err.detail : describeError(err),
        });
      });
    return () => controller.abort();
  }, [projectId, runId]);

  if (state.status === "loading") {
    return <LoadingState type="orbit" inline label="Loading execution report…" />;
  }
  if (state.status === "missing") {
    return (
      <p className="home-notice is-info" role="status">
        {state.error || "No JSON execution report is available for this run."}
      </p>
    );
  }
  if (state.status === "error") {
    return <p className="home-notice is-error" role="alert">{state.error}</p>;
  }

  const { report } = state;
  const { summary } = report;
  const Chevron = open ? ChevronDown : ChevronRight;
  const items = [
    { label: "Total steps", value: summary.totalSteps },
    { label: "Attempted", value: summary.attemptedSteps },
    { label: "Passed", value: summary.passedSteps, kind: "pass" },
    { label: "Failed", value: summary.failedSteps, kind: "fail" },
    { label: "Skipped", value: summary.skippedSteps, kind: "skip" },
    // Optional (absent in older stored reports).
    ...(summary.notRunSteps != null ? [{ label: "Not run", value: summary.notRunSteps, kind: "skip" }] : []),
    { label: "Pages", value: summary.totalPages },
  ];

  return (
    <section className="home-test-card" aria-label="Execution report">
      <button
        type="button"
        className="home-test-head"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Chevron size={18} aria-hidden="true" />
        <span className="home-test-name">
          Execution report · {report.executionId}
        </span>
        <ResultStatus status={report.status} />
        {formatMs(report.executionTimeMs) && (
          <span className="home-step-time">{formatMs(report.executionTimeMs)}</span>
        )}
      </button>

      {open && (
        <div className="home-test-body">
          <p className="home-step-value">
            {report.testcaseName ? `${report.testcaseName} · ` : ""}
            Started {formatDateTime(report.startTime)} · schema v{report.reportVersion}
          </p>
          <p className="home-step-value">{report.actualOutcome}</p>

          <dl className="home-result-summary" aria-label="Report summary">
            {items.map((item) => (
              <div key={item.label} className={item.kind ? `is-${item.kind}` : undefined}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>

          {report.errors.length > 0 && (
            <>
              <h4>Errors ({report.errors.length})</h4>
              <ul className="home-step-list">
                {report.errors.map((err, i) => (
                  <li key={i} className="home-step">
                    <p className="home-step-error">
                      {err.stepNumber ? `Step ${err.stepNumber} — ` : ""}
                      {err.type} ({err.category}): {err.message}
                    </p>
                  </li>
                ))}
              </ul>
            </>
          )}

          <h4>Steps</h4>
          <ol className="home-step-list">
            {report.steps.map((step) => (
              <StepRow key={`${step.testId}-${step.stepNumber}`} step={step} projectId={projectId} runId={runId} />
            ))}
          </ol>

          {report.pages.length > 0 && (
            <>
              <h4>Pages</h4>
              <ul className="home-step-list">
                {report.pages.map((page) => (
                  <li key={page.pageId} className="home-step">
                    <strong>{page.pageId}</strong> {page.pageTitle ? `${page.pageTitle} — ` : ""}
                    {page.url || "URL not recorded"} · {page.events.length} event(s)
                  </li>
                ))}
              </ul>
            </>
          )}

          {report.attempts.length > 0 && (
            <>
              <h4>Attempts</h4>
              <ul className="home-step-list">
                {report.attempts.map((attempt) => (
                  <li key={attempt.attemptNumber} className="home-step">
                    Attempt {attempt.attemptNumber}: <ResultStatus status={attempt.status} />{" "}
                    {formatMs(attempt.executionTimeMs)}
                  </li>
                ))}
              </ul>
            </>
          )}

          <button type="button" className="home-secondary-button" onClick={() => setShowJson((v) => !v)}>
            {showJson ? "Hide JSON" : "View JSON"}
          </button>
          {showJson && (
            <pre className="home-json-view" aria-label="Raw JSON report">
              {JSON.stringify(report, null, 2)}
            </pre>
          )}
        </div>
      )}
    </section>
  );
}
