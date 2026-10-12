import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ListChecks, Loader, OctagonX, XCircle } from "lucide-react";

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
 * resultsHref (optional; where the failure analysis lives, offered on failed steps).
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

export default function StepTimeline({ timeline, connection, error, runStatus, resultsHref }) {
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
