import { ApiError, authedFetch } from "./authedFetch.js";

// POST /projects/{project_id}/runs
// No request body: the backend snapshots the project's current browser.
export function createRun(projectId) {
  return authedFetch(`/projects/${projectId}/runs`, { method: "POST" });
}

// GET /projects/{project_id}/runs?page=&page_size=[&status&browser&run_id&created_from&created_to&search]
// Returns the paginated envelope { items, page, page_size, total, total_pages },
// newest first (ordering and all filtering are done by the backend). Empty
// filters are omitted from the query. A bare array (old contract) is wrapped so
// callers always see one shape; anything else is rejected.
// Also filterable by framework ("selenium" | "playwright"); omitted when empty, like the others.
export function buildRunsQuery({
  page = 1,
  pageSize = 20,
  status,
  browser,
  framework,
  runId,
  createdFrom,
  createdTo,
  search,
} = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const optional = {
    status,
    browser,
    framework,
    run_id: runId,
    created_from: createdFrom,
    created_to: createdTo,
    search,
  };
  Object.entries(optional).forEach(([key, value]) => {
    const text = value === undefined || value === null ? "" : String(value).trim();
    if (text) query.set(key, text);
  });
  return query;
}

// Normalizes the list reply to { items, page, page_size, total, total_pages }. `page` and
// `pageSize` are the values that were requested, used when the reply omits its own.
export function normalizeRunsPage(data, page = 1, pageSize = 20) {
  if (Array.isArray(data)) {
    return { items: data, page: 1, page_size: data.length, total: data.length, total_pages: 1 };
  }

  if (data && Array.isArray(data.items)) {
    const num = (value, fallback) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
    return {
      items: data.items,
      page: num(data.page, page),
      page_size: num(data.page_size, pageSize),
      total: num(data.total, data.items.length),
      total_pages: num(data.total_pages, 1),
    };
  }

  throw new ApiError("The backend returned an unexpected response.", 500, null);
}

export async function getRuns(projectId, options = {}, signal) {
  const { page = 1, pageSize = 20 } = options;
  const query = buildRunsQuery(options);

  const data = await authedFetch(`/projects/${projectId}/runs?${query}`, { signal });
  return normalizeRunsPage(data, page, pageSize);
}

// GET /projects/{project_id}/runs/{run_id}
export function getRun(projectId, runId, signal) {
  return authedFetch(`/projects/${projectId}/runs/${runId}`, { signal });
}

// POST /projects/{project_id}/runs/{run_id}/start
// No request body: the backend uses the run's own browser snapshot.
// Returns the RunResponse (normally RUNNING); execution continues on the
// backend, so this does NOT mean the run has finished.
// A 409 keeps the backend's own message: it can mean the run was already started or
// cancelled, or that the framework's runtime is unavailable. Callers re-fetch the run
// to tell which (see runStateChanged).
export function startRun(projectId, runId) {
  return authedFetch(`/projects/${projectId}/runs/${runId}/start`, {
    method: "POST",
  });
}

// POST /projects/{project_id}/runs/{run_id}/retry   (no body, 201)
// Creates a NEW QUEUED run from a COMPLETED/FAILED one. It is not started,
// and the original run is left untouched.
// A 409 keeps the backend's own message (wrong state, unavailable runtime, ...).
export function retryRun(projectId, runId) {
  return authedFetch(`/projects/${projectId}/runs/${runId}/retry`, {
    method: "POST",
  });
}

// POST /projects/{project_id}/runs/{run_id}/cancel   (no body)
// QUEUED -> returns CANCELLED. RUNNING -> may still return RUNNING with
// cancel_requested=true; the run only becomes CANCELLED once the backend has
// stopped it, so callers must keep polling.
export function cancelRun(projectId, runId) {
  return authedFetch(`/projects/${projectId}/runs/${runId}/cancel`, {
    method: "POST",
  });
}

// GET /projects/{project_id}/runs/{run_id}/results
// summary and tests are null until the run is COMPLETED or FAILED.
export function getRunResults(projectId, runId, signal) {
  return authedFetch(`/projects/${projectId}/runs/${runId}/results`, { signal });
}

// GET /projects/{project_id}/runs/{run_id}/screenshots/{screenshot_id}
// Fetched with the Authorization header and returned as a Blob; an <img src>
// pointing at this endpoint could not send the token.
export function getScreenshotBlob(projectId, runId, screenshotId, signal) {
  return authedFetch(
    `/projects/${projectId}/runs/${runId}/screenshots/${screenshotId}`,
    { signal, responseType: "blob" }
  );
}

// GET /projects/{project_id}/runs/{run_id}/report/json
// The structured JSON execution report. 404 = no report for this run (e.g. it
// predates reporting); 409 = run not COMPLETED/FAILED.
export function getExecutionReport(projectId, runId, signal) {
  return authedFetch(`/projects/${projectId}/runs/${runId}/report/json`, { signal });
}

// GET /projects/{project_id}/runs/{run_id}/artifacts/screenshots/{file_name}
// Resolves the "screenshots/<file_name>" paths used inside the JSON report.
export function getScreenshotArtifactBlob(projectId, runId, fileName, signal) {
  return authedFetch(
    `/projects/${projectId}/runs/${runId}/artifacts/screenshots/${encodeURIComponent(fileName)}`,
    { signal, responseType: "blob" }
  );
}

// ---- Reports -------------------------------------------------------------
// GET /projects/{project_id}/runs/{run_id}/report/{html|excel|pdf}
// Only COMPLETED/FAILED runs have reports (409 otherwise). Fetched with the
// Authorization header and returned as a Blob; the token is never put in a URL.
export const REPORT_FORMATS = {
  html: { label: "HTML", ext: "html", mime: "text/html" },
  excel: { label: "Excel", ext: "xlsx", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
  pdf: { label: "PDF", ext: "pdf", mime: "application/pdf" },
  json: { label: "JSON", ext: "json", mime: "application/json" },
};

async function downloadReport(projectId, runId, format) {
  const spec = REPORT_FORMATS[format];

  let result;
  try {
    result = await authedFetch(`/projects/${projectId}/runs/${runId}/report/${format}`, {
      responseType: "download",
    });
  } catch (err) {
    if (err?.status === 409) {
      throw new ApiError("Reports are available only for completed or failed runs.", 409, err.detail);
    }
    if (err?.status >= 500) {
      throw new ApiError(`The ${spec.label} report could not be generated. Please try again.`, err.status, err.detail);
    }
    throw err;
  }

  if (!result?.blob || result.blob.size === 0) {
    throw new ApiError(`The server returned an empty ${spec.label} report.`, 502, null);
  }

  // A JSON/HTML error page where a file was expected is not a report.
  if (format !== "html" && result.contentType && !result.contentType.toLowerCase().startsWith(spec.mime)) {
    throw new ApiError(`The server returned an unexpected response for the ${spec.label} report.`, 502, null);
  }

  return {
    blob: result.blob,
    // Prefer the server's name; fall back to the documented one for this run.
    filename: result.filename || `Convergence_Run_${runId}.${spec.ext}`,
  };
}

export const downloadHtmlReport = (projectId, runId) => downloadReport(projectId, runId, "html");
export const downloadExcelReport = (projectId, runId) => downloadReport(projectId, runId, "excel");
export const downloadPdfReport = (projectId, runId) => downloadReport(projectId, runId, "pdf");
export const downloadJsonReport = (projectId, runId) => downloadReport(projectId, runId, "json");
