import { Link, useOutletContext } from "react-router-dom";
import {
  ChevronRight,
  CircleUserRound,
  FlaskConical,
  FolderPlus,
  Plus,
} from "lucide-react";

import DashboardMetrics from "../components/DashboardMetrics.jsx";
import ProjectRows from "../components/ProjectRows.jsx";

const HEALTH_LABEL = {
  checking: "Checking backend…",
  healthy: "Backend connected",
  unhealthy: "Backend reports a problem",
  unreachable: "Backend unreachable",
};

export default function DashboardPage() {
  const { user, projects, health, openCreateProject } = useOutletContext();

  const name = String(user.full_name || user.name || "Frontend User");
  const firstName = name.trim().split(/\s+/)[0] || "there";

  return (
    <>
      <section className="home-welcome">
        <div>
          <span className="home-eyebrow">Workspace overview</span>
          <h2>Welcome back, {firstName}.</h2>
          <p>Manage your projects and keep your work moving.</p>
        </div>

        <button
          className="home-secondary-button"
          type="button"
          onClick={openCreateProject}
        >
          <FolderPlus size={18} aria-hidden="true" />
          <span>New project</span>
        </button>
      </section>

      {projects.status === "ready" && <DashboardMetrics projects={projects.items} />}

      <section className="home-shortcuts" aria-label="Workspace shortcuts">
        <button className="home-shortcut" type="button" onClick={openCreateProject}>
          <span className="home-shortcut-icon is-blue">
            <Plus size={23} aria-hidden="true" />
          </span>
          <span className="home-shortcut-copy">
            <strong>Create a project</strong>
            <small>Start a new automation workspace.</small>
          </span>
          <ChevronRight size={19} aria-hidden="true" />
        </button>

        <Link className="home-shortcut" to="/tests">
          <span className="home-shortcut-icon is-violet">
            <FlaskConical size={22} aria-hidden="true" />
          </span>
          <span className="home-shortcut-copy">
            <strong>Run tests</strong>
            <small>View your test workspace.</small>
          </span>
          <ChevronRight size={19} aria-hidden="true" />
        </Link>

        <Link className="home-shortcut" to="/profile">
          <span className="home-shortcut-icon is-green">
            <CircleUserRound size={22} aria-hidden="true" />
          </span>
          <span className="home-shortcut-copy">
            <strong>Your profile</strong>
            <small>View your account information.</small>
          </span>
          <ChevronRight size={19} aria-hidden="true" />
        </Link>
      </section>

      <section className="home-workspace-panel">
        <div className="home-workspace-copy">
          <span className="home-eyebrow">Convergence</span>
          <h3>Automation workspace</h3>
          <p>Your projects and activity will take shape here.</p>
        </div>

        <span
          className={`home-ready is-${health}`}
          role="status"
          aria-live="polite"
        >
          <span aria-hidden="true" />
          {HEALTH_LABEL[health]}
        </span>
      </section>

      {projects.status === "ready" && projects.items.length > 0 && (
        <section className="home-projects" aria-label="Created projects">
          <div className="home-section-heading">
            <div>
              <span className="home-eyebrow">Your workspace</span>
              <h3>Projects</h3>
            </div>
            <span className="home-project-count">{projects.items.length}</span>
          </div>

          <ProjectRows projects={projects.items} />
        </section>
      )}
    </>
  );
}
