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
export async function getRuns(
  projectId,
  { page = 1, pageSize = 20, status, browser, runId, createdFrom, createdTo, search } = {},
  signal
) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const optional = {
    status,
    browser,
    run_id: runId,
    created_from: createdFrom,
    created_to: createdTo,
    search,
  };
  Object.entries(optional).forEach(([key, value]) => {
    const text = value === undefined || value === null ? "" : String(value).trim();
    if (text) query.set(key, text);
  });

  const data = await authedFetch(`/projects/${projectId}/runs?${query}`, { signal });

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

// GET /projects/{project_id}/runs/{run_id}
export function getRun(projectId, runId, signal) {
  return authedFetch(`/projects/${projectId}/runs/${runId}`, { signal });
}

// POST /projects/{project_id}/runs/{run_id}/start
// No request body: the backend uses the run's own browser snapshot.
// Returns the RunResponse (normally RUNNING); execution continues on the
// backend, so this does NOT mean the run has finished.
export async function startRun(projectId, runId) {
  try {
    return await authedFetch(`/projects/${projectId}/runs/${runId}/start`, {
      method: "POST",
    });
  } catch (err) {
    if (err?.status === 409) {
      throw new ApiError(
        "Run state has changed. Refreshing the latest status.",
        409,
        err.detail
      );
    }
    throw err;
  }
}

// POST /projects/{project_id}/runs/{run_id}/retry   (no body, 201)
// Creates a NEW QUEUED run from a COMPLETED/FAILED one. It is not started,
// and the original run is left untouched.
export async function retryRun(projectId, runId) {
  try {
    return await authedFetch(`/projects/${projectId}/runs/${runId}/retry`, {
      method: "POST",
    });
  } catch (err) {
    if (err?.status === 409) {
      throw new ApiError(
        "This run cannot be retried in its current state.",
        409,
        err.detail
      );
    }
    throw err;
  }
}

// POST /projects/{project_id}/runs/{run_id}/cancel   (no body)
// QUEUED -> returns CANCELLED. RUNNING -> may still return RUNNING with
// cancel_requested=true; the run only becomes CANCELLED once the backend has
// stopped it, so callers must keep polling.
export async function cancelRun(projectId, runId) {
  try {
    return await authedFetch(`/projects/${projectId}/runs/${runId}/cancel`, {
      method: "POST",
    });
  } catch (err) {
    if (err?.status === 409) {
      throw new ApiError(
        "Run state has changed. Refreshing the latest status.",
        409,
        err.detail
      );
    }
    throw err;
  }
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

// ---- Reports -------------------------------------------------------------
// GET /projects/{project_id}/runs/{run_id}/report/{html|excel|pdf}
// Only COMPLETED/FAILED runs have reports (409 otherwise). Fetched with the
// Authorization header and returned as a Blob; the token is never put in a URL.
export const REPORT_FORMATS = {
  html: { label: "HTML", ext: "html", mime: "text/html" },
  excel: { label: "Excel", ext: "xlsx", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
  pdf: { label: "PDF", ext: "pdf", mime: "application/pdf" },
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
