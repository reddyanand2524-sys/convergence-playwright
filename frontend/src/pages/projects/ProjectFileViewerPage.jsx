import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import { getProjectFile } from "../../services/filesApi.js";
import { describeError } from "../../services/authedFetch.js";

/*
 * Viewer. Editing happens on the separate /edit route. file_path from the backend is never used.
 */
export default function ProjectFileViewerPage() {
  const { project } = useOutletContext();
  const { fileId } = useParams();
  const saved = useLocation().state?.saved;

  const [state, setState] = useState({ status: "loading", file: null, error: "" });

  const load = useCallback(
    async (signal) => {
      setState({ status: "loading", file: null, error: "" });
      try {
        const file = await getProjectFile(project.id, fileId, signal);
        setState({ status: "ready", file, error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({
          status: "error",
          file: null,
          error: describeError(err, "This file was not found in this project."),
        });
      }
    },
    [project.id, fileId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const filesPath = `/projects/${project.id}/files`;

  return (
    <div className="home-viewer">
      <Link className="home-back-link" to={filesPath}>
        <ArrowLeft size={16} aria-hidden="true" />
        Project Files
      </Link>

      {state.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading file…" />
        </div>
      )}

      {state.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not open file</h3>
          <p>{state.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {saved && state.status === "ready" && (
        <p className="home-notice is-success" role="status">File saved successfully.</p>
      )}

      {state.status === "ready" && (
        <div className="home-code-card">
          <div className="home-code-header">
            <strong>{state.file.file_name}</strong>
            <Link className="home-file-edit" to={`/projects/${project.id}/files/${fileId}/edit`}>
              Edit
            </Link>
          </div>

          <pre className="home-code" tabIndex={0}>
            {String(state.file.file_content ?? "")
              .split("\n")
              .map((line, index) => (
                <div className="home-code-line" key={index}>
                  <span className="home-code-no" aria-hidden="true">{index + 1}</span>
                  <code>{line || " "}</code>
                </div>
              ))}
          </pre>
        </div>
      )}
    </div>
  );
}
