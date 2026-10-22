/*
 * Display view of ONE step of the JSON execution report (GET .../report/json).
 *
 * Field names follow the report schema (docs/json-execution-report.md): stepNumber, stepName,
 * command, value, status, attempted, skipReason, executionTimeMs, error { type, category,
 * message }, screenshot. The report also carries `name` / `action` as aliases of stepName /
 * command, so those are the only fallbacks used. Result-API steps are a different shape
 * (action / target / step_number / duration_seconds) and are rendered elsewhere; none of their
 * fields are guessed here. Every field is optional: a missing one never throws.
 */
const text = (value) => (typeof value === "string" && value.trim() ? value : "");

/** "Type (category): message" from whatever parts the error has; "" when it has none. */
export function describeReportError(error) {
  if (typeof error === "string") return error.trim();
  if (!error || typeof error !== "object") return "";

  const type = text(error.type);
  const category = text(error.category);
  const message = text(error.message);

  const head = [type, category && (type ? `(${category})` : category)].filter(Boolean).join(" ");
  return [head, message].filter(Boolean).join(": ");
}

export function reportStepView(step, index = 0) {
  const s = step && typeof step === "object" ? step : {};
  const hasValue = s.value !== undefined && s.value !== null && s.value !== "";

  return {
    number: Number.isFinite(s.stepNumber) ? s.stepNumber : index + 1,
    label: (text(s.command) || text(s.action) || "step").toUpperCase(),
    name: text(s.stepName) || text(s.name),
    status: s.status,
    // only an explicit `false` means "not attempted"; a missing flag says nothing
    notAttempted: s.attempted === false,
    executionTimeMs: typeof s.executionTimeMs === "number" ? s.executionTimeMs : null,
    value: hasValue ? String(s.value) : "",
    hasError: Boolean(s.error),
    errorText: describeReportError(s.error),
    skipReason: text(s.skipReason),
    screenshot: text(s.screenshot),
  };
}
