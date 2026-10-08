import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useOutletContext } from "react-router-dom";
import { FileCode2, Upload } from "lucide-react";
import LoadingState from "../../components/brand/LoadingState.jsx";

import { deleteProjectFile, listProjectFiles, uploadProjectFiles } from "../../services/filesApi.js";
import { describeError } from "../../services/authedFetch.js";

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value ?? "") : date.toLocaleString();
}

export default function ProjectFilesPage() {
  const { project } = useOutletContext();
  const location = useLocation();
  const inputRef = useRef(null);

  // state: "loading" | "ready" | "error"
  const [state, setState] = useState({ status: "loading", files: [], error: "" });
  const [selected, setSelected] = useState(() => new Set());
  const [uploading, setUploading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const deleteInFlight = useRef(false);
  const [notice, setNotice] = useState(() =>
    location.state?.uploadError
      ? { kind: "error", text: `Project saved, but uploading files failed: ${location.state.uploadError}` }
      : location.state?.created
        ? { kind: "success", text: "Project saved and files uploaded." }
        : null
  );

  const load = useCallback(
    async (signal) => {
      setState((prev) => ({ ...prev, status: "loading", error: "" }));
      try {
        const files = await listProjectFiles(project.id, signal);
        setState({ status: "ready", files: Array.isArray(files) ? files : [], error: "" });
      } catch (err) {
        if (err?.name === "AbortError") return;
        setState({
          status: "error",
          files: [],
          error: describeError(err, "This project was not found."),
        });
      }
    },
    [project.id]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function onUpload(event) {
    const picked = Array.from(event.target.files || []).filter((file) =>
      file.name.toLowerCase().endsWith(".py")
    );
    event.target.value = "";

    if (picked.length === 0) {
      setNotice({ kind: "error", text: "Choose at least one .py file." });
      return;
    }

    setUploading(true);
    setNotice(null);

    try {
      await uploadProjectFiles(project.id, picked);
      setNotice({
        kind: "success",
        text: `${picked.length} ${picked.length === 1 ? "file" : "files"} uploaded.`,
      });
      await load();
    } catch (err) {
      setNotice({ kind: "error", text: describeError(err, "This project was not found.") });
    } finally {
      setUploading(false);
    }
  }

  function openDelete(file) {
    setDeleteError("");
    setDeleteTarget(file);
  }

  async function confirmDelete() {
    if (deleteInFlight.current || !deleteTarget) return;
    deleteInFlight.current = true;
    setDeleting(true);
    setDeleteError("");

    const target = deleteTarget;

    try {
      await deleteProjectFile(project.id, target.id);
      setState((prev) => ({ ...prev, files: prev.files.filter((f) => f.id !== target.id) }));
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(target.id);
        return next;
      });
      setDeleteTarget(null);
      setNotice({ kind: "success", text: `${target.file_name} deleted successfully.` });
    } catch (err) {
      setDeleteError(
        err?.status === 0 || err?.status === 401 || err?.status === 404
          ? describeError(err, "File not found.")
          : `Unable to delete file. ${describeError(err)}`
      );
    } finally {
      deleteInFlight.current = false;
      setDeleting(false);
    }
  }

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const { files } = state;

  return (
    <div className="home-files">
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Python files</span>
          <h3>Project files</h3>
        </div>

        <div className="home-files-actions">
          {state.status === "ready" && files.length > 0 && (
            <span className="home-files-selected">
              {selected.size} of {files.length} selected
            </span>
          )}

          <input
            ref={inputRef}
            type="file"
            accept=".py"
            multiple
            hidden
            onChange={onUpload}
          />
          <button
            className="home-primary-button"
            type="button"
            disabled={uploading}
            onClick={() => inputRef.current?.click()}
          >
            <Upload size={18} aria-hidden="true" />
            {uploading ? "Uploading…" : "Upload files"}
          </button>
        </div>
      </div>

      {notice && (
        <p
          className={`home-notice is-${notice.kind}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
        </p>
      )}

      {state.status === "loading" && (
        <div className="home-empty-state" role="status">
          <LoadingState type="flow" label="Loading files…" />
        </div>
      )}

      {state.status === "error" && (
        <div className="home-empty-state is-error" role="alert">
          <h3>Could not load files</h3>
          <p>{state.error}</p>
          <button className="home-secondary-button" type="button" onClick={() => load()}>
            Try again
          </button>
        </div>
      )}

      {state.status === "ready" && files.length === 0 && (
        <div className="home-empty-state">
          <span className="home-empty-icon">
            <FileCode2 size={25} aria-hidden="true" />
          </span>
          <h3>No files yet</h3>
          <p>Upload Python test files to this project.</p>
        </div>
      )}

      {state.status === "ready" && files.length > 0 && (
        <ul className="home-file-list" aria-label="Project files">
          {files.map((file) => (
            <li className="home-file-row" key={file.id}>
              <input
                type="checkbox"
                aria-label={`Select ${file.file_name}`}
                checked={selected.has(file.id)}
                onChange={() => toggle(file.id)}
              />
              <span className="home-project-icon">
                <FileCode2 size={19} aria-hidden="true" />
              </span>
              <Link
                className="home-file-name"
                to={`/projects/${project.id}/files/${file.id}`}
              >
                {file.file_name}
              </Link>
              <span className="home-file-date">{formatDate(file.created_at)}</span>
              <span className="home-file-actions">
                <Link
                  className="home-file-edit"
                  to={`/projects/${project.id}/files/${file.id}/edit`}
                  aria-label={`Edit ${file.file_name}`}
                >
                  Edit
                </Link>
                <button
                  className="home-file-edit is-danger"
                  type="button"
                  aria-label={`Delete ${file.file_name}`}
                  onClick={() => openDelete(file)}
                >
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {deleteTarget && (
        <div
          className="home-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleting) setDeleteTarget(null);
          }}
        >
          <section
            className="home-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-file-title"
            aria-describedby="delete-file-desc"
          >
            <h2 id="delete-file-title">Delete {deleteTarget.file_name}?</h2>
            <p id="delete-file-desc">
              This will permanently remove this file from the project.
            </p>

            {deleteError && <p className="home-notice is-error" role="alert">{deleteError}</p>}

            <div className="home-modal-actions">
              <button
                className="home-secondary-button"
                type="button"
                disabled={deleting}
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </button>
              <button
                className="home-primary-button is-danger"
                type="button"
                disabled={deleting}
                onClick={confirmDelete}
              >
                {deleting ? "Deleting..." : "Delete"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
