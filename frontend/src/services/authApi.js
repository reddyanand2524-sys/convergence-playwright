import { authedFetch } from "./authedFetch.js";

// GET /auth/me
export function getMe(signal) {
  return authedFetch("/auth/me", { signal });
}
