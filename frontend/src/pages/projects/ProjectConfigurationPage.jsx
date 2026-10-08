import { useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { Pencil } from "lucide-react";

import { updateProject } from "../../services/projectsApi.js";
import { describeError } from "../../services/authedFetch.js";
import { BROWSER_OPTIONS, FREQUENCY_OPTIONS } from "../../constants/projectOptions.js";

const FIELDS = ["name", "frequency", "browser"];

function toDraft(project) {
  return {
    name: project.name,
    frequency: project.frequency,
    browser: project.browser,
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

  const dirty = FIELDS.some((field) => draft[field].trim() !== project[field]);

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

  async function save(event) {
    event.preventDefault();
    if (inFlight.current) return;

    const errors = validate(draft);
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      return;
    }

    const payload = {};
    FIELDS.forEach((field) => {
      const value = draft[field].trim();
      if (value !== project[field]) payload[field] = value;
    });

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
            <label htmlFor="cfg-browser">Browser</label>
            <select
              id="cfg-browser"
              value={draft.browser}
              disabled={saving}
              onChange={(event) => change("browser", event.target.value)}
            >
              {withCurrent(BROWSER_OPTIONS, project.browser).map((option) => (
                <option key={option}>{option}</option>
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
    </div>
  );
}
