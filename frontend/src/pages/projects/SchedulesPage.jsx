import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { CalendarClock, Plus } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import ScheduleList from "../../components/ScheduleList.jsx";
import ScheduleForm from "../../components/ScheduleForm.jsx";
import {
  deleteSchedule,
  disableSchedule,
  enableSchedule,
  listSchedules,
  runScheduleNow,
} from "../../services/schedulesApi.js";
import { describeError } from "../../services/authedFetch.js";

const REFRESH_MS = 30000; // modest background refresh; the backend scheduler changes state on its own

/*
 * /projects/:projectId/schedules
 * Shows the backend's persisted schedules. The frontend never runs anything:
 * scheduling, ONCE auto-disable and run creation all happen on the backend.
 */
export default function SchedulesPage() {
  const { project } = useOutletContext();

  // status: "loading" | "ready" | "error"
  const [state, setState] = useState({ status: "loading", items: [], error: "" });
  const [form, setForm] = useState(null); // null | { schedule? }
  const [confirm, setConfirm] = useState(null); // null | { kind: "delete" | "run", schedule }
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmError, setConfirmError] = useState("");
  const [pending, setPending] = useState({}); // scheduleId -> "toggle" | "run" | "delete"
  const [notice, setNotice] = useState(null); // { kind, text, runId? }
  const inFlight = useRef({}); // scheduleId -> true

  const load = useCallback(
    async ({ signal, silent } = {}) => {
      if (!silent) setState((prev) => ({ ...prev, status: "loading", error: "" }));
      try {
        const items = await listSchedules(project.id, signal);
        setState({ status: "ready", items, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        // A failed background refresh keeps showing what we have.
        if (silent) return;
        setState({ status: "error", items: [], error: describeError(err, "Project not found.") });
      }
    },
    [project.id]
  );

  useEffect(() => {
    const controller = new AbortController();
    load({ signal: controller.signal });
    return () => controller.abort();
  }, [load]);

  // Background refresh: while the tab is visible, and again when it regains focus.
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") load({ silent: true });
    };
    const timer = setInterval(tick, REFRESH_MS);
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("focus", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
      window.removeEventListener("focus", tick);
    };
  }, [load]);

  const replace = (saved) =>
    setState((prev) => ({ ...prev, items: prev.items.map((item) => (item.id === saved.id ? saved : item)) }));

  function begin(id, action) {
    if (inFlight.current[id]) return false;
    inFlight.current[id] = true;
    setPending((prev) => ({ ...prev, [id]: action }));
    return true;
  }

  function end(id) {
    inFlight.current[id] = false;
    setPending((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  // The schedule is gone or changed on the server: say so and re-sync.
  function conflict(err, fallbackText) {
    if (err?.status === 404) {
      setNotice({ kind: "error", text: "This schedule no longer exists." });
    } else {
      setNotice({ kind: "error", text: describeError(err, fallbackText) });
    }
    load({ silent: true });
  }

  async function toggle(schedule) {
    if (!begin(schedule.id, "toggle")) return;
    setNotice(null);
    try {
      const saved = await (schedule.enabled ? disableSchedule : enableSchedule)(project.id, schedule.id);
      replace(saved);
      setNotice({ kind: "success", text: `"${saved.name}" ${saved.enabled ? "enabled" : "disabled"}.` });
    } catch (err) {
      conflict(err, "Project or schedule not found.");
    } finally {
      end(schedule.id);
    }
  }

  async function confirmAction() {
    if (!confirm || confirmBusy) return;
    const { kind, schedule } = confirm;
    if (!begin(schedule.id, kind)) return;
    setConfirmBusy(true);
    setConfirmError("");

    try {
      if (kind === "delete") {
        await deleteSchedule(project.id, schedule.id);
        setState((prev) => ({ ...prev, items: prev.items.filter((item) => item.id !== schedule.id) }));
        setNotice({ kind: "success", text: `"${schedule.name}" deleted. Its runs were not deleted.` });
      } else {
        const run = await runScheduleNow(project.id, schedule.id);
        setNotice({ kind: "success", text: `"${schedule.name}" started as Run #${run.id}.`, runId: run.id });
        load({ silent: true });
      }
      setConfirm(null);
    } catch (err) {
      if (err?.status === 404) {
        setConfirm(null);
        conflict(err);
      } else {
        // 409 (an active run already exists) and everything else stay in the dialog
        setConfirmError(err?.status === 0 ? describeError(err) : err?.message || describeError(err));
        if (err?.status === 409) load({ silent: true });
      }
    } finally {
      end(schedule.id);
      setConfirmBusy(false);
    }
  }

  function saved(schedule, wasEdit) {
    setForm(null);
    setNotice({ kind: "success", text: `Schedule "${schedule.name}" ${wasEdit ? "updated" : "created"}.` });
    load({ silent: true });
  }

  const { status, items } = state;

  return (
    <div className="home-runs">
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Automation</span>
          <h3>Schedules</h3>
        </div>

        <button className="home-primary-button" type="button" onClick={() => setForm({})}>
          <Plus size={17} aria-hidden="true" />
          Create Schedule
        </button>
      </div>

      <p className="home-page-description">
        The backend scheduler starts these runs automatically. Times are shown in your local time zone.
      </p>

      {notice && (
        <p className={`home-notice is-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
          {notice.text}{" "}
          {notice.runId && <Link to={`/projects/${project.id}/runs/${notice.runId}`}>View Run #{notice.runId}</Link>}
        </p>
      )}

      {status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading schedules..." />
        </div>
      )}

      {status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load schedules</h3>
          <p>{state.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {status === "ready" && items.length === 0 && (
        <div className="home-empty-state">
          <span className="home-empty-icon"><CalendarClock size={25} aria-hidden="true" /></span>
          <h3>No schedules configured.</h3>
          <p>Create a schedule to run this project's tests automatically.</p>
          <button className="home-primary-button" type="button" onClick={() => setForm({})}>
            <Plus size={17} aria-hidden="true" />
            Create Schedule
          </button>
        </div>
      )}

      {status === "ready" && items.length > 0 && (
        <ScheduleList
          schedules={items}
          pending={pending}
          onEdit={(schedule) => setForm({ schedule })}
          onToggle={toggle}
          onRunNow={(schedule) => { setConfirmError(""); setConfirm({ kind: "run", schedule }); }}
          onDelete={(schedule) => { setConfirmError(""); setConfirm({ kind: "delete", schedule }); }}
        />
      )}

      {form && (
        <ScheduleForm
          projectId={project.id}
          project={project}
          schedule={form.schedule}
          onClose={() => setForm(null)}
          onSaved={saved}
        />
      )}

      {confirm && (
        <div
          className="home-modal-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && !confirmBusy && setConfirm(null)}
        >
          <section className="home-dialog" role="alertdialog" aria-modal="true" aria-labelledby="sch-confirm-title" aria-describedby="sch-confirm-desc">
            <h2 id="sch-confirm-title">
              {confirm.kind === "delete" ? "Delete this schedule?" : "Run this schedule now?"}
            </h2>
            <p id="sch-confirm-desc">
              {confirm.kind === "delete"
                ? `"${confirm.schedule.name}" will stop running. Runs it already created are not deleted.`
                : `This starts a normal run of "${confirm.schedule.name}" immediately, using the schedule's browser (${confirm.schedule.browser}).`}
            </p>

            {confirmError && <p className="home-notice is-error" role="alert">{confirmError}</p>}

            <div className="home-modal-actions">
              <button className="home-secondary-button" type="button" disabled={confirmBusy} onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button
                className={`home-primary-button${confirm.kind === "delete" ? " is-danger" : ""}`}
                type="button"
                disabled={confirmBusy}
                onClick={confirmAction}
              >
                {confirmBusy
                  ? confirm.kind === "delete" ? "Deleting..." : "Starting..."
                  : confirm.kind === "delete" ? "Delete" : "Run Now"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
