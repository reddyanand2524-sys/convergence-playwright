import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RotateCcw } from "lucide-react";

import { getRun, retryRun } from "../services/runsApi.js";
import { describeError } from "../services/authedFetch.js";
import { isRetryable, runStateChanged } from "../utils/runStatus.js";

const NOT_FOUND = "Project or Run not found.";

/*
 * Retry Run (FAILED) / Rerun (COMPLETED). Creates a NEW queued run via
 * POST .../retry and opens it; the original run is never touched and the new
 * run is not started automatically.
 */
export default function RetryRunControl({ projectId, run, onUpdate }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const inFlight = useRef(false);

  // Not retryable (e.g. the run turned out not to be terminal): hide the button
  // but keep the explanation for a refused attempt.
  if (!isRetryable(run.status)) {
    return info ? <p className="home-notice is-info" role="status">{info}</p> : null;
  }

  const label = run.status === "FAILED" ? "Retry Run" : "Rerun";

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");

    try {
      const created = await retryRun(projectId, run.id);
      setOpen(false);
      navigate(`/projects/${projectId}/runs/${created.id}`, {
        state: { retriedFrom: run.id },
      });
    } catch (err) {
      setError(
        err?.status === 409 || err?.status === 404 || err?.status === 401
          ? describeError(err, NOT_FOUND)
          : `Unable to create a new run. ${describeError(err, NOT_FOUND)}`
      );

      if (err?.status === 409 && onUpdate) {
        // The run is not in the state we displayed: show the server's state.
        try {
          const fresh = await getRun(projectId, run.id);
          // Only when the run really changed; otherwise the dialog keeps the backend's message.
          if (runStateChanged(run, fresh)) {
            setOpen(false);
            setInfo("Run state has changed. Showing the latest status.");
            onUpdate(fresh);
          }
        } catch {
          /* keep the 409 message in the dialog */
        }
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function close() {
    if (busy) return;
    setOpen(false);
    setError("");
  }

  return (
    <>
      <button className="home-secondary-button" type="button" onClick={() => setOpen(true)}>
        <RotateCcw size={17} aria-hidden="true" />
        {label}
      </button>

      {open && (
        <div
          className="home-modal-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && close()}
        >
          <section
            className="home-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="retry-run-title"
            aria-describedby="retry-run-desc"
          >
            <h2 id="retry-run-title">{label} #{run.id}?</h2>
            <p id="retry-run-desc">
              This creates a new queued run. Run #{run.id} and its results stay
              as they are. You can start the new run afterwards.
            </p>

            {error && <p className="home-notice is-error" role="alert">{error}</p>}

            <div className="home-modal-actions">
              <button className="home-secondary-button" type="button" disabled={busy} onClick={close}>
                Cancel
              </button>
              <button className="home-primary-button" type="button" disabled={busy} onClick={confirm}>
                {busy ? "Creating..." : label}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
