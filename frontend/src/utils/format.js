// The backend sends naive UTC timestamps ("2026-10-06T06:15:11"): no "Z", no
// offset. Browsers would read those as LOCAL time, so they are parsed as UTC.
export function parseServerDate(value) {
  if (!value) return null;
  const text = String(value);
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(text);
  const date = new Date(hasZone ? text : `${text}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value, fallback = "—") {
  if (!value) return fallback;
  const date = parseServerDate(value);
  return date ? date.toLocaleString() : String(value);
}

// <input type="datetime-local"> value (local wall time) -> naive UTC string for the API.
export function localInputToServerUtc(localValue) {
  const date = new Date(localValue);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 19);
}

// naive UTC from the API -> value for <input type="datetime-local"> (local wall time).
export function serverUtcToLocalInput(value) {
  const date = parseServerDate(value);
  if (!date) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Duration between two backend timestamps; null when either is missing.
export function formatDuration(start, end) {
  if (!start || !end) return null;
  const ms = (parseServerDate(end)?.getTime() ?? NaN) - (parseServerDate(start)?.getTime() ?? NaN);
  if (!Number.isFinite(ms) || ms < 0) return null;

  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;

  if (h) return `${h}h ${m}m ${s}s`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

// Seconds (number from the backend) -> "1.1s" / "2m 5s"; null if not a number.
export function formatSeconds(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 60) return `${Math.round(value * 10) / 10}s`;
  const m = Math.floor(value / 60);
  const s = Math.round(value % 60);
  return `${m}m ${s}s`;
}
