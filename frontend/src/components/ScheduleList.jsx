import ScheduleStatusBadge from "./ScheduleStatusBadge.jsx";
import { formatDateTime } from "../utils/format.js";

const FREQUENCY_LABELS = { ONCE: "Once", INTERVAL: "Interval", HOURLY: "Hourly", DAILY: "Daily", WEEKLY: "Weekly" };

function frequencyText(schedule) {
  const label = FREQUENCY_LABELS[schedule.frequency] || schedule.frequency;
  return schedule.frequency === "INTERVAL" && schedule.interval_value != null
    ? `${label} (every ${schedule.interval_value} min)`
    : label;
}

/*
 * Table of schedules. All values are the backend's; "-" marks a missing one.
 * Next run is shown only while the schedule is enabled - a disabled schedule
 * is not going to run, whatever next_run_at still holds.
 * `pending` maps schedule id -> the action in flight, to block double clicks.
 */
export default function ScheduleList({ schedules, pending, onEdit, onToggle, onRunNow, onDelete }) {
  return (
    <div className="home-table-wrap">
      <table className="home-table home-schedule-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Browser</th>
            <th>Frequency</th>
            <th>Next run</th>
            <th>Last run</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {schedules.map((schedule) => {
            const busy = pending[schedule.id];
            return (
              <tr key={schedule.id}>
                <td className="home-schedule-name">{schedule.name}</td>
                <td>{schedule.browser}</td>
                <td>{frequencyText(schedule)}</td>
                <td>{schedule.enabled ? formatDateTime(schedule.next_run_at, "-") : "-"}</td>
                <td>{formatDateTime(schedule.last_run_at, "-")}</td>
                <td><ScheduleStatusBadge enabled={schedule.enabled} /></td>
                <td>
                  <div className="home-schedule-actions">
                    <button type="button" className="home-file-edit" disabled={Boolean(busy)} onClick={() => onRunNow(schedule)} aria-label={`Run ${schedule.name} now`}>
                      {busy === "run" ? "Starting..." : "Run Now"}
                    </button>
                    <button type="button" className="home-file-edit" disabled={Boolean(busy)} onClick={() => onToggle(schedule)} aria-label={`${schedule.enabled ? "Disable" : "Enable"} ${schedule.name}`}>
                      {busy === "toggle" ? "Working..." : schedule.enabled ? "Disable" : "Enable"}
                    </button>
                    <button type="button" className="home-file-edit" disabled={Boolean(busy)} onClick={() => onEdit(schedule)} aria-label={`Edit ${schedule.name}`}>
                      Edit
                    </button>
                    <button type="button" className="home-file-edit is-danger" disabled={Boolean(busy)} onClick={() => onDelete(schedule)} aria-label={`Delete ${schedule.name}`}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
