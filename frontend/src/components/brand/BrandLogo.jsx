import { LOGOS } from "../../brand/assets.js";

/*
 * Supplied production logo assets only - never recoloured or redrawn.
 * variant: "lockup" (mark + wordmark) | "mark"; surface: "dark" | "light".
 */
export default function BrandLogo({
  variant = "lockup",
  surface = "dark",
  height = 32,
  className = "",
  decorative = false,
}) {
  return (
    <img
      className={`brand-logo ${className}`.trim()}
      src={LOGOS[variant][surface]}
      alt={decorative ? "" : "Convergence"}
      height={height}
      style={{ height, width: "auto" }}
      draggable="false"
    />
  );
}
