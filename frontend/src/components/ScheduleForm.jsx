import { useRef, useState } from "react";
import { X } from "lucide-react";

import { FREQUENCIES, createSchedule, updateSchedule } from "../services/schedulesApi.js";
import { describeError } from "../services/authedFetch.js";
import { localInputToServerUtc, serverUtcToLocalInput } from "../utils/format.js";
import {
  defaultScheduleBrowser,
  scheduleBrowserOptions,
  showsPlaywrightScheduleHint,
} from "../utils/scheduleBrowser.js";

const FREQUENCY_LABELS = {
  ONCE: "Once",
  INTERVAL: "Every N minutes",
  HOURLY: "Hourly",
  DAILY: "Daily",
  WEEKLY: "Weekly",
};

const FIELD_NAMES = ["name", "browser", "frequency", "interval_value", "start_at"];

function blankLocalNow(minutesAhead = 5) {
  const date = new Date(Date.now() + minutesAhead * 60000);
  date.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Maps backend 422 detail ([{loc, msg}] or a plain string) onto form fields.
function mapBackendErrors(err) {
  const fields = {};
  const general = [];

  if (Array.isArray(err?.detail)) {
    err.detail.forEach((item) => {
      const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null;
      const msg = String(item?.msg || "").replace(/^Value error, /, "");
      if (FIELD_NAMES.includes(field)) fields[field] = msg;
      else if (msg) general.push(msg);
    });
  } else if (typeof err?.detail === "string") {
    general.push(err.detail);
  }

  if (!Object.keys(fields).length && !general.length) general.push(describeError(err));
  return { fields, general };
}

/*
 * Create / edit form (modal). Create sends the full body; edit sends only what
 * changed (PATCH). Backend validation stays authoritative - its messages are
 * shown next to the field they belong to.
 */
export default function ScheduleForm({ projectId, project, schedule, onClose, onSaved }) {
  const editing = Boolean(schedule);
  const initialStart = editing ? serverUtcToLocalInput(schedule.start_at) : blankLocalNow();

  const [values, setValues] = useState(() => ({
    name: schedule?.name ?? "",
    browser: schedule?.browser ?? defaultScheduleBrowser(project),
    frequency: schedule?.frequency ?? "ONCE",
    interval_value: schedule?.interval_value != null ? String(schedule.interval_value) : "",
    start_at: initialStart,
    enabled: schedule?.enabled ?? true,
  }));
  const [errors, setErrors] = useState({ fields: {}, general: [] });
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);

  const set = (field) => (event) => {
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => ({ fields: { ...prev.fields, [field]: undefined }, general: [] }));
  };

  // Run browsers are lower-case in the API ("chrome"). Selenium projects offer Chrome / Firefox /
  // Edge; Playwright projects offer Chrome. A stored value outside that list (set elsewhere) must
  // not be lost, so it stays visible until the user picks something else.
  const browserOptions = scheduleBrowserOptions(project, values.browser);
  const playwrightHint = showsPlaywrightScheduleHint(project);

  function validate() {
    const fields = {};
    const name = values.name.trim();

    if (name.length < 2) fields.name = "Name must be at least 2 characters.";
    else if (name.length > 150) fields.name = "Name must be 150 characters or fewer.";
    if (!values.browser) fields.browser = "Choose a browser.";
    if (!values.start_at || !localInputToServerUtc(values.start_at)) fields.start_at = "Choose a start date and time.";
    if (values.frequency === "INTERVAL") {
      const n = Number(values.interval_value);
      if (!/^\d+$/.test(values.interval_value.trim()) || n < 1) {
        fields.interval_value = "Enter a whole number of minutes, 1 or more.";
      }
    }
    return fields;
  }

  async function submit(event) {
    event.preventDefault();
    if (inFlight.current) return;

    const fields = validate();
    if (Object.keys(fields).length) {
      setErrors({ fields, general: [] });
      return;
    }

    const isInterval = values.frequency === "INTERVAL";
    let request;

    if (!editing) {
      request = () =>
        createSchedule(projectId, {
          name: values.name.trim(),
          browser: values.browser,
          frequency: values.frequency,
          ...(isInterval ? { interval_value: Number(values.interval_value) } : {}),
          start_at: localInputToServerUtc(values.start_at),
          enabled: values.enabled,
        });
    } else {
      const patch = {};
      if (values.name.trim() !== schedule.name) patch.name = values.name.trim();
      if (values.browser !== schedule.browser) patch.browser = values.browser;
      if (values.start_at !== initialStart) patch.start_at = localInputToServerUtc(values.start_at);

      const newInterval = isInterval ? Number(values.interval_value) : null;
      if (values.frequency !== schedule.frequency || newInterval !== (schedule.interval_value ?? null)) {
        // frequency and interval_value must be consistent, so they travel together
        patch.frequency = values.frequency;
        patch.interval_value = newInterval;
      }

      if (Object.keys(patch).length === 0) {
        onClose();
        return;
      }
      request = () => updateSchedule(projectId, schedule.id, patch);
    }

    inFlight.current = true;
    setSaving(true);
    setErrors({ fields: {}, general: [] });

    try {
      const saved = await request();
      onSaved(saved, editing);
    } catch (err) {
      setErrors(mapBackendErrors(err));
      inFlight.current = false;
      setSaving(false);
    }
  }

  const inPast = values.start_at && new Date(values.start_at).getTime() < Date.now();
  const fe = errors.fields;

  return (
    <div
      className="home-modal-backdrop"
      onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}
    >
      <section className="home-dialog is-form" role="dialog" aria-modal="true" aria-labelledby="schedule-form-title">
        <div className="home-dialog-heading">
          <h2 id="schedule-form-title">{editing ? "Edit schedule" : "Create schedule"}</h2>
          <button className="home-icon-button" type="button" aria-label="Close dialog" disabled={saving} onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={submit} noValidate className="home-schedule-form">
          <div className="home-form-field">
            <label htmlFor="sch-name">Schedule name</label>
            <input
              id="sch-name"
              autoFocus
              maxLength={150}
              value={values.name}
              aria-invalid={Boolean(fe.name)}
              aria-describedby={fe.name ? "sch-name-err" : undefined}
              onChange={set("name")}
              disabled={saving}
            />
            {fe.name && <p id="sch-name-err" className="home-form-error" role="alert">{fe.name}</p>}
          </div>

          <div className="home-schedule-row">
            <div className="home-form-field">
              <label htmlFor="sch-browser">Browser</label>
              <select
                id="sch-browser"
                value={values.browser}
                onChange={set("browser")}
                disabled={saving}
                aria-invalid={Boolean(fe.browser)}
                aria-describedby={playwrightHint ? "sch-browser-hint" : undefined}
              >
                {browserOptions.map((b) => (
                  <option key={b.value} value={b.value}>{b.label}</option>
                ))}
              </select>
              {fe.browser && <p className="home-form-error" role="alert">{fe.browser}</p>}
            </div>

            <div className="home-form-field">
              <label htmlFor="sch-frequency">Frequency</label>
              <select id="sch-frequency" value={values.frequency} onChange={set("frequency")} disabled={saving} aria-invalid={Boolean(fe.frequency)}>
                {FREQUENCIES.map((f) => (
                  <option key={f} value={f}>{FREQUENCY_LABELS[f]}</option>
                ))}
              </select>
              {fe.frequency && <p className="home-form-error" role="alert">{fe.frequency}</p>}
            </div>
          </div>

          {playwrightHint && (
            <p id="sch-browser-hint" className="home-field-hint">
              Chrome is the available browser for Playwright schedules. The server decides whether a
              schedule is allowed for this project; if it refuses, its message is shown here.
            </p>
          )}

          {values.frequency === "INTERVAL" && (
            <div className="home-form-field">
              <label htmlFor="sch-interval">Interval (minutes)</label>
              <input
                id="sch-interval"
                inputMode="numeric"
                placeholder="e.g. 15"
                value={values.interval_value}
                aria-invalid={Boolean(fe.interval_value)}
                aria-describedby={fe.interval_value ? "sch-interval-err" : undefined}
                onChange={set("interval_value")}
                disabled={saving}
              />
              {fe.interval_value && <p id="sch-interval-err" className="home-form-error" role="alert">{fe.interval_value}</p>}
            </div>
          )}

          <div className="home-form-field">
            <label htmlFor="sch-start">Start date and time</label>
            <input
              id="sch-start"
              type="datetime-local"
              value={values.start_at}
              aria-invalid={Boolean(fe.start_at)}
              aria-describedby="sch-start-hint"
              onChange={set("start_at")}
              disabled={saving}
            />
            <p id="sch-start-hint" className="home-field-hint">
              Your local time. It is converted to UTC for the server.
              {inPast && " This time is in the past, so the schedule will run as soon as the scheduler picks it up."}
            </p>
            {fe.start_at && <p className="home-form-error" role="alert">{fe.start_at}</p>}
          </div>

          {!editing && (
            <label className="home-check-field">
              <input type="checkbox" checked={values.enabled} onChange={set("enabled")} disabled={saving} />
              <span>Enabled (the scheduler will run it)</span>
            </label>
          )}

          {errors.general.map((message) => (
            <p key={message} className="home-notice is-error" role="alert">{message}</p>
          ))}

          <div className="home-modal-actions">
            <button className="home-secondary-button" type="button" disabled={saving} onClick={onClose}>
              Cancel
            </button>
            <button className="home-primary-button" type="submit" disabled={saving}>
              {saving ? "Saving..." : editing ? "Save changes" : "Create schedule"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
