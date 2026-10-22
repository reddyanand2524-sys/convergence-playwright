import { Link } from "react-router-dom";
import { FolderPlus } from "lucide-react";

import { frameworkLabel } from "../utils/framework.js";

/* Project list rows; each row opens /projects/:projectId. */
export default function ProjectRows({ projects, large = false }) {
  return (
    <div className={`home-project-list${large ? " home-project-list-large" : ""}`}>
      {projects.map((project) => (
        <Link
          className="home-project-row is-link"
          key={project.id}
          to={`/projects/${project.id}`}
        >
          <span className="home-project-icon">
            <FolderPlus size={19} aria-hidden="true" />
          </span>
          <strong>{project.name}</strong>
          <span>
            {frameworkLabel(project)} · {project.frequency} · {project.browser}
          </span>
        </Link>
      ))}
    </div>
  );
}
