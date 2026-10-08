import { useEffect, useMemo, useState } from "react";
import { Info, MonitorPlay } from "lucide-react";
import LoadingState from "./brand/LoadingState.jsx";

/*
 * Read-only live view of the browser the backend is driving for a Run.
 *
 * It embeds noVNC's minimal "lite" page from the backend container
 * (Xvfb -> x11vnc -> websockify). That page has no control panel; its one
 * status bar is cropped out by .cv-live-frame, so only the browser is visible.
 * The iframe exists only while the run is RUNNING, so no VNC / websocket
 * connection is held before or after execution. noVNC is started with
 * view_only, which ignores all mouse, keyboard and Ctrl-Alt-Del input, and this
 * component never calls the Run API: closing or leaving the page cannot affect
 * the Selenium run.
 *
 * Limitation: the page is cross-origin, so we can only tell that it was
 * reachable and loaded - not that its VNC websocket actually connected. The
 * "Live browser" state therefore never claims a confirmed connection.
 *
 * The backend has ONE shared virtual display, so this shows that display - if
 * several runs execute at once, their browsers all appear here.
 *
 * Viewer URL: VITE_LIVE_BROWSER_URL if set (a page URL, or a directory URL
 * ending in "/", to which vnc_lite.html is added), otherwise port 6080 on the
 * host the app was opened from. noVNC is published on 127.0.0.1 only, so it is
 * reachable from the machine running Docker and nowhere else.
 */
const VIEWER_PAGE = "vnc_lite.html";
const VIEWER_PARAMS = { view_only: "true", scale: "true" };

function defaultViewerBase() {
  return `${window.location.protocol}//${window.location.hostname}:6080/`;
}

// Returns { page, src } (page = URL without query, for the reachability probe),
// or null when the configured value is not a usable http(s) URL.
export function buildViewerUrl(configured, fallbackBase = defaultViewerBase()) {
  try {
    const url = new URL(configured?.trim() || fallbackBase);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.pathname.endsWith("/")) url.pathname += VIEWER_PAGE;
    const page = `${url.origin}${url.pathname}`;
    for (const [key, value] of Object.entries(VIEWER_PARAMS)) url.searchParams.set(key, value);
    return { page, src: url.toString() };
  } catch {
    return null;
  }
}

export default function LiveBrowserViewer({ run }) {
  const running = run.status === "RUNNING";
  const queued = run.status === "QUEUED";
  const viewer = useMemo(() => buildViewerUrl(import.meta.env.VITE_LIVE_BROWSER_URL), []);
  // "checking" | "ready" | "unavailable"; one reachability probe per viewing, no polling.
  const [link, setLink] = useState(viewer ? "checking" : "unavailable");
  const [framed, setFramed] = useState(false);

  useEffect(() => {
    if (!running) return undefined;
    if (!viewer) {
      setLink("unavailable");
      return undefined;
    }
    const controller = new AbortController();
    setLink("checking");
    setFramed(false);
    // no-cors: we only need to know whether the viewer answers at all.
    fetch(viewer.page, { mode: "no-cors", signal: controller.signal })
      .then(() => setLink("ready"))
      .catch((err) => {
        if (err?.name !== "AbortError") setLink("unavailable");
      });
    return () => controller.abort();
  }, [running, viewer]);

  if (!running && !queued) return null;

  const showFrame = running && link === "ready";
  const connecting = running && (link === "checking" || (showFrame && !framed));

  return (
    <section className="cv-live" aria-label="Live browser">
      <div className="cv-live-head">
        <h4><MonitorPlay size={16} aria-hidden="true" /> Live browser</h4>
        {showFrame && <span className="cv-live-note">{run.browser} &middot; view only</span>}
      </div>

      <p className="cv-live-warning" role="note">
        <Info size={15} aria-hidden="true" />
        <span>
          Live browser uses the shared automation display. If multiple tests run at the
          same time, their browser windows may appear here.
        </span>
      </p>

      {queued && <p className="cv-live-state" role="status">Waiting for browser...</p>}

      {running && link === "unavailable" && (
        <p className="cv-live-state is-error" role="status">
          Live browser unavailable. The test keeps running.
        </p>
      )}

      {showFrame && (
        <div className="cv-live-frame">
          {connecting && (
            <div className="cv-live-overlay" role="status">
              <LoadingState type="orbit" inline label="Connecting to live browser..." />
            </div>
          )}
          <iframe
            title={`Live ${run.browser} browser for run ${run.id}`}
            src={viewer.src}
            onLoad={() => setFramed(true)}
            referrerPolicy="no-referrer"
          />
        </div>
      )}

      {running && link === "checking" && (
        <p className="cv-live-state" role="status">Connecting to live browser...</p>
      )}
    </section>
  );
}
