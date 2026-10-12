/*
 * Shared authenticated API helper.
 *
 * Reuses the access token that LoginPage already stores in
 * localStorage ("access_token"). No second token store exists.
 *
 * VITE_API_BASE_URL already contains "/api", so callers pass paths
 * such as "/projects" - never "/api/projects".
 */

const TOKEN_KEY = "access_token";
const USER_KEY = "current_user";

export const UNAUTHORIZED_EVENT = "auth:unauthorized";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    // Raw FastAPI "detail" (string or 422 array) for field-level mapping
    this.detail = detail;
    // 0 = the request never reached the backend (network failure)
    this.status = status;
  }
}

function baseUrl() {
  const base = import.meta.env.VITE_API_BASE_URL;

  if (!base) {
    throw new ApiError("VITE_API_BASE_URL is not configured.", 0);
  }

  return base.replace(/\/+$/, "");
}

function detailToMessage(detail, fallback) {
  if (typeof detail === "string") return detail;

  // FastAPI 422: [{ loc, msg, type }, ...]
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => item?.msg)
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }

  return fallback;
}

export function getAccessToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

/**
 * @param {string} path  e.g. "/projects"
 * @param {{ method?: string, json?: unknown, formData?: FormData,
 *           signal?: AbortSignal, auth?: boolean }} options
 */
export async function authedFetch(path, options = {}) {
  const { method = "GET", json, formData, signal, auth = true, responseType } = options;

  const headers = {};

  if (auth) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let body;

  if (formData) {
    // Browser sets the multipart boundary itself.
    body = formData;
  } else if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }

  let response;

  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers,
      body,
      signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new ApiError(
      "Cannot reach the backend. Check that the server is running and your network connection.",
      0
    );
  }

  // Binary success responses (e.g. screenshots) bypass JSON parsing.
  if (response.ok && responseType === "blob") return response.blob();

  // File downloads: the blob plus what the server said about it. Content-
  // Disposition is only readable cross-origin if the backend exposes it, so
  // filename may be null (callers then build their own).
  if (response.ok && responseType === "download") {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    return {
      blob: await response.blob(),
      contentType: response.headers.get("Content-Type") || "",
      filename: match ? decodeURIComponent(match[1]) : null,
    };
  }

  const text = await response.text();
  let data = null;

  if (text.trim()) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && auth) {
      clearSession();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }

    throw new ApiError(
      detailToMessage(
        data?.detail,
        `Request failed (HTTP ${response.status}).`
      ),
      response.status,
      data?.detail
    );
  }

  return data;
}

/**
 * Authenticated streaming GET (Server-Sent Events). Same bearer token and error
 * conventions as authedFetch, but the successful Response is returned unread so the
 * caller can consume response.body incrementally. The token is only ever sent in
 * the Authorization header, never in the URL.
 */
export async function authedStream(path, { signal, headers: extraHeaders = {} } = {}) {
  const headers = { Accept: "text/event-stream", ...extraHeaders };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;

  try {
    response = await fetch(`${baseUrl()}${path}`, { method: "GET", headers, signal });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new ApiError(
      "Cannot reach the backend. Check that the server is running and your network connection.",
      0
    );
  }

  if (!response.ok) {
    let data = null;
    try {
      data = JSON.parse(await response.text());
    } catch {
      data = null;
    }

    if (response.status === 401) {
      clearSession();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }

    throw new ApiError(
      detailToMessage(data?.detail, `Request failed (HTTP ${response.status}).`),
      response.status,
      data?.detail
    );
  }

  return response;
}

/** User-facing text for an error from authedFetch. */
export function describeError(err, notFoundMessage = "Not found.") {
  if (err?.status === 0) return err.message;
  if (err?.status === 401) return "Your session has expired. Please sign in again.";
  if (err?.status === 404) return notFoundMessage;
  return err?.message || "Something went wrong.";
}
