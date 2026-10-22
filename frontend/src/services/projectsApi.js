import { ApiError, authedFetch } from "./authedFetch.js";
import { buildCreatePayload } from "../utils/projectForm.js";

// The backend returns the projects as a bare JSON array (possibly empty). Anything else is a
// malformed reply and is reported, not shown as "no projects".
export function normalizeProjectList(data) {
  if (!Array.isArray(data)) {
    throw new ApiError("The backend returned an unexpected response.", 500, null);
  }
  return data;
}

// GET /projects
export async function listProjects(signal) {
  return normalizeProjectList(await authedFetch("/projects", { signal }));
}

// POST /projects
// Backend CreateProjectRequest: name (2-150), frequency (1-50), browser (1-30) and an
// optional framework ("selenium" | "playwright"; the backend defaults to Selenium when it
// is left out). Playwright projects use the browser "chrome". The backend may refuse with
// 403 / 409 / 422 and a "detail" message, which the caller shows as is.
export function createProject({ name, frequency, browser, framework }) {
  return authedFetch("/projects", {
    method: "POST",
    json: buildCreatePayload({ name, frequency, browser, framework }),
  });
}

// GET /projects/{project_id}
export function getProject(projectId, signal) {
  return authedFetch(`/projects/${projectId}`, { signal });
}

// PATCH /projects/{project_id}  (partial: name, frequency, browser, framework)
// 409 when a framework change is refused (enabled schedules, queued/running runs).
export function updateProject(projectId, payload) {
  return authedFetch(`/projects/${projectId}`, {
    method: "PATCH",
    json: payload,
  });
}

// DELETE /projects/{project_id}  -> 204
// Permanently deletes the project and its files, runs, results, screenshots,
// diagnostics and schedules. 409 if a run is currently in progress.
export function deleteProject(projectId) {
  return authedFetch(`/projects/${projectId}`, { method: "DELETE" });
}
