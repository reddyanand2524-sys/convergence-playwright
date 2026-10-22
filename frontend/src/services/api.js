import { ApiError, authedFetch } from "./authedFetch.js";

/*
 * Public (unauthenticated) auth calls. They go through the shared authedFetch
 * with auth:false, so they get the same base-URL normalisation, network-failure
 * handling (ApiError.status === 0) and FastAPI error parsing - including 422
 * arrays - as every other API call. No token is sent and a 401 here (e.g. wrong
 * password) never clears an existing session. Messages are built from the
 * backend's "detail" only; request bodies are never echoed.
 */

const BACKEND_HINT = "Make sure the FastAPI backend is running.";

// `request` is injectable for tests.
export async function publicRequest(path, action, options, request = authedFetch) {
  let data;

  try {
    data = await request(path, { ...options, auth: false });
  } catch (err) {
    if (err instanceof ApiError && err.status > 0 && (err.detail === undefined || err.detail === null)) {
      // The reply had no FastAPI "detail": a proxy error page, or an empty body.
      throw new ApiError(
        err.status >= 500
          ? `${action} failed: the backend returned an invalid response (HTTP ${err.status}). ${BACKEND_HINT}`
          : `${action} failed (HTTP ${err.status}).`,
        err.status,
        err.detail
      );
    }
    throw err;
  }

  if (data === null || typeof data !== "object") {
    throw new ApiError(
      `${action} failed: the backend returned an empty or invalid response. ${BACKEND_HINT}`,
      502,
      null
    );
  }

  return data;
}


// ============================================================
// AUTHENTICATION
// ============================================================

// REGISTER
export function registerUser(data) {
  return publicRequest("/auth/register", "Registration", {
    method: "POST",
    json: data,
  });
}


// LOGIN
export function loginUser(email, password, signal) {
  return publicRequest("/auth/login", "Login", {
    method: "POST",
    json: { email, password },
    signal,
  });
}


// FORGOT PASSWORD
// The backend takes the email as a query parameter; that contract is unchanged.
export function forgotPassword(email) {
  return publicRequest(
    `/auth/forgot-password?email=${encodeURIComponent(email)}`,
    "Forgot password",
    { method: "POST" }
  );
}


// RESET PASSWORD
export function resetPassword(token, newPassword, confirmPassword) {
  return publicRequest("/auth/reset-password", "Reset password", {
    method: "POST",
    json: {
      token,
      new_password: newPassword,
      confirm_password: confirmPassword,
    },
  });
}
