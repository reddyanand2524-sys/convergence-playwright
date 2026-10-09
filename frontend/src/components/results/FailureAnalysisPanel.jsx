import { useEffect, useRef, useState } from "react";
import { Check, Copy, ImageOff } from "lucide-react";

import ScreenshotThumb from "./ScreenshotThumb.jsx";
import { formatDateTime, formatSeconds } from "../../utils/format.js";

/*
 * Failure analysis for one failed test.
 *
 * Renders the `analysis` object that GET .../runs/{id}/results already returns
 * (no extra request). The backend keeps the two kinds of statements apart and
 * so does this panel:
 *   - "Evidence" = facts read from recorded data (confirmed);
 *   - "Likely cause" and "Suggestions" = hypotheses / advice (not confirmed).
 * Every field may be missing (legacy records), so each section is optional.
 */

const CONFIDENCE_LABEL = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

const ROLE_LABEL = {
  failed: "Failed step",
  last_successful: "Last successful step",
  preceding: "Earlier step",
};

function stepText(step) {
  const number = step.step_number ?? step.sequence_index;
  return [`#${number}`, step.action, step.target].filter(Boolean).join(" ");
}

function technicalText(analysis) {
  const lines = [
    analysis.exception_type && `Exception: ${analysis.exception_type}`,
    analysis.exception_message && `Message: ${analysis.exception_message}`,
    analysis.file_name &&
      `Location: ${analysis.file_name}${analysis.line_number != null ? `:${analysis.line_number}` : ""}`,
    analysis.source_line && `Source line: ${analysis.source_line}`,
    analysis.page_url && `Last navigation target: ${analysis.page_url}`,
    analysis.traceback && `\nTraceback:\n${analysis.traceback}`,
  ];
  return lines.filter(Boolean).join("\n");
}

async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

