import { useState } from "react";
import { ChevronRight, FileCode2, Upload, X } from "lucide-react";

import { createProject } from "../services/projectsApi.js";
import { uploadProjectFiles } from "../services/filesApi.js";
import { describeError } from "../services/authedFetch.js";
import { FREQUENCY_OPTIONS } from "../constants/projectOptions.js";
import { DEFAULT_FRAMEWORK, FRAMEWORK_OPTIONS, frameworkLabel } from "../utils/framework.js";
import { browserAfterFrameworkChange, browserOptionsFor } from "../utils/projectForm.js";

/*
 * Create Project flow (details -> summary -> Save Project).
 * Frequency, Browser and Framework are sent to POST /projects. Selenium is the default;
 * Playwright restricts the browser to Chrome. Whether Playwright can run is decided by the
 * backend: a refusal (403 / 409 / 422) is shown with its own message. "Save Project" does
 * NOT start a run.
 */
export default function CreateProjectModal({ onClose, onCreated }) {
  const [step, setStep] = useState("details");
  const [name, setName] = useState("");
  const [frequency, setFrequency] = useState("5 minutes");
  const [framework, setFramework] = useState(DEFAULT_FRAMEWORK);
  const [browser, setBrowser] = useState("Chrome");
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  function onContinue(event) {
    event.preventDefault();

    if (files.length === 0) {
      setFileError("Choose at least one .py file.");
      return;
    }

    setFileError("");
    setStep("summary");
  }

  async function onSave() {
    setSaving(true);
    setSaveError("");

    let project;

    try {
      project = await createProject({
        name: name.trim(),
        frequency,
        browser,
        framework,
      });
    } catch (err) {
      setSaveError(describeError(err));
      setSaving(false);
      return;
    }

    // The project now exists. A failed upload must not look like a failed
    // project, so hand the upload error to the files page instead.
    let uploadError = "";

    try {
      await uploadProjectFiles(project.id, files);
    } catch (err) {
      uploadError = describeError(err);
    }

    setSaving(false);
    onCreated(project, uploadError);
  }

  const isSummary = step === "summary";
  const browserOptions = browserOptionsFor(framework, browser);
  const browserShown = browserOptions.find((option) => option.value === browser)?.label ?? browser;

  function onFrameworkChange(next) {
    setFramework(next);
    setBrowser((current) => browserAfterFrameworkChange(next, current, null));
  }

  return (
    <div
      className="home-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving) onClose();
      }}
    >
      <section
        className={`home-modal${isSummary ? " is-summary" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
      >
        <div className="home-modal-heading">
          <div>
            <span className="home-eyebrow">
              {isSummary ? "Review" : "Workspace"}
            </span>
            <h2 id="create-project-title">
              {isSummary ? "Summary" : "Create project"}
            </h2>
          </div>

          <button
            className="home-icon-button"
            type="button"
            aria-label="Close dialog"
            disabled={saving}
            onClick={onClose}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>

        {!isSummary ? (
          <form className="home-project-form" onSubmit={onContinue}>
            <div className="home-project-form-fields">
              <div className="home-form-field">
                <label htmlFor="home-project-name">Project name</label>
                <input
                  id="home-project-name"
                  autoFocus
                  minLength={2}
                  maxLength={60}
                  placeholder="e.g. Checkout flow"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />
              </div>

              <div className="home-form-field">
                <label htmlFor="home-project-frequency">Frequency</label>
                <select
                  id="home-project-frequency"
                  value={frequency}
                  onChange={(event) => setFrequency(event.target.value)}
                >
                  {FREQUENCY_OPTIONS.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </select>
              </div>

              <div className="home-form-field">
                <label htmlFor="home-project-framework">Framework</label>
                <select
                  id="home-project-framework"
                  value={framework}
                  onChange={(event) => onFrameworkChange(event.target.value)}
                >
                  {FRAMEWORK_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                {framework === "playwright" && (
                  <p className="home-form-hint">
                    Playwright projects run in Chrome only. The server confirms whether Playwright
                    is available when you save or run the project.
                  </p>
                )}
              </div>

              <div className="home-form-field">
                <label htmlFor="home-project-browser">Browser Type</label>
                <select
                  id="home-project-browser"
                  value={browser}
                  onChange={(event) => setBrowser(event.target.value)}
                >
                  {browserOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>

              <div className="home-form-field home-upload-field">
                <label htmlFor="home-project-files">Upload Python files</label>
                <label className="home-folder-picker" htmlFor="home-project-files">
                  <span className="home-folder-picker-icon">
                    <Upload size={21} aria-hidden="true" />
                  </span>
                  <span>
                    <strong>Choose .py files</strong>
                    <small>Select one or more Python files</small>
                  </span>
                  <input
                    id="home-project-files"
                    type="file"
                    accept=".py"
                    multiple
                    onChange={(event) => {
                      const python = Array.from(event.target.files || []).filter(
                        (file) => file.name.toLowerCase().endsWith(".py")
                      );
                      setFiles(python);
                      setFileError(
                        python.length ? "" : "Choose at least one .py file."
                      );
                    }}
                  />
                </label>

                {files.length > 0 && (
                  <div className="home-selected-files" role="status">
                    <FileCode2 size={17} aria-hidden="true" />
                    <span>
                      {files.length} Python {files.length === 1 ? "file" : "files"} selected
                    </span>
                  </div>
                )}

                {fileError && (
                  <p className="home-form-error" role="alert">{fileError}</p>
                )}
              </div>
            </div>

            <div className="home-modal-actions">
              <button className="home-secondary-button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button className="home-primary-button" type="submit">
                Continue
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>
          </form>
        ) : (
          <div className="home-project-summary">
            <dl>
              <div><dt>Project name</dt><dd>{name.trim()}</dd></div>
              <div><dt>Frequency</dt><dd>{frequency}</dd></div>
              <div><dt>Framework</dt><dd>{frameworkLabel({ framework })}</dd></div>
              <div><dt>Browser Type</dt><dd>{browserShown}</dd></div>
              <div>
                <dt>Python files</dt>
                <dd>{files.length} {files.length === 1 ? "file" : "files"}</dd>
              </div>
            </dl>

            <ul aria-label="Selected Python files">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  <FileCode2 size={17} aria-hidden="true" />
                  <span>{file.name}</span>
                </li>
              ))}
            </ul>

            {saveError && (
              <p className="home-form-error" role="alert">{saveError}</p>
            )}

            <div className="home-modal-actions">
              <button
                className="home-secondary-button"
                type="button"
                disabled={saving}
                onClick={() => setStep("details")}
              >
                Back
              </button>
              <button
                className="home-primary-button"
                type="button"
                disabled={saving}
                onClick={onSave}
              >
                {saving ? "Saving..." : "Save Project"}
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
