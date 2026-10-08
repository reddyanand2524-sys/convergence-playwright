// Brand-gradient progress bar. value: 0-100.
export default function ProgressBar({ value = 0, label = "Progress" }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className="cv-progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className="cv-progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}
