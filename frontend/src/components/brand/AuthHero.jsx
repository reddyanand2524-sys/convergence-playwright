import BrandLogo from "./BrandLogo.jsx";

// Flow geometry from the Convergence mark (100x100 viewBox).
const PATHS = {
  g: "M14 22 C40 22 38 50 62 50",
  t: "M14 50 L62 50",
  b: "M14 78 C40 78 38 50 62 50",
  o: "M62 50 L80 50",
};
const LAYERS = [
  { key: "g", colour: "var(--color-design-green)", cx: 14, cy: 22, delay: "0s" },
  { key: "t", colour: "var(--color-operate-teal)", cx: 14, cy: 50, delay: "0.15s" },
  { key: "b", colour: "var(--color-build-blue)", cx: 14, cy: 78, delay: "0.3s" },
];
const TAGS = [
  { label: "Testing", tone: "green" },
  { label: "Observability", tone: "blue" },
  { label: "Logs", tone: "teal" },
  { label: "Security", tone: "neutral" },
];

function FlowHero() {
  return (
    <svg className="cv-hero-svg" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
      {LAYERS.map(({ key, colour, cx, cy, delay }) => (
        <g key={key} style={{ color: colour }}>
          <path className="cv-flow-base" d={PATHS[key]} />
          <path
            className="cv-flow-dash"
            d={PATHS[key]}
            pathLength="100"
            style={{ animationDelay: delay }}
          />
          <circle cx={cx} cy={cy} r="5" fill="currentColor" />
        </g>
      ))}
      <path className="cv-flow-base is-out" d={PATHS.o} />
      <path className="cv-flow-dash is-out" d={PATHS.o} pathLength="100" style={{ animationDelay: "0.45s" }} />
      <circle className="cv-flow-node" cx="84" cy="50" r="8" fill="#fff" />
    </svg>
  );
}

/* Left (navy) panel shared by the auth screens. */
export default function AuthHero({
  headline = "Every signal, converged.",
  copy = "Tests, traces, logs and security events in one pipeline — so you see the cause before the symptom.",
}) {
  return (
    <div className="cv-hero">
      <div className="cv-hero-glow" aria-hidden="true" />
      <BrandLogo variant="lockup" surface="dark" height={34} />

      <div className="cv-hero-body">
        <FlowHero />
        <div className="cv-hero-copy">
          <h2>{headline}</h2>
          <p>{copy}</p>
        </div>
      </div>

      <ul className="cv-hero-tags" aria-label="Platform capabilities">
        {TAGS.map(({ label, tone }) => (
          <li key={label} className={`cv-tag is-${tone}`}>
            {label}
          </li>
        ))}
      </ul>
    </div>
  );
}
