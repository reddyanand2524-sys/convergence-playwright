import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Ban, CheckCircle2, ChevronDown, ChevronRight, Clock, ListChecks, Loader, OctagonX, XCircle } from "lucide-react";

import {
  STATUS_LABEL,
  formatStepDuration,
  groupRowsByTest,
  outcomeOf,
} from "../utils/runTimeline.js";
import { describeStep, failureSummary } from "../utils/stepDescription.js";

/*
 * Live execution timeline for a run. Renders only what the event stream reported:
 * one row per step (updated in place from RUNNING to PASSED / FAILED), grouped by
 * test. A row shows a readable title, its status and duration; a button expands the
 * technical details (action, element, locator, ...). There are no "waiting" rows
 * because the backend does not announce steps before they start, and nothing here can
 * show typed values, full URLs or error text - the events do not carry them; a failed
 * step shows a summary and the exception class only.
 *
 * Props: timeline (from useRunEvents), connection, error, runStatus (the page's
 * polled run.status, used for the final outcome if the stream has not delivered it),
 * resultsHref (optional; where the failure analysis lives, offered on failed steps),
 * framework ("playwright" switches to the test-level view below; anything else is the
 * Selenium step timeline, unchanged).
 */
const ICONS = { RUNNING: Loader, PASSED: CheckCircle2, FAILED: XCircle, INTERRUPTED: OctagonX };

const CONNECTION_LABEL = {
  connecting: "Connecting...",
  live: "Live",
  reconnecting: "Reconnecting...",
  ended: "Finished",
};

