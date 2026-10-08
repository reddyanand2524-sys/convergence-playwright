import { useRef, useState } from "react";
import { Play } from "lucide-react";

import { getRun, startRun } from "../services/runsApi.js";
import { describeError } from "../services/authedFetch.js";

/*
 * Start button for a QUEUED run (e.g. one just created by Retry/Rerun).
 * Uses the existing startRun call; polling then follows the run.
 * A 409 means someone else already started/cancelled it: the run is re-fetched
 * and the server's state is shown instead of claiming the start succeeded.
 */
export default function StartRunControl({ projectId, run, onUpdate }) {
  const [starting, setStarting] = useState(false);
  // notice: { kind: "error" | "info", text }
  const [notice, setNotice] = useState(null);
  const inFlight = useRef(false);

  const shown = notice ? <p className={`home-notice is-${notice.kind}`} role="status">{notice.text}</p> : null;

  if (run.status !== "QUEUED" || run.cancel_requested) return shown;

  async function start() {
    if (inFlight.current) return;
    inFlight.current = true;
    setStarting(true);
    setNotice(null);

    try {
      onUpdate(await startRun(projectId, run.id));
    } catch (err) {
      if (err?.status === 409) {
        try {
          const fresh = await getRun(projectId, run.id);
          setNotice({ kind: "info", text: "Run state has changed. Showing the latest status." });
          onUpdate(fresh);
        } catch (refreshErr) {
          setNotice({ kind: "error", text: `${err.message} ${describeError(refreshErr, "Project or Run not found.")}` });
        }
      } else {
        setNotice({ kind: "error", text: describeError(err, "Project or Run not found.") });
      }
    } finally {
      inFlight.current = false;
      setStarting(false);
    }
  }

  return (
    <>
      <button className="home-primary-button" type="button" disabled={starting} onClick={start}>
        <Play size={17} aria-hidden="true" />
        {starting ? "Starting Run..." : "Start Run"}
      </button>
      {shown}
    </>
  );
}
