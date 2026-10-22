import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import ResultStatus from "./ResultStatus.jsx";
import FailureDiagnosticPanel from "./FailureDiagnosticPanel.jsx";
import FailureAnalysisPanel from "./FailureAnalysisPanel.jsx";
import ScreenshotThumb from "./ScreenshotThumb.jsx";
import { formatSeconds } from "../../utils/format.js";

function StepRow({ step, projectId, runId }) {
  const duration = formatSeconds(step.duration_seconds);
  const screenshots = step.screenshots || [];

  return (
    <li className="home-step">
      <div className="home-step-head">
        <span className="home-step-no">Step {step.step_number ?? step.sequence_index + 1}</span>
        <strong className="home-step-action">{String(step.action).toUpperCase()}</strong>
        <ResultStatus status={step.status} />
        {duration && <span className="home-step-time">{duration}</span>}
      </div>

      {step.target && <p className="home-step-target">{step.target}</p>}
      {step.value && <p className="home-step-value">Value: {step.value}</p>}
      {step.error && <p className="home-step-error">{step.error}</p>}

      {screenshots.length > 0 && (
        <div className="home-step-shots">
          {screenshots.map((shot) => (
            <ScreenshotThumb key={shot.id} projectId={projectId} runId={runId} screenshot={shot} />
          ))}
        </div>
      )}
    </li>
  );
}

export default function TestResultCard({ test, projectId, runId, framework }) {
  const [open, setOpen] = useState(false);
  const duration = formatSeconds(test.duration_seconds);
  const steps = test.steps || [];
  const playwright = framework === "playwright";
  // Playwright attaches screenshots to the test, not to a step. Anything already shown under a
  // step is not repeated. Bytes come through the authenticated screenshot endpoint.
  const shownInSteps = new Set(steps.flatMap((step) => (step.screenshots || []).map((shot) => shot.id)));
  const testShots = playwright ? (test.screenshots || []).filter((shot) => !shownInSteps.has(shot.id)) : [];
  const panelId = `test-${test.id}-panel`;
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <article className="home-test-card">
      <button
        type="button"
        className="home-test-head"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <Chevron size={18} aria-hidden="true" />
        <span className="home-test-name">{test.nodeid}</span>
        <ResultStatus status={test.status} />
        {duration && <span className="home-step-time">{duration}</span>}
      </button>

      {open && (
        <div className="home-test-body" id={panelId}>
          {(test.file_name || test.class_name || test.method_name) && (
            <p className="home-test-meta">
              {[test.file_name, test.class_name, test.method_name].filter(Boolean).join(" / ")}
            </p>
          )}

          {test.analysis ? (
            <FailureAnalysisPanel analysis={test.analysis} test={test} projectId={projectId} runId={runId} />
          ) : (
            test.failure && <FailureDiagnosticPanel failure={test.failure} />
          )}

          {steps.length > 0 ? (
            <ol className="home-steps" aria-label="Steps">
              {steps.map((step) => (
                <StepRow key={step.id} step={step} projectId={projectId} runId={runId} />
              ))}
            </ol>
          ) : (
            <p className="home-test-meta">
              {playwright
                ? "Playwright reports results per test; step details are not recorded."
                : "No steps were recorded for this test."}
            </p>
          )}

          {testShots.length > 0 && (
            <div className="home-step-shots" aria-label="Test screenshots">
              {testShots.map((shot) => (
                <ScreenshotThumb key={shot.id} projectId={projectId} runId={runId} screenshot={shot} />
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  );
}