function StepRow({ row, resultsHref }) {
  const [open, setOpen] = useState(false);
  const Icon = ICONS[row.status] || Loader;
  const duration = formatStepDuration(row.duration);
  const number = row.number != null ? row.number : row.index + 1;
  const { title, subtitle, details } = describeStep(row);
  const panelId = `cv-step-${String(row.id).replace(/[^A-Za-z0-9_-]/g, "-")}`;

  return (
    <li
      className={`cv-timeline-row is-${row.status.toLowerCase()}`}
      aria-current={row.status === "RUNNING" ? "step" : undefined}
    >
      <div className="cv-timeline-main">
        <Icon className="cv-timeline-icon" size={16} aria-hidden="true" />
        <span className="cv-timeline-name">
          <span className="cv-timeline-step">Step {number} &mdash;</span>
          <strong className="cv-timeline-title">{title}</strong>
          {subtitle && <span className="cv-timeline-sub">{subtitle}</span>}
        </span>
        <span className="cv-timeline-status">{STATUS_LABEL[row.status] || row.status}</span>
        {duration && <span className="cv-timeline-duration">{duration}</span>}
        <button
          type="button"
          className="cv-timeline-toggle"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          Details
        </button>
      </div>

      {row.status === "FAILED" && (
        <p className="cv-timeline-error">
          Failed: {failureSummary(row.errorCategory)}.
          {row.errorCategory && <span className="cv-timeline-class"> {row.errorCategory}</span>}
          {resultsHref && (
            <>
              {" "}
              <Link to={resultsHref}>View failure analysis</Link>
            </>
          )}
        </p>
      )}

      {open && (
        <dl className="cv-timeline-details" id={panelId}>
          {details.map((item) => (
            <div key={item.label}>
              <dt>{item.label}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

// Playwright runs publish test-level events only (test_started / test_finished): there are no
// step events, so this view lists tests and their outcome and never waits for steps.
const TEST_ICONS = {
  RUNNING: Loader,
  PASSED: CheckCircle2,
  FAILED: XCircle,
  ERROR: XCircle,
  SKIPPED: Ban,
  INTERRUPTED: OctagonX,
};

function TestRow({ test }) {
  const Icon = TEST_ICONS[test.status] || Clock;
  const duration = formatStepDuration(test.duration);

  return (
    <li
      className={`cv-timeline-row is-${String(test.status).toLowerCase()}`}
      aria-current={test.status === "RUNNING" ? "step" : undefined}
    >
      <div className="cv-timeline-main">
        <Icon className="cv-timeline-icon" size={16} aria-hidden="true" />
        <span className="cv-timeline-name">
          <strong className="cv-timeline-title">{test.name || test.id}</strong>
        </span>
        <span className="cv-timeline-status">{STATUS_LABEL[test.status] || test.status}</span>
        {duration && <span className="cv-timeline-duration">{duration}</span>}
      </div>
    </li>
  );
}

function TestTimeline({ timeline, connection, error, runStatus }) {
  const outcome = outcomeOf(timeline, runStatus);
  const ended = Boolean(outcome);

  return (
    <section className="cv-timeline" aria-label="Live execution progress">
      <div className="cv-timeline-head">
        <h4><ListChecks size={16} aria-hidden="true" /> Execution progress</h4>
        {CONNECTION_LABEL[connection] && !ended && (
          <span className={`cv-timeline-conn is-${connection}`}>{CONNECTION_LABEL[connection]}</span>
        )}
      </div>

      <p className="cv-timeline-note" role="note">
        Playwright runs report progress per test. Step-by-step events are not available.
      </p>

      {timeline.gap && (
        <p className="cv-timeline-note" role="note">
          <AlertTriangle size={14} aria-hidden="true" /> Some earlier test events are no longer
          available here. The final results list every test.
        </p>
      )}

      {connection === "error" && (
        <p className="cv-timeline-note is-error" role="alert">
          {error || "Live updates are unavailable."} The run itself is not affected.
        </p>
      )}

      {timeline.tests.length === 0 ? (
        <p className="cv-timeline-empty" role="status">
          {ended ? "No test events were recorded for this run." : "Waiting for the first test to start."}
        </p>
      ) : (
        <ol className="cv-timeline-list">
          {timeline.tests.map((test) => (
            <TestRow key={test.id} test={test} />
          ))}
        </ol>
      )}

      {outcome && (
        <p className={`cv-timeline-outcome is-${outcome.type}`} role="status">
          {outcome.text}
        </p>
      )}
    </section>
  );
}

export default function StepTimeline({ timeline, connection, error, runStatus, resultsHref, framework }) {
  if (framework === "playwright") {
    return <TestTimeline timeline={timeline} connection={connection} error={error} runStatus={runStatus} />;
  }

  const groups = groupRowsByTest(timeline);
  const named = groups.filter((g) => g.name).length > 1;
  const outcome = outcomeOf(timeline, runStatus);
  const ended = Boolean(outcome);

  return (
    <section className="cv-timeline" aria-label="Live execution steps">
      <div className="cv-timeline-head">
        <h4><ListChecks size={16} aria-hidden="true" /> Execution steps</h4>
        {CONNECTION_LABEL[connection] && !ended && (
          <span className={`cv-timeline-conn is-${connection}`}>{CONNECTION_LABEL[connection]}</span>
        )}
      </div>

      {timeline.gap && (
        <p className="cv-timeline-note" role="note">
          <AlertTriangle size={14} aria-hidden="true" /> Some earlier steps are no longer available
          here. The final results list every step.
        </p>
      )}

      {connection === "error" && (
        <p className="cv-timeline-note is-error" role="alert">
          {error || "Live step updates are unavailable."} The run itself is not affected.
        </p>
      )}

      {timeline.rows.length === 0 && (
        <p className="cv-timeline-empty" role="status">
          {ended
            ? "No step events were recorded for this run."
            : "Waiting for the first step. Browser actions, explicit waits and failed assertions appear here as they happen; other Python code is not shown as steps."}
        </p>
      )}

      {groups
        .filter((g) => g.rows.length > 0)
        .map((group) => (
          <div className="cv-timeline-test" key={group.id}>
            {named && group.name && <h5>{group.name}</h5>}
            <ol className="cv-timeline-list">
              {group.rows.map((row) => (
                <StepRow key={row.id} row={row} resultsHref={resultsHref} />
              ))}
            </ol>
          </div>
        ))}

      {outcome && (
        <p className={`cv-timeline-outcome is-${outcome.type}`} role="status">
          {outcome.text}
        </p>
      )}
    </section>
  );
}
