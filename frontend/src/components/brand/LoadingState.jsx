import { useEffect, useState } from "react";

import { LOGOS, loaderSrc } from "../../brand/assets.js";

/*
 * Reusable loader.
 *  - The GIF is only requested when this component mounts.
 *  - Under prefers-reduced-motion the GIF is not rendered (so not downloaded
 *    either) and the static brand mark is shown instead.
 *
 * type: flow | converge | orbit | dots | bar
 */
const SIZE = { flow: 120, converge: 112, orbit: 40, dots: 48, bar: 0 };
const QUERY = "(prefers-reduced-motion: reduce)";

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(QUERY).matches
  );

  useEffect(() => {
    const media = window.matchMedia?.(QUERY);
    if (!media) return undefined;
    const onChange = (event) => setReduced(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

export default function LoadingState({
  type = "flow",
  surface = "dark",
  label = "Loading…",
  inline = false,
  className = "",
}) {
  const size = SIZE[type];
  const reduced = usePrefersReducedMotion();

  return (
    <div
      className={`cv-loading ${inline ? "is-inline" : ""} ${className}`.trim()}
      role="status"
      aria-live="polite"
    >
      <span className={`cv-loader is-${type}`} aria-hidden="true">
        {reduced ? (
          <img
            className="cv-loader-static"
            src={LOGOS.mark[surface]}
            alt=""
            width={type === "bar" ? 24 : size}
            height={type === "bar" ? 24 : size}
            draggable="false"
          />
        ) : (
          <img
            className="cv-loader-gif"
            src={loaderSrc(type, surface)}
            alt=""
            width={type === "bar" ? 240 : size}
            height={type === "bar" ? 24 : size}
            draggable="false"
          />
        )}
      </span>
      {label && <span className="cv-loading-label">{label}</span>}
    </div>
  );
}
