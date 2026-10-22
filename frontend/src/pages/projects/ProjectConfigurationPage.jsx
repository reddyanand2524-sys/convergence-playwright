import { useRef, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { Pencil, Trash2 } from "lucide-react";

import { deleteProject, updateProject } from "../../services/projectsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { FREQUENCY_OPTIONS } from "../../constants/projectOptions.js";
import { FRAMEWORK_OPTIONS, frameworkLabel, frameworkOf } from "../../utils/framework.js";
import {
  browserAfterFrameworkChange,
  browserOptionsFor,
  buildProjectPatch,
} from "../../utils/projectForm.js";

const FIELDS = ["name", "frequency", "browser", "framework"];

// A legacy project without a framework is edited as Selenium; that is only the form's
// starting value - nothing is sent unless the user changes it.
function toDraft(project) {
  return {
    name: project.name,
    frequency: project.frequency,
    browser: project.browser,
    framework: frameworkOf(project),
  };
}

// Backend values are free-form; never drop or rewrite one that is not in
// the standard list - show it as an extra option instead.
function withCurrent(options, value) {
  return options.includes(value) ? options : [value, ...options];
}

// Maps a FastAPI 422 detail array to { field: message }.
function fieldErrorsFrom(detail) {
  const errors = {};
  if (!Array.isArray(detail)) return errors;

  detail.forEach((item) => {
    const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null;
    if (FIELDS.includes(field) && item.msg) errors[field] = item.msg;
  });

  return errors;
}

function validate(draft) {
  const errors = {};
  const name = draft.name.trim();

  if (name.length < 2) errors.name = "Project name must be at least 2 characters.";
  else if (name.length > 150) errors.name = "Project name must be 150 characters or fewer.";

  if (!draft.frequency.trim()) errors.frequency = "Frequency is required.";
  else if (draft.frequency.length > 50) errors.frequency = "Frequency must be 50 characters or fewer.";

  if (!draft.browser.trim()) errors.browser = "Browser is required.";
  else if (draft.browser.length > 30) errors.browser = "Browser must be 30 characters or fewer.";

  return errors;
}

export default function ProjectConfigurationPage() {
  const { project, setProject, reloadProjects } = useOutletContext();

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => toDraft(project));
  const [fieldErrors, setFieldErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const inFlight = useRef(false);

  const navigate = useNavigate();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const deleteInFlight = useRef(false);

  const dirty = Object.keys(buildProjectPatch(project, draft)).length > 0;

  function startEdit() {
    setDraft(toDraft(project));
    setFieldErrors({});
    setNotice(null);
    setEditing(true);
  }

  function cancel() {
    // Restore the last server-loaded values. No request is sent.
    setDraft(toDraft(project));
    setFieldErrors({});
    setNotice(null);
    setEditing(false);
  }

  function change(field, value) {
    setDraft((prev) => ({ ...prev, [field]: value }));
    setFieldErrors((prev) => ({ ...prev, [field]: undefined }));
  }

  // Playwright is Chrome only; going back to Selenium restores a Selenium browser
  // (the project's own one when it had one).
  function changeFramework(next) {
    setDraft((prev) => ({
      ...prev,
      framework: next,
      browser: browserAfterFrameworkChange(next, prev.browser, project),
    }));
    setFieldErrors((prev) => ({ ...prev, framework: undefined, browser: undefined }));
  }

  async function save(event) {
    event.preventDefault();
    if (inFlight.current) return;

    const errors = validate(draft);
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      return;
    }

    const payload = buildProjectPatch(project, draft);

    if (Object.keys(payload).length === 0) {
      setEditing(false);
      setNotice({ kind: "info", text: "No changes to save." });
      return;
    }

    inFlight.current = true;
    setSaving(true);
    setNotice(null);

    try {
      const updated = await updateProject(project.id, payload);
      // Only the backend response becomes the new local state.
      setProject(updated);
      setDraft(toDraft(updated));
      setFieldErrors({});
      setEditing(false);
      setNotice({ kind: "success", text: "Project configuration updated successfully." });
      reloadProjects();
    } catch (err) {
      const mapped = fieldErrorsFrom(err?.detail);
      if (Object.keys(mapped).length) setFieldErrors(mapped);

      setNotice({
        kind: "error",
        text:
          err?.status === 404
            ? "Project not found. It may have been removed."
            : err?.status === 0
              ? "Unable to connect to the backend."
              : describeError(err),
      });
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  function openDelete() {
    setDeleteError("");
    setDeleteOpen(true);
  }

  // The project is only removed from the UI after the backend confirms (204).
  async function confirmDelete() {
    if (deleteInFlight.current) return;
    deleteInFlight.current = true;
    setDeleting(true);
    setDeleteError("");

    try {
      await deleteProject(project.id);
      await reloadProjects();
      navigate("/tests", { replace: true });
    } catch (err) {
      setDeleteError(
        err?.status === 0
          ? "Unable to connect to the backend."
          : `Unable to delete project. ${describeError(err, "Project not found. It may already have been deleted.")}`
      );
      deleteInFlight.current = false;
      setDeleting(false);
    }
  }

  return (
    <div className="home-config">
      <div className="home-section-heading">
        <div>
          <span className="home-eyebrow">Execution settings</span>
          <h3>Project configuration</h3>
        </div>

        {!editing && (
          <button className="home-secondary-button" type="button" onClick={startEdit}>
            <Pencil size={16} aria-hidden="true" />
            Edit Configuration
          </button>
        )}
      </div>

      {notice && (
        <p
          className={`home-notice is-${notice.kind}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
        </p>
      )}

      {!editing ? (
        <dl className="home-config-card">
          <div><dt>Project name</dt><dd>{project.name}</dd></div>
          <div><dt>Frequency</dt><dd>{project.frequency}</dd></div>
          <div><dt>Framework</dt><dd>{frameworkLabel(project)}</dd></div>
          <div><dt>Browser</dt><dd>{project.browser}</dd></div>
          <div><dt>Project ID</dt><dd>{project.id}</dd></div>
          <div><dt>Status</dt><dd>{project.is_active ? "Active" : "Inactive"}</dd></div>
        </dl>
      ) : (
        <form className="home-config-card is-editing" onSubmit={save} noValidate>
          <div className="home-form-field">
            <label htmlFor="cfg-name">Project name</label>
            <input
              id="cfg-name"
              value={draft.name}
              maxLength={150}
              disabled={saving}
              aria-invalid={Boolean(fieldErrors.name)}
              onChange={(event) => change("name", event.target.value)}
            />
            {fieldErrors.name && <p className="home-form-error" role="alert">{fieldErrors.name}</p>}
          </div>

          <div className="home-form-field">
            <label htmlFor="cfg-frequency">Frequency</label>
            <select
              id="cfg-frequency"
              value={draft.frequency}
              disabled={saving}
              onChange={(event) => change("frequency", event.target.value)}
            >
              {withCurrent(FREQUENCY_OPTIONS, project.frequency).map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
            {fieldErrors.frequency && <p className="home-form-error" role="alert">{fieldErrors.frequency}</p>}
          </div>

          <div className="home-form-field">
            <label htmlFor="cfg-framework">Framework</label>
            <select
              id="cfg-framework"
              value={draft.framework}
              disabled={saving}
              onChange={(event) => changeFramework(event.target.value)}
            >
              {withCurrent(
                FRAMEWORK_OPTIONS.map((option) => option.value),
                draft.framework
              ).map((value) => (
                <option key={value} value={value}>
                  {FRAMEWORK_OPTIONS.find((option) => option.value === value)?.label ?? value}
                </option>
              ))}
            </select>
            {draft.framework === "playwright" && (
              <p className="home-form-hint">
                Playwright projects run in Chrome only. The server confirms whether the change
                is allowed when you save (for example it is refused while runs are active or
                schedules are enabled).
              </p>
            )}
            {fieldErrors.framework && <p className="home-form-error" role="alert">{fieldErrors.framework}</p>}
          </div>

          <div className="home-form-field">
            <label htmlFor="cfg-browser">Browser</label>
            <select
              id="cfg-browser"
              value={draft.browser}
              disabled={saving}
              onChange={(event) => change("browser", event.target.value)}
            >
              {browserOptionsFor(draft.framework, project.browser).map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            {fieldErrors.browser && <p className="home-form-error" role="alert">{fieldErrors.browser}</p>}
          </div>

          <div className="home-config-actions">
            {dirty && <span className="home-unsaved">Unsaved changes</span>}
            <button className="home-secondary-button" type="button" disabled={saving} onClick={cancel}>
              Cancel
            </button>
            <button className="home-primary-button" type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save Changes"}
            </button>
          </div>
        </form>
      )}

      {!editing && (
        <section className="home-danger-zone" aria-labelledby="danger-zone-title">
          <div>
            <h4 id="danger-zone-title">Delete project</h4>
            <p>Permanently delete this project and everything in it. This cannot be undone.</p>
          </div>
          <button className="home-secondary-button is-danger-outline" type="button" onClick={openDelete}>
            <Trash2 size={16} aria-hidden="true" />
            Delete Project
          </button>
        </section>
      )}

      {deleteOpen && (
        <div
          className="home-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleting) setDeleteOpen(false);
          }}
        >
          <section
            className="home-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-project-title"
            aria-describedby="delete-project-desc"
          >
            <h2 id="delete-project-title">Delete {project.name}?</h2>
            <p id="delete-project-desc">
              This will permanently delete the project and all of its associated files, runs,
              historical results, screenshots, failure diagnostics and schedules. This cannot be
              undone.
            </p>

            {deleteError && <p className="home-notice is-error" role="alert">{deleteError}</p>}

            <div className="home-modal-actions">
              <button
                className="home-secondary-button"
                type="button"
                disabled={deleting}
                onClick={() => setDeleteOpen(false)}
              >
                Cancel
              </button>
              <button
                className="home-primary-button is-danger"
                type="button"
                disabled={deleting}
                onClick={confirmDelete}
              >
                {deleting ? "Deleting..." : "Delete Project"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
