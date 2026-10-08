import { authedFetch } from "./authedFetch.js";

// GET /health (public endpoint, no token needed)
export function getHealth(signal) {
  return authedFetch("/health", { auth: false, signal });
}