function CopyButton({ text }) {
  const [state, setState] = useState("idle"); // idle | copied | failed
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function onCopy() {
    setState((await copyToClipboard(text)) ? "copied" : "failed");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }

  return (
    <button className="home-secondary-button home-analysis-copy" type="button" onClick={onCopy}>
      {state === "copied" ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy details"}
    </button>
  );
}

export default function FailureAnalysisPanel({ analysis, test, projectId, runId }) {
  if (!analysis) return null;

  const evidence = analysis.evidence || [];
  const timeline = analysis.timeline || [];
  const actions = analysis.recommended_actions || [];
  const technical = technicalText(analysis);
  const duration = formatSeconds(analysis.duration_seconds ?? test?.duration_seconds);
  const when = analysis.timestamp ? formatDateTime(analysis.timestamp) : null;
  const meta = [analysis.test_status, analysis.browser, duration, when].filter(Boolean);

  return (
    <section className="home-analysis" aria-label="Failure analysis">
      <header className="home-analysis-head">
        <h5>Failure analysis</h5>
        <h6 className="home-analysis-title">{analysis.failure_title || analysis.failure_category}</h6>
        <p className="home-analysis-badges">
          {analysis.failure_category && <span className="home-analysis-badge">{analysis.failure_category}</span>}
          {analysis.confidence && (
            <span
              className={`home-analysis-badge is-${analysis.confidence}`}
              title={analysis.confidence_note || undefined}
            >
              {CONFIDENCE_LABEL[analysis.confidence] || analysis.confidence}
            </span>
          )}
        </p>
        {analysis.summary && <p className="home-analysis-summary">{analysis.summary}</p>}
        {meta.length > 0 && <p className="home-analysis-meta">{meta.join(" · ")}</p>}
      </header>

      <div className="home-analysis-section">
        <h6>Root-cause analysis</h6>
        {analysis.likely_cause && (
          <>
            <p className="home-analysis-label">Likely cause (hypothesis, not confirmed)</p>
            <p>{analysis.likely_cause}</p>
          </>
        )}
        {analysis.confidence_note && <p className="home-analysis-note">{analysis.confidence_note}</p>}
        {evidence.length > 0 ? (
          <>
            <p className="home-analysis-label">Evidence (recorded facts)</p>
            <dl className="home-analysis-evidence">
              {evidence.map((item, index) => (
                <div key={`${item.label}-${index}`}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <p className="home-analysis-note">No supporting evidence was recorded for this failure.</p>
        )}
      </div>

      <div className="home-analysis-section">
        <h6>Failure timeline</h6>
        {timeline.length > 0 ? (
          <ol className="home-analysis-timeline">
            {timeline.map((step) => (
              <li key={step.sequence_index} className={`is-${step.role}`}>
                <span className="home-analysis-role">{ROLE_LABEL[step.role] || "Step"}</span>
                <span className="home-analysis-step">{stepText(step)}</span>
                <span className="home-analysis-step-status">{step.status}</span>
                {formatSeconds(step.duration_seconds) && (
                  <span className="home-analysis-note">{formatSeconds(step.duration_seconds)}</span>
                )}
                {step.occurred_at && <span className="home-analysis-note">{formatDateTime(step.occurred_at)}</span>}
                {step.screenshot_ids?.length > 0 && <span className="home-analysis-note">screenshot recorded</span>}
                {step.error && <span className="home-analysis-step-error">{step.error}</span>}
              </li>
            ))}
          </ol>
        ) : (
          <p className="home-analysis-note">No steps were recorded, so there is no timeline for this failure.</p>
        )}
        {!analysis.failed_step && timeline.length > 0 && (
          <p className="home-analysis-note">
            No recorded step is marked failed. The failing action may not be one of the recorded steps.
          </p>
        )}
      </div>

      <div className="home-analysis-section">
        <h6>Failure screenshot</h6>
        {analysis.failure_screenshot ? (
          <ScreenshotThumb projectId={projectId} runId={runId} screenshot={analysis.failure_screenshot} />
        ) : (
          <p className="home-analysis-note">
            <ImageOff size={14} aria-hidden="true" /> No screenshot was captured for this failure.
          </p>
        )}
      </div>

      <div className="home-analysis-section">
        <h6>Recommended actions</h6>
        {actions.length > 0 ? (
          <>
            <p className="home-analysis-note">Suggestions to investigate; they are not confirmed fixes.</p>
            <ol className="home-analysis-actions">
              {actions.map((action, index) => (
                <li key={index}>{action}</li>
              ))}
            </ol>
          </>
        ) : (
          <p className="home-analysis-note">No recommendations are available.</p>
        )}
      </div>

      <details className="home-analysis-tech">
        <summary>Technical details</summary>
        {technical ? (
          <>
            <CopyButton text={technical} />
            <dl className="home-analysis-evidence">
              {analysis.exception_type && (
                <div><dt>Exception type</dt><dd>{analysis.exception_type}</dd></div>
              )}
              {analysis.exception_message && (
                <div><dt>Exception message</dt><dd>{analysis.exception_message}</dd></div>
              )}
              {analysis.file_name && (
                <div>
                  <dt>Source</dt>
                  <dd>{analysis.file_name}{analysis.line_number != null ? `, line ${analysis.line_number}` : ""}</dd>
                </div>
              )}
              {analysis.source_line && <div><dt>Source line</dt><dd><code>{analysis.source_line}</code></dd></div>}
              {analysis.page_url && (
                <div>
                  <dt>Last navigation target</dt>
                  <dd>
                    {analysis.page_url}
                    {analysis.page_url_source && <span className="home-analysis-note"> — {analysis.page_url_source}</span>}
                  </dd>
                </div>
              )}
            </dl>
            {analysis.traceback ? (
              <pre className="cv-log home-analysis-trace" tabIndex={0} aria-label="Traceback">{analysis.traceback}</pre>
            ) : (
              <p className="home-analysis-note">No traceback was stored for this result.</p>
            )}
          </>
        ) : (
          <p className="home-analysis-note">No technical details were recorded for this failure.</p>
        )}
      </details>
    </section>
  );
}
