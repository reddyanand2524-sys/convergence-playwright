import { useState } from "react";
import { Search, X } from "lucide-react";

import { FRAMEWORK_OPTIONS } from "../utils/framework.js";

const STATUS_OPTIONS = ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"];
// Run browsers come back lowercase from the API (e.g. "chrome"); the labels are
// the same browsers the app already offers when creating a project.
const BROWSER_FILTERS = [
  { value: "chrome", label: "Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "edge", label: "Edge" },
];

export const EMPTY_FILTERS = {
  search: "",
  status: "",
  browser: "",
  framework: "",
  run_id: "",
  created_from: "",
  created_to: "",
};

/*
 * Filter form. Holds a local draft; nothing reaches the backend until
 * "Apply Filters" (or Enter). The parent remounts it (via key) when the applied
 * filters change, so back/forward and Clear always repopulate the controls.
 */
export default function RunHistoryFilters({ applied, busy, onApply, onClear }) {
  const [draft, setDraft] = useState({ ...EMPTY_FILTERS, ...applied });
  const [errors, setErrors] = useState({});

  const set = (field) => (event) => {
    setDraft((prev) => ({ ...prev, [field]: event.target.value }));
    setErrors((prev) => ({ ...prev, [field]: undefined, range: undefined }));
  };

  function validate() {
    const next = {};
    const runId = draft.run_id.trim();

    if (runId && !/^[1-9]\d*$/.test(runId)) {
      next.run_id = "Run ID must be a positive whole number.";
    }
    if (draft.created_from && draft.created_to && draft.created_from > draft.created_to) {
      next.range = "Created From cannot be later than Created To.";
    }
    return next;
  }

  function submit(event) {
    event.preventDefault();
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length) return;

    onApply({
      ...draft,
      search: draft.search.trim(),
      run_id: draft.run_id.trim(),
    });
  }

  const anyActive = Object.values(applied).some(Boolean) || Object.values(draft).some(Boolean);

  return (
    <form className="home-filters" onSubmit={submit} noValidate aria-label="Filter runs">
      <div className="home-filters-search">
        <Search size={17} aria-hidden="true" />
        <input
          type="search"
          aria-label="Search runs"
          placeholder="Search runs..."
          value={draft.search}
          onChange={set("search")}
        />
      </div>

      <div className="home-filters-grid">
        <label className="home-filter-field">
          <span>Status</span>
          <select value={draft.status} onChange={set("status")}>
            <option value="">All Statuses</option>
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </label>

        <label className="home-filter-field">
          <span>Browser</span>
          <select value={draft.browser} onChange={set("browser")}>
            <option value="">All Browsers</option>
            {BROWSER_FILTERS.map((browser) => (
              <option key={browser.value} value={browser.value}>{browser.label}</option>
            ))}
          </select>
        </label>

        <label className="home-filter-field">
          <span>Framework</span>
          <select value={draft.framework} onChange={set("framework")}>
            <option value="">All Frameworks</option>
            {FRAMEWORK_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>

        <label className="home-filter-field">
          <span>Run ID</span>
          <input
            type="text"
            inputMode="numeric"
            placeholder="Run ID"
            value={draft.run_id}
            aria-invalid={Boolean(errors.run_id)}
            onChange={set("run_id")}
          />
        </label>

        <label className="home-filter-field">
          <span>Created From</span>
          <input type="date" value={draft.created_from} aria-invalid={Boolean(errors.range)} onChange={set("created_from")} />
        </label>

        <label className="home-filter-field">
          <span>Created To</span>
          <input type="date" value={draft.created_to} aria-invalid={Boolean(errors.range)} onChange={set("created_to")} />
        </label>
      </div>

      {[errors.run_id, errors.range].filter(Boolean).map((message) => (
        <p className="home-form-error" role="alert" key={message}>{message}</p>
      ))}

      <div className="home-filters-actions">
        <button className="home-primary-button" type="submit" disabled={busy}>
          Apply Filters
        </button>
        <button
          className="home-secondary-button"
          type="button"
          disabled={busy || !anyActive}
          onClick={() => {
            setErrors({});
            onClear();
          }}
        >
          <X size={16} aria-hidden="true" />
          Clear Filters
        </button>
      </div>
    </form>
  );
}
