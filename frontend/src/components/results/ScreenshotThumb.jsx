import { useEffect, useState } from "react";
import LoadingState from "../brand/LoadingState.jsx";
import { ImageOff, X } from "lucide-react";

import { getScreenshotBlob } from "../../services/runsApi.js";

/*
 * Loads one screenshot through the authenticated API as a Blob and shows it
 * via an object URL (revoked on unmount). Mounted only while its test card is
 * expanded, so images are not downloaded until the user asks to see them.
 */
export default function ScreenshotThumb({ projectId, runId, screenshot }) {
  // status: "loading" | "ready" | "error"
  const [state, setState] = useState({ status: "loading", src: "" });
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setState({ status: "loading", src: "" });

    getScreenshotBlob(projectId, runId, screenshot.id, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setState({ status: "ready", src: objectUrl });
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({ status: "error", src: "" });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [projectId, runId, screenshot.id]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const label = screenshot.capture_reason
    ? `Screenshot (${screenshot.capture_reason})`
    : "Screenshot";

  if (state.status === "loading") {
    return <span className="home-shot is-loading"><LoadingState type="orbit" inline label="Loading screenshot…" /></span>;
  }

  if (state.status === "error") {
    return (
      <span className="home-shot is-error" role="alert">
        <ImageOff size={15} aria-hidden="true" /> Screenshot could not be loaded
      </span>
    );
  }

  return (
    <>
      <button
        type="button"
        className="home-shot-thumb"
        aria-label={`View larger: ${label}`}
        onClick={() => setOpen(true)}
      >
        <img src={state.src} alt={label} />
        <span>{screenshot.capture_reason || "Screenshot"}</span>
      </button>

      {open && (
        <div
          className="home-modal-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}
        >
          <section className="home-lightbox" role="dialog" aria-modal="true" aria-label={label}>
            <button
              type="button"
              className="home-icon-button home-lightbox-close"
              aria-label="Close screenshot"
              onClick={() => setOpen(false)}
            >
              <X size={20} aria-hidden="true" />
            </button>
            <img src={state.src} alt={label} />
            <p>{screenshot.file_name}</p>
          </section>
        </div>
      )}
    </>
  );
}
