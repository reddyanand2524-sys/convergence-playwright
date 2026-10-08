import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import RunHistoryTable from "../../components/RunHistoryTable.jsx";
import RunHistoryFilters, { EMPTY_FILTERS } from "../../components/RunHistoryFilters.jsx";
import Pagination from "../../components/Pagination.jsx";
import { getRuns } from "../../services/runsApi.js";
import { describeError } from "../../services/authedFetch.js";

const PAGE_SIZE = 20; // backend default
const FILTER_KEYS = Object.keys(EMPTY_FILTERS); // search, status, browser, run_id, created_from, created_to

function pageFromParams(params) {
  const n = Number.parseInt(params.get("page") || "1", 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

/*
 * /projects/:projectId/history?page=N&status=...&browser=...&run_id=...
 *                              &created_from=...&created_to=...&search=...
 * The URL is the single source of truth for page and filters, so refresh, Back,
 * Forward and shared links all restore the same view. Every change is a new
 * backend request; nothing is filtered or paged in the browser.
 */
export default function RunHistoryPage() {
  const { project } = useOutletContext();
  const [params, setParams] = useSearchParams();
  const page = pageFromParams(params);

  const filters = useMemo(() => {
    const f = { ...EMPTY_FILTERS };
    FILTER_KEYS.forEach((key) => {
      f[key] = (params.get(key) || "").trim();
    });
    return f;
  }, [params]);
  const filtersKey = JSON.stringify(filters);
  const hasFilters = FILTER_KEYS.some((key) => filters[key]);

  // status: "loading" | "ready" | "error"
  const [state, setState] = useState({ status: "loading", data: null, error: "", code: 0 });
  const [reloadKey, setReloadKey] = useState(0);
  const loadedFilters = useRef(null); // filters the rows on screen belong to

  const load = useCallback(
    async (signal) => {
      // Rows from different filters are dropped at once; only a plain page change
      // keeps the previous rows (dimmed) while the next page loads.
      setState((prev) => ({
        status: "loading",
        data: loadedFilters.current === filtersKey ? prev.data : null,
        error: "",
        code: 0,
      }));
      try {
        const data = await getRuns(
          project.id,
          {
            page,
            pageSize: PAGE_SIZE,
            status: filters.status,
            browser: filters.browser,
            runId: filters.run_id,
            createdFrom: filters.created_from,
            createdTo: filters.created_to,
            search: filters.search,
          },
          signal
        );
        loadedFilters.current = filtersKey;
        setState({ status: "ready", data, error: "", code: 0 });
      } catch (err) {
        if (err?.name === "AbortError") return;
        loadedFilters.current = null;
        setState({
          status: "error",
          data: null,
          error: describeError(err, "Project not found."),
          code: err?.status ?? 0,
        });
      }
    },
    // filtersKey stands in for the individual filter values
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [project.id, page, filtersKey]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, reloadKey]);

  // A page past the end (e.g. a stale link): jump to the last real page,
  // keeping every active filter.
  const data = state.data;
  useEffect(() => {
    if (state.status === "ready" && data.items.length === 0 && data.total > 0 && page > data.total_pages) {
      const next = new URLSearchParams(params);
      next.set("page", String(Math.max(data.total_pages, 1)));
      setParams(next, { replace: true });
    }
  }, [state.status, data, page, params, setParams]);

  function buildParams(nextPage, nextFilters) {
    const next = new URLSearchParams();
    next.set("page", String(nextPage));
    FILTER_KEYS.forEach((key) => {
      const value = (nextFilters[key] || "").trim();
      if (value) next.set(key, value); // empty filters never reach the URL
    });
    return next;
  }

  const goTo = (nextPage) => setParams(buildParams(nextPage, filters));
  const applyFilters = (next) => setParams(buildParams(1, next)); // new filters => page 1
  const clearFilters = () => setParams(buildParams(1, EMPTY_FILTERS));

  const loading = state.status === "loading";

  return (
    <div className="home-runs">
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Runs</span>
          <h3>Run history</h3>
        </div>

        <button
          className="home-secondary-button"
          type="button"
          disabled={loading}
          onClick={() => setReloadKey((n) => n + 1)}
        >
          <RefreshCw size={16} aria-hidden="true" />
          Refresh
        </button>
      </div>

      <RunHistoryFilters
        key={filtersKey}
        applied={filters}
        busy={loading}
        onApply={applyFilters}
        onClear={clearFilters}
      />

      {loading && !data && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading run history..." />
        </div>
      )}

      {state.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>{state.code === 422 ? "These filters could not be applied" : "Could not load run history"}</h3>
          <p>{state.error}</p>
          <div className="home-empty-actions">
            {state.code === 422 && hasFilters ? (
              <button className="home-secondary-button" type="button" onClick={clearFilters}>
                Clear Filters
              </button>
            ) : (
              <button className="home-secondary-button" type="button" onClick={() => setReloadKey((n) => n + 1)}>
                Try again
              </button>
            )}
          </div>
        </div>
      )}

      {state.status !== "error" && data && data.total === 0 && (
        <div className="home-empty-state">
          {hasFilters ? (
            <>
              <h3>No runs match the selected filters.</h3>
              <p>Try different filters, or clear them to see all runs.</p>
              <button className="home-secondary-button" type="button" onClick={clearFilters}>
                Clear Filters
              </button>
            </>
          ) : (
            <>
              <h3>No runs yet</h3>
              <p>Runs you create for this project will appear here.</p>
              <Link className="home-primary-button" to={`/projects/${project.id}/run`}>
                Run Test
              </Link>
            </>
          )}
        </div>
      )}

      {state.status !== "error" && data && data.items.length > 0 && (
        <div className={loading ? "home-history-busy" : undefined} aria-busy={loading}>
          <RunHistoryTable projectId={project.id} runs={data.items} />
        </div>
      )}

      {state.status !== "error" && data && data.total > 0 && (
        <Pagination
          page={data.page}
          pageSize={data.page_size}
          total={data.total}
          totalPages={data.total_pages}
          disabled={loading}
          onChange={goTo}
        />
      )}
    </div>
  );
}
