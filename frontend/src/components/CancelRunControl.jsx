import { useRef, useState } from "react";
import { XCircle } from "lucide-react";

import { cancelRun, getRun } from "../services/runsApi.js";
import { describeError } from "../services/authedFetch.js";

const NOT_FOUND = "Project or Run not found.";

/*
 * Cancel Run button + confirmation dialog. Rendered only for QUEUED/RUNNING
 * runs, and hidden once the backend reports cancel_requested (a RUNNING run
 * then just waits for the existing polling to see CANCELLED).
 * onUpdate(run) receives the backend's RunResponse.
 *
 * A 409 means the run's state changed under us (already finished, or another
 * cancel won the race): the run is re-fetched and the server state shown.
 */
export default function CancelRunControl({ projectId, run, onUpdate }) {
  const [open, setOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // notice: { kind: "error" | "info", text }
  const [notice, setNotice] = useState(null);
  const inFlight = useRef(false);

  const cancellable = run.status === "QUEUED" || run.status === "RUNNING";

  // Not cancellable (any more): hide the button, but keep showing why if a
  // cancel attempt left a message (e.g. 409 after the run already finished).
  if (!cancellable || run.cancel_requested) {
    return notice ? <p className={`home-notice is-${notice.kind}`} role="status">{notice.text}</p> : null;
  }

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setCancelling(true);
    setNotice(null);

    try {
      const updated = await cancelRun(projectId, run.id);
      setOpen(false);
      onUpdate(updated);
    } catch (err) {
      if (err?.status === 409) {
        try {
          const fresh = await getRun(projectId, run.id);
          setOpen(false);
          setNotice({ kind: "info", text: "Run state has changed. Showing the latest status." });
          onUpdate(fresh);
        } catch (refreshErr) {
          setNotice({ kind: "error", text: `${err.message} ${describeError(refreshErr, NOT_FOUND)}` });
        }
      } else {
        setNotice({
          kind: "error",
          text:
            err?.status === 404 || err?.status === 401
              ? describeError(err, NOT_FOUND)
              : `Unable to cancel run. ${describeError(err, NOT_FOUND)}`,
        });
      }
    } finally {
      inFlight.current = false;
      setCancelling(false);
    }
  }

  function close() {
    if (cancelling) return;
    setOpen(false);
    setNotice(null);
  }

  return (
    <>
      <button
        className="home-secondary-button is-danger-outline"
        type="button"
        onClick={() => setOpen(true)}
      >
        <XCircle size={17} aria-hidden="true" />
        Cancel Run
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
            aria-labelledby="cancel-run-title"
            aria-describedby="cancel-run-desc"
          >
            <h2 id="cancel-run-title">Cancel this run?</h2>
            <p id="cancel-run-desc">
              {run.status === "RUNNING"
                ? "The running execution will be stopped. This cannot be undone."
                : "This queued run will be cancelled and will not execute."}
            </p>

            {notice && <p className={`home-notice is-${notice.kind}`} role="alert">{notice.text}</p>}

            <div className="home-modal-actions">
              <button
                className="home-secondary-button"
                type="button"
                disabled={cancelling}
                onClick={close}
              >
                Keep Run
              </button>
              <button
                className="home-primary-button is-danger"
                type="button"
                disabled={cancelling}
                onClick={confirm}
              >
                {cancelling ? "Cancelling..." : "Cancel Run"}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
