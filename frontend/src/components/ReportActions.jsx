import { useEffect, useRef, useState } from "react";
import { ChevronDown, FileDown } from "lucide-react";

import {
  REPORT_FORMATS,
  downloadExcelReport,
  downloadHtmlReport,
  downloadPdfReport,
} from "../services/runsApi.js";
import { describeError } from "../services/authedFetch.js";

const DOWNLOADERS = {
  html: downloadHtmlReport,
  excel: downloadExcelReport,
  pdf: downloadPdfReport,
};

const UNAVAILABLE = "Reports are available only for completed or failed runs.";

// Saves a Blob as a file without leaving the page.
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to start the download before releasing the blob.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/*
 * "Reports" menu for ONE run (HTML / Excel / PDF). Always uses the projectId and
 * run.id it was rendered with; parents key it by run so nothing carries over
 * from another run. Availability is a UI hint only: the backend answers 409 for
 * runs that are not COMPLETED/FAILED and that is handled too.
 */
export default function ReportActions({ projectId, run }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState({}); // format -> true while downloading
  const [notice, setNotice] = useState(null); // { kind: "info" | "success" | "error", text }
  const inFlight = useRef({}); // per-format guard against double clicks
  const rootRef = useRef(null);

  const available = run.status === "COMPLETED" || run.status === "FAILED";
  const busy = Object.values(pending).some(Boolean);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function download(format) {
    if (inFlight.current[format]) return; // one request per format at a time
    inFlight.current[format] = true;

    const label = REPORT_FORMATS[format].label;
    setOpen(false);
    setPending((prev) => ({ ...prev, [format]: true }));
    setNotice({ kind: "info", text: `Generating ${label} report...` });

    try {
      const { blob, filename } = await DOWNLOADERS[format](projectId, run.id);
      saveBlob(blob, filename);
      setNotice({ kind: "success", text: `${label} report downloaded: ${filename}` });
    } catch (err) {
      setNotice({ kind: "error", text: describeError(err, "Project or Run not found.") });
    } finally {
      inFlight.current[format] = false;
      setPending((prev) => ({ ...prev, [format]: false }));
    }
  }

  return (
    <>
    <div className="home-reports" ref={rootRef}>
      <button
        className="home-secondary-button"
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={!available}
        title={available ? `Download reports for Run #${run.id}` : UNAVAILABLE}
        onClick={() => setOpen((value) => !value)}
      >
        <FileDown size={17} aria-hidden="true" />
        {busy ? "Generating..." : "Reports"}
        <ChevronDown size={15} aria-hidden="true" />
      </button>

      {open && (
        <div className="home-reports-menu" role="menu" aria-label={`Reports for Run #${run.id}`}>
          <p className="home-reports-title">Run #{run.id}</p>
          {Object.entries(REPORT_FORMATS).map(([format, spec]) => (
            <button
              key={format}
              type="button"
              role="menuitem"
              disabled={Boolean(pending[format])}
              onClick={() => download(format)}
            >
              {pending[format] ? `Generating ${spec.label} report...` : `${spec.label} Report`}
            </button>
          ))}
        </div>
      )}
    </div>

    {/* Siblings of .home-reports (not children), so the Reports control stays one
        button tall and lines up with its neighbours; each wraps to its own full row. */}
    {!available && <p className="home-reports-hint">{UNAVAILABLE}</p>}

    {notice && (
      <p
        className={`home-notice is-${notice.kind} home-reports-notice`}
        role={notice.kind === "error" ? "alert" : "status"}
      >
        {notice.text}
      </p>
    )}
    </>
  );
}
