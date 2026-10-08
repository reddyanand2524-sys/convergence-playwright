import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft, FileCode2, CalendarClock, History, Play, Settings2 } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import { getProject } from "../../services/projectsApi.js";
import { describeError } from "../../services/authedFetch.js";

/*
 * /projects/:projectId
 * The project is loaded from GET /projects/{projectId}; the backend is the
 * source of truth. State is keyed to projectId, so switching projects never
 * shows the previous project's data.
 * Only Configuration and Files exist; Run/Results/History come later.
 */
export default function ProjectWorkspace() {
  const { projectId } = useParams();
  const shell = useOutletContext();
  const { pathname } = useLocation();

  // status: "loading" | "ready" | "notfound" | "error"
  const [state, setState] = useState({ projectId, status: "loading", project: null, error: "" });

  const load = useCallback(
    async (signal) => {
      setState({ projectId, status: "loading", project: null, error: "" });
      try {
        const project = await getProject(projectId, signal);
        setState({ projectId, status: "ready", project, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({
          projectId,
          status: err?.status === 404 ? "notfound" : "error",
          project: null,
          error: describeError(err),
        });
      }
    },
    [projectId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function setProject(project) {
    setState((prev) => ({ ...prev, project }));
  }

  // First render after a projectId change, before the effect runs.
  const isStale = state.projectId !== projectId;

  if (isStale || state.status === "loading") {
    return (
      <div className="home-empty-state" role="status">
        <LoadingState type="flow" label="Loading project..." />
      </div>
    );
  }

  if (state.status === "notfound") {
    return (
      <div className="home-empty-state" role="alert">
        <h3>Project not found</h3>
        <p>This project does not exist or you do not have access to it.</p>
        <Link className="home-secondary-button" to="/tests">
          Back to Projects
        </Link>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="home-empty-state is-error" role="alert">
        <h3>Could not load project</h3>
        <p>{state.error}</p>
        <button className="home-secondary-button" type="button" onClick={() => load()}>
          Try again
        </button>
      </div>
    );
  }

  const { project } = state;

  return (
    <section className="home-subpage">
      <Link className="home-back-link" to="/tests">
        <ArrowLeft size={16} aria-hidden="true" />
        Back to Projects
      </Link>

      <span className="home-eyebrow">Project</span>
      <h2>{project.name}</h2>
      <p className="home-page-description">
        {project.browser} · {project.frequency}
      </p>

      <nav className="home-tabs" aria-label="Project sections">
        <NavLink to={`/projects/${project.id}/configuration`} className="home-tab">
          <Settings2 size={16} aria-hidden="true" />
          Configuration
        </NavLink>
        <NavLink to={`/projects/${project.id}/files`} className="home-tab">
          <FileCode2 size={16} aria-hidden="true" />
          Files
        </NavLink>
        <NavLink
          to={`/projects/${project.id}/run`}
          className={({ isActive }) =>
            `home-tab${isActive || pathname.startsWith(`/projects/${project.id}/runs/`) ? " active" : ""}`
          }
        >
          <Play size={16} aria-hidden="true" />
          Run Test
        </NavLink>
        <NavLink to={`/projects/${project.id}/history`} className="home-tab">
          <History size={16} aria-hidden="true" />
          History
        </NavLink>
        <NavLink to={`/projects/${project.id}/schedules`} className="home-tab">
          <CalendarClock size={16} aria-hidden="true" />
          Schedules
        </NavLink>
      </nav>

      <Outlet context={{ ...shell, project, setProject }} />
    </section>
  );
}

export function ProjectIndexRedirect() {
  const { projectId } = useParams();
  return <Navigate to={`/projects/${projectId}/configuration`} replace />;
}
