const VERSION = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "";

function apiHost() {
  try {
    return new URL(import.meta.env.VITE_API_BASE_URL).host;
  } catch {
    return "";
  }
}

const HEALTH = {
  healthy: { tone: "ok", label: "All systems operational" },
  checking: { tone: "build", label: "Checking backend…" },
  unhealthy: { tone: "warn", label: "Backend reports a problem" },
  unreachable: { tone: "crit", label: "Backend unreachable" },
};

/*
 * 32px footer/status bar. Only shows real values: backend health (when the
 * caller knows it), API host from VITE_API_BASE_URL and the app version.
 */
export default function StatusBar({ health }) {
  const known = health ? HEALTH[health] : null;
  const host = apiHost();

  return (
    <footer className="cv-statusbar">
      <div className="cv-statusbar-group">
        <span>© {new Date().getFullYear()} Convergence</span>
        {known && (
          <span className={`cv-statusbar-health is-${known.tone}`} role="status">
            <span aria-hidden="true" />
            {known.label}
          </span>
        )}
        {(host || VERSION) && (
          <span className="cv-statusbar-meta">
            {[host, VERSION && `v${VERSION}`].filter(Boolean).join(" · ")}
          </span>
        )}
      </div>
    </footer>
  );
}
