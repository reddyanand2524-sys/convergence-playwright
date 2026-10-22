import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { Play } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import RunStatusBadge from "../../components/RunStatusBadge.jsx";
import StartedRunCard from "../../components/StartedRunCard.jsx";
import { createRun, getRun, getRuns, startRun } from "../../services/runsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { formatDateTime } from "../../utils/format.js";
import { frameworkLabel } from "../../utils/framework.js";
import { runStateChanged } from "../../utils/runStatus.js";

const NOT_FOUND = "Project or Run not found.";
const RECENT_RUNS = 5;

/*
 * Run tab. Several runs can be started from here and run at the same time; each
 * started run gets its own card with its own state and polling.
 * "Create Run" does two backend calls on exactly ONE run:
 *   POST /projects/{id}/runs            -> QUEUED run
 *   POST /projects/{id}/runs/{rid}/start -> RUNNING (execution is async)
 * If the start call fails, the already-created run is kept in `pending`
 * and retried with startRun - a second run is never created.
 */
export default function ProjectRunPage() {
  const { project } = useOutletContext();

  const [list, setList] = useState({ status: "loading", runs: [], error: "" });
  const [confirming, setConfirming] = useState(false);
  // "idle" | "creating" | "starting"
  const [phase, setPhase] = useState("idle");
  const [actionError, setActionError] = useState("");
  const [pending, setPending] = useState(null); // created, not yet started
  const [started, setStarted] = useState([]); // runs started from this page, newest first
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);

  const busy = phase !== "idle";

  const load = useCallback(
    async (signal) => {
      // Keep an already-loaded table on screen while it refreshes.
      setList((prev) => (prev.status === "ready" ? prev : { ...prev, status: "loading", error: "" }));
      try {
        // Latest runs only; the full paginated list lives on the History tab.
        const page = await getRuns(project.id, { page: 1, pageSize: RECENT_RUNS }, signal);
        setList({ status: "ready", runs: page.items, total: page.total, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setList({ status: "error", runs: [], error: describeError(err, NOT_FOUND) });
      }
    },
    [project.id]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const addStarted = (run) =>
    setStarted((prev) => [run, ...prev.filter((item) => item.id !== run.id)]);

  function openConfirm() {
    setActionError("");
    setConfirming(true);
  }

  async function start(run) {
    setPhase("starting");
    try {
      const startedRun = await startRun(project.id, run.id);
      setPending(null);
      setConfirming(false);
      addStarted(startedRun);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        // 409 does not always mean "someone else got there first". Only when the run really
        // changed do we show the server's state; otherwise (e.g. the runtime is unavailable)
        // the run stays pending and the backend's own message is shown below. Never retried
        // automatically.
        try {
          const fresh = await getRun(project.id, run.id);
          if (runStateChanged(run, fresh)) {
            setPending(null);
            setConfirming(false);
            setActionError("");
            setNotice(`Run #${run.id}: run state has changed. Showing the latest status.`);
            addStarted(fresh);
            await load();
            return;
          }
        } catch {
          /* fall through to the error below */
        }
      }
      setPending(run);
      setConfirming(false);
      setActionError(describeError(err, NOT_FOUND));
    }
  }

  async function onCreate() {
    if (inFlight.current) return;
    inFlight.current = true;
    setActionError("");
    setNotice("");
    setPhase("creating");

    try {
      const run = await createRun(project.id);
      await start(run);
    } catch (err) {
      setActionError(describeError(err, NOT_FOUND));
    } finally {
      inFlight.current = false;
      setPhase("idle");
    }
  }

  async function onRetryStart() {
    if (inFlight.current || !pending) return;
    inFlight.current = true;
    setActionError("");
    try {
      await start(pending);
    } finally {
      inFlight.current = false;
      setPhase("idle");
    }
  }

  const runPath = (run) => `/projects/${project.id}/runs/${run.id}`;

  return (
    <div className="home-runs">
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Runs</span>
          <h3>Run Test</h3>
        </div>

        {!confirming && !pending && (
          <button
            className="home-primary-button"
            type="button"
            disabled={busy}
            onClick={openConfirm}
          >
            <Play size={17} aria-hidden="true" />
            Run Test
          </button>
        )}
      </div>

      {confirming && (
        <section className="home-run-confirm" aria-label="Confirm run">
          <h4>Run using current project configuration</h4>
          <dl className="home-config-card">
            <div><dt>Project</dt><dd>{project.name}</dd></div>
            <div><dt>Framework</dt><dd>{frameworkLabel(project)}</dd></div>
            <div><dt>Browser</dt><dd>{project.browser}</dd></div>
            <div><dt>Frequency (project setting)</dt><dd>{project.frequency}</dd></div>
          </dl>
          <p className="home-page-description">
            A run is created with the browser above and then started. Frequency
            is only project configuration and does not schedule this run.
          </p>

          {actionError && <p className="home-notice is-error" role="alert">{actionError}</p>}

          <div className="home-config-actions">
            <button
              className="home-secondary-button"
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
            <button
              className="home-primary-button"
              type="button"
              disabled={busy}
              onClick={onCreate}
            >
              {phase === "creating"
                ? "Creating Run..."
                : phase === "starting"
                  ? "Starting Run..."
                  : "Create & Start Run"}
            </button>
          </div>
        </section>
      )}

      {pending && (
        <section className="home-run-confirm" aria-label="Run not started">
          <h4>Run #{pending.id} was created but not started</h4>
          {actionError && <p className="home-notice is-error" role="alert">{actionError}</p>}
          <div className="home-config-actions">
            <Link className="home-secondary-button" to={runPath(pending)}>View run</Link>
            <button
              className="home-primary-button"
              type="button"
              disabled={busy}
              onClick={onRetryStart}
            >
              {phase === "starting" ? "Starting Run..." : "Start Run"}
            </button>
          </div>
        </section>
      )}

      {notice && <p className="home-notice is-info" role="status">{notice}</p>}

      {started.map((run) => (
        <StartedRunCard
          key={run.id}
          projectId={project.id}
          initialRun={run}
          onSettled={() => load()}
        />
      ))}

      {list.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading runs..." />
        </div>
      )}

      {list.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load runs</h3>
          <p>{list.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {list.status === "ready" && list.runs.length === 0 && !confirming && !pending && started.length === 0 && (
        <div className="home-empty-state">
          <h3>No runs yet</h3>
          <p>No runs have been created for this project yet.</p>
          <button className="home-primary-button" type="button" disabled={busy} onClick={openConfirm}>
            <Play size={17} aria-hidden="true" />
            Run Test
          </button>
        </div>
      )}

      {list.status === "ready" && list.runs.length > 0 && (
        <div className="home-table-wrap">
          <table className="home-table">
            <thead>
              <tr><th>Run</th><th>Status</th><th>Framework</th><th>Browser</th><th>Created</th></tr>
            </thead>
            <tbody>
              {list.runs.map((run) => (
                <tr key={run.id}>
                  <td><Link to={runPath(run)}>#{run.id}</Link></td>
                  <td><RunStatusBadge status={run.status} /></td>
                  <td>{frameworkLabel(run)}</td>
                  <td>{run.browser}</td>
                  <td>{formatDateTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {list.status === "ready" && list.total > list.runs.length && (
        <p className="home-results-note">
          Showing the latest {list.runs.length} of {list.total} runs.{" "}
          <Link to={`/projects/${project.id}/history`}>View full run history</Link>
        </p>
      )}
    </div>
  );
}
