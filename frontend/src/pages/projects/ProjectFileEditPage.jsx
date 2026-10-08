import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import { getProjectFile, updateProjectFile } from "../../services/filesApi.js";
import { describeError } from "../../services/authedFetch.js";

const NOT_FOUND = "File not found.";

/*
 * /projects/:projectId/files/:fileId/edit
 * Loads the current content from the backend, saves with PATCH, then returns
 * to the viewer (which re-fetches). The backend is the only source of truth;
 * nothing is persisted in the browser.
 */
export default function ProjectFileEditPage() {
  const { project } = useOutletContext();
  const { fileId } = useParams();
  const navigate = useNavigate();

  const [state, setState] = useState({ status: "loading", file: null, error: "" });
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const inFlight = useRef(false);

  const viewerPath = `/projects/${project.id}/files/${fileId}`;

  const load = useCallback(
    async (signal) => {
      setState({ status: "loading", file: null, error: "" });
      try {
        const file = await getProjectFile(project.id, fileId, signal);
        setState({ status: "ready", file, error: "" });
        setContent(String(file.file_content ?? ""));
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({ status: "error", file: null, error: describeError(err, NOT_FOUND) });
      }
    },
    [project.id, fileId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const dirty = state.status === "ready" && content !== String(state.file.file_content ?? "");

  function cancel() {
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    navigate(viewerPath);
  }

  async function save() {
    if (inFlight.current || !dirty) return;
    inFlight.current = true;
    setSaving(true);
    setSaveError("");

    try {
      await updateProjectFile(project.id, fileId, content);
      navigate(viewerPath, { state: { saved: true } });
    } catch (err) {
      setSaveError(
        err?.status === 0 || err?.status === 404 || err?.status === 401
          ? describeError(err, NOT_FOUND)
          : `Unable to save file. ${describeError(err)}`
      );
      inFlight.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="home-viewer">
      <Link className="home-back-link" to={viewerPath}>
        <ArrowLeft size={16} aria-hidden="true" />
        Back to file
      </Link>

      {state.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading file..." />
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

      {state.status === "ready" && (
        <div className="home-code-card">
          <div className="home-code-header">
            <strong>{state.file.file_name}</strong>
            {dirty && <span className="home-unsaved">Unsaved changes</span>}
          </div>

          <textarea
            className="home-code-editor"
            aria-label={`Edit ${state.file.file_name}`}
            value={content}
            spellCheck={false}
            wrap="off"
            disabled={saving}
            onChange={(event) => setContent(event.target.value)}
          />

          {saveError && (
            <p className="home-notice is-error home-code-notice" role="alert">{saveError}</p>
          )}

          <div className="home-code-actions">
            <button className="home-secondary-button" type="button" disabled={saving} onClick={cancel}>
              Cancel
            </button>
            <button
              className="home-primary-button"
              type="button"
              disabled={saving || !dirty}
              onClick={save}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
