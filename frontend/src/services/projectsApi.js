import { authedFetch } from "./authedFetch.js";

// GET /projects
export function listProjects(signal) {
  return authedFetch("/projects", { signal });
}

// POST /projects
// Backend CreateProjectRequest: name (2-150), frequency (1-50), browser (1-30).
// All three are required and accepted by the current backend.
export function createProject({ name, frequency, browser }) {
  return authedFetch("/projects", {
    method: "POST",
    json: { name, frequency, browser },
  });
}

// GET /projects/{project_id}
export function getProject(projectId, signal) {
  return authedFetch(`/projects/${projectId}`, { signal });
}

// PATCH /projects/{project_id}  (partial: name, frequency, browser)
export function updateProject(projectId, payload) {
  return authedFetch(`/projects/${projectId}`, {
    method: "PATCH",
    json: payload,
  });
}
