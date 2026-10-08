import { useOutletContext } from "react-router-dom";
import { FlaskConical, Plus } from "lucide-react";
import LoadingState from "../components/brand/LoadingState.jsx";

import ProjectRows from "../components/ProjectRows.jsx";

export default function TestsPage() {
  const { projects, reloadProjects, openCreateProject } = useOutletContext();

  return (
    <section className="home-subpage">
      <span className="home-eyebrow">Workspace</span>
      <h2>Tests</h2>
      <p className="home-page-description">
        Your test workspace is ready for its next project.
      </p>

      {projects.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading projects…" />
        </div>
      )}

      {projects.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load projects</h3>
          <p>{projects.error}</p>
          <button
            className="home-secondary-button"
            type="button"
            onClick={() => reloadProjects()}
          >
            Try again
          </button>
        </div>
      )}

      {projects.status === "ready" && projects.items.length === 0 && (
        <div className="home-empty-state">
          <span className="home-empty-icon">
            <FlaskConical size={25} aria-hidden="true" />
          </span>
          <h3>No projects yet</h3>
          <p>Create a project to get started.</p>
          <button
            className="home-primary-button"
            type="button"
            onClick={openCreateProject}
          >
            <Plus size={18} aria-hidden="true" />
            Create Project
          </button>
        </div>
      )}

      {projects.status === "ready" && projects.items.length > 0 && (
        <ProjectRows projects={projects.items} large />
      )}
    </section>
  );
}
