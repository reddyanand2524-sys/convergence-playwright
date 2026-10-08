import { authedFetch } from "./authedFetch.js";

// GET /projects/{project_id}/files  (metadata only)
export function listProjectFiles(projectId, signal) {
  return authedFetch(`/projects/${projectId}/files`, { signal });
}

// POST /projects/{project_id}/files  (multipart, repeated "files" field)
export function uploadProjectFiles(projectId, files) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));

  return authedFetch(`/projects/${projectId}/files`, {
    method: "POST",
    formData,
  });
}

// PATCH /projects/{project_id}/files/{file_id}  (replaces file_content)
export function updateProjectFile(projectId, fileId, fileContent) {
  return authedFetch(`/projects/${projectId}/files/${fileId}`, {
    method: "PATCH",
    json: { file_content: fileContent },
  });
}

// DELETE /projects/{project_id}/files/{file_id}  (204, no body)
export function deleteProjectFile(projectId, fileId) {
  return authedFetch(`/projects/${projectId}/files/${fileId}`, {
    method: "DELETE",
  });
}

// GET /projects/{project_id}/files/{file_id}  (includes file_content)
export function getProjectFile(projectId, fileId, signal) {
  return authedFetch(`/projects/${projectId}/files/${fileId}`, { signal });
}
