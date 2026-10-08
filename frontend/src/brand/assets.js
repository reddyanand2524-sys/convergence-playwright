// Single map of Convergence brand assets served from /public/assets/brand.
const BASE = "/assets/brand";

export const LOGOS = {
  lockup: { dark: `${BASE}/logos/lockup-dark-bg-transparent.png`, light: `${BASE}/logos/lockup-light-bg-transparent.png` },
  mark: { dark: `${BASE}/logos/mark-dark-bg-transparent.png`, light: `${BASE}/logos/mark-light-bg-transparent.png` },
};

// type -> loader GIF. "dark"/"light" is the surface the loader sits on.
// Loader semantics: flow = page/query, converge = run analysis, orbit = inline
// action, dots = streaming, bar = top-of-page progress.
export const LOADERS = ["flow", "converge", "orbit", "dots", "bar"];

export function loaderSrc(type, surface = "dark") {
  return `${BASE}/loaders-transparent/loader-${type}-for-${surface}-bg.gif`;
}

export function splashSrc(surface = "dark") {
  return `${BASE}/loaders-transparent/splash-flow-for-${surface}-bg.gif`;
}
