import type { SVGProps } from "react";

// The Google Chrome mark, drawn from its geometry: three 120° segments around
// a white ring and a blue centre. Used wherever Eidos refers to Chrome.
const SEGMENTS = [
  { fill: "#EA4335", d: "M5.54 12.03A22 22 0 0 1 43.6 14L24 14 15.34 29Z" },
  { fill: "#FBBC04", d: "M43.6 14A22 22 0 0 1 22.86 45.97L32.66 29 24 14Z" },
  { fill: "#34A853", d: "M22.86 45.97A22 22 0 0 1 5.54 12.03L15.34 29 32.66 29Z" },
];

const MARKUP = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">${SEGMENTS.map(
  (segment) => `<path fill="${segment.fill}" d="${segment.d}"/>`,
).join("")}<circle cx="24" cy="24" r="10" fill="#fff"/><circle cx="24" cy="24" r="8" fill="#1A73E8"/></svg>`;

/** For `<img>` sources such as plugin icons. */
export const chromeIconSrc = `data:image/svg+xml,${encodeURIComponent(MARKUP)}`;

export function ChromeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false" {...props}>
      {SEGMENTS.map((segment) => <path key={segment.fill} fill={segment.fill} d={segment.d} />)}
      <circle cx="24" cy="24" r="10" fill="#fff" />
      <circle cx="24" cy="24" r="8" fill="#1A73E8" />
    </svg>
  );
}
