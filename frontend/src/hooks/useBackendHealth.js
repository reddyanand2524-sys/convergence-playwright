import { useEffect, useState } from "react";

import { getHealth } from "../services/healthApi.js";

const HEALTH_POLL_MS = 30000;

/* "checking" | "healthy" | "unhealthy" | "unreachable" - polls GET /health. */
export default function useBackendHealth() {
  const [health, setHealth] = useState("checking");

  useEffect(() => {
    let cancelled = false;
    let controller = new AbortController();

    async function check() {
      controller = new AbortController();
      try {
        const result = await getHealth(controller.signal);
        if (!cancelled) {
          setHealth(result?.status === "healthy" ? "healthy" : "unhealthy");
        }
      } catch (err) {
        if (cancelled || err?.name === "AbortError") return;
        setHealth(err?.status === 0 ? "unreachable" : "unhealthy");
      }
    }

    check();
    const timer = setInterval(check, HEALTH_POLL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  return health;
}
