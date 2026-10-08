import { Link } from "react-router-dom";

import RunStatusBadge from "./RunStatusBadge.jsx";
import { formatDateTime, formatSeconds } from "../utils/format.js";

// Counts can be null for runs that have no results yet; show a dash, not a 0.
const count = (value) => (value === null || value === undefined ? "-" : value);

/* One row per run, straight from the backend's list item. */
export default function RunHistoryTable({ projectId, runs }) {
  return (
    <div className="home-table-wrap">
      <table className="home-table home-history-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Status</th>
            <th>Browser</th>
            <th>Created</th>
            <th>Started</th>
            <th>Completed</th>
            <th>Duration</th>
            <th>Tests</th>
            <th>Passed</th>
            <th>Failed</th>
            <th>Skipped</th>
            <th>Screenshots</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td><Link to={`/projects/${projectId}/runs/${run.id}`}>#{run.id}</Link></td>
              <td><RunStatusBadge status={run.status} /></td>
              <td>{run.browser}</td>
              <td>{formatDateTime(run.created_at, "-")}</td>
              <td>{formatDateTime(run.started_at, "-")}</td>
              <td>{formatDateTime(run.completed_at, "-")}</td>
              <td>{formatSeconds(run.duration_seconds) ?? "-"}</td>
              <td>{count(run.test_count)}</td>
              <td>{count(run.passed_count)}</td>
              <td>{count(run.failed_count)}</td>
              <td>{count(run.skipped_count)}</td>
              <td>{count(run.screenshot_count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
