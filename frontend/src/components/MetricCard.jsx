/* Single KPI tile. tone: neutral | success | running | build | warning | critical */
export default function MetricCard({ label, value, hint, tone = "neutral", icon: Icon, mono = false }) {
  return (
    <article className={`cv-metric is-${tone}`}>
      <div className="cv-metric-head">
        <span className="cv-metric-label">{label}</span>
        {Icon && <Icon size={16} aria-hidden="true" />}
      </div>
      <div className={`cv-metric-value${mono ? " is-mono" : ""}`}>{value}</div>
      {hint && <div className="cv-metric-hint">{hint}</div>}
    </article>
  );
}
