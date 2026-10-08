import { ApiError, authedFetch } from "./authedFetch.js";

/*
 * Schedules API - contract taken from the backend's live OpenAPI schema and
 * from probing the running server:
 *
 *   GET    /projects/{pid}/schedules                    -> ScheduleResponse[]
 *   POST   /projects/{pid}/schedules                    -> 201 ScheduleResponse
 *   GET    /projects/{pid}/schedules/{sid}              -> ScheduleResponse
 *   PATCH  /projects/{pid}/schedules/{sid}              -> ScheduleResponse (partial body)
 *   DELETE /projects/{pid}/schedules/{sid}              -> 204 (runs are NOT deleted)
 *   POST   /projects/{pid}/schedules/{sid}/enable       -> ScheduleResponse
 *   POST   /projects/{pid}/schedules/{sid}/disable      -> ScheduleResponse
 *   POST   /projects/{pid}/schedules/{sid}/run-now      -> 201 RunResponse (a normal run)
 *
 * ScheduleResponse: id, project_id, name, browser, frequency, interval_value,
 *   start_at, next_run_at, last_run_at, enabled, created_at, updated_at
 *   (all timestamps are naive UTC).
 *
 * frequency is one of FREQUENCIES (exact, upper-case). interval_value is in
 * MINUTES and is required (>0) only for INTERVAL, forbidden otherwise.
 * 409 on run-now means this schedule already has an active run.
 */
export const FREQUENCIES = ["ONCE", "INTERVAL", "HOURLY", "DAILY", "WEEKLY"];

const base = (projectId) => `/projects/${projectId}/schedules`;

// The backend returns a list; anything else is a malformed response.
export async function listSchedules(projectId, signal) {
  const data = await authedFetch(base(projectId), { signal });
  if (!Array.isArray(data)) {
    throw new ApiError("The backend returned an unexpected response.", 500, null);
  }
  return data;
}

export function getSchedule(projectId, scheduleId, signal) {
  return authedFetch(`${base(projectId)}/${scheduleId}`, { signal });
}

// body: { name, browser, frequency, interval_value?, start_at, enabled? }
export function createSchedule(projectId, body) {
  return authedFetch(base(projectId), { method: "POST", json: body });
}

// body: any subset of { name, browser, frequency, interval_value, start_at, enabled }
export function updateSchedule(projectId, scheduleId, body) {
  return authedFetch(`${base(projectId)}/${scheduleId}`, { method: "PATCH", json: body });
}

export function deleteSchedule(projectId, scheduleId) {
  return authedFetch(`${base(projectId)}/${scheduleId}`, { method: "DELETE" });
}

export function enableSchedule(projectId, scheduleId) {
  return authedFetch(`${base(projectId)}/${scheduleId}/enable`, { method: "POST" });
}

export function disableSchedule(projectId, scheduleId) {
  return authedFetch(`${base(projectId)}/${scheduleId}/disable`, { method: "POST" });
}

// Returns the RunResponse of the new run (already started by the backend).
export function runScheduleNow(projectId, scheduleId) {
  return authedFetch(`${base(projectId)}/${scheduleId}/run-now`, { method: "POST" });
}
