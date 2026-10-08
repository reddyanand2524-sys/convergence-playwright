import { useEffect, useState } from "react";

import { getRuns } from "../services/runsApi.js";
import { describeError } from "../services/authedFetch.js";
import { parseServerDate } from "../utils/format.js";

const PAGE_SIZE = 100; // backend maximum

/*
 * Aggregates real run history (GET /projects/{id}/runs) across projects.
 * Nothing here is invented: every figure is derived from fields the backend
 * returns. Status counts use the latest PAGE_SIZE runs per project;
 * `truncated` tells the UI when older runs were left out.
 */
export function summarize(projectRuns) {
  const runs = projectRuns.flatMap(({ project, page }) =>
    page.items.map((run) => ({ ...run, projectName: project.name }))
  );
  const totalRuns = projectRuns.reduce((sum, { page }) => sum + page.total, 0);
  const truncated = projectRuns.some(({ page }) => page.total > page.items.length);

  const count = (status) => runs.filter((run) => run.status === status).length;
  const finished = runs.filter((run) => run.status === "COMPLETED" || run.status === "FAILED");
  const tests = finished.reduce((sum, run) => sum + (run.test_count || 0), 0);
  const passed = finished.reduce((sum, run) => sum + (run.passed_count || 0), 0);
  const durations = runs.map((run) => run.duration_seconds).filter((value) => typeof value === "number");

  const latest = runs.reduce((best, run) => {
    const at = parseServerDate(run.created_at)?.getTime() ?? 0;
    return !best || at > best.at ? { run, at } : best;
  }, null);

  return {
    projects: projectRuns.length,
    totalRuns,
    truncated,
    completed: count("COMPLETED"),
    failed: count("FAILED"),
    running: count("RUNNING"),
    queued: count("QUEUED"),
    tests,
    passRate: tests > 0 ? (passed / tests) * 100 : null,
    avgDuration: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null,
    last: latest?.run || null,
  };
}

// state: { status: "idle" | "loading" | "ready" | "error", data, error }
export default function useWorkspaceMetrics(projects) {
  const [state, setState] = useState({ status: "idle", data: null, error: "" });
  const key = projects.map((project) => project.id).join(",");

  useEffect(() => {
    if (!projects.length) {
      setState({ status: "idle", data: null, error: "" });
      return undefined;
    }

    const controller = new AbortController();
    setState((prev) => ({ ...prev, status: "loading", error: "" }));

    Promise.all(
      projects.map((project) =>
        getRuns(project.id, { page: 1, pageSize: PAGE_SIZE }, controller.signal).then((page) => ({ project, page }))
      )
    )
      .then((projectRuns) => setState({ status: "ready", data: summarize(projectRuns), error: "" }))
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({ status: "error", data: null, error: describeError(err) });
      });

    return () => controller.abort();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  return state;
}
