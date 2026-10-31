import { useId } from "react";
import styles from "./Mark.module.css";

// One ribbon segment (sweeps 160°, outer at its start, inner at its tapered end). Three copies at 120° form a
// twisted loop: each end tucks under the next segment's start, and the shading turns light to dark along it.
const SEGMENT =
  "M0.0 -42.0C-1.2 -41.9 -4.8 -41.4 -7.3 -41.1C-9.7 -40.8 -12.2 -40.7 -14.6 -40.2C-17.1 -39.7 -19.7 -39.1 -22.0 -38.2C-24.4 -37.2 -26.9 -36.0 -28.9 -34.4C-30.9 -32.8 -32.6 -30.7 -34.0 -28.5C-35.4 -26.4 -36.5 -24.0 -37.5 -21.6C-38.4 -19.3 -39.1 -16.8 -39.6 -14.4C-40.1 -12.0 -40.3 -9.5 -40.4 -7.1C-40.4 -4.7 -40.2 -2.3 -39.9 -0.0C-39.5 2.3 -38.9 4.6 -38.2 6.7C-37.4 8.9 -36.5 11.0 -35.4 12.9C-34.2 14.8 -32.9 16.6 -31.4 18.1C-29.8 19.6 -28.0 20.9 -26.1 21.9C-24.3 23.0 -22.3 23.7 -20.4 24.3C-18.5 24.9 -16.6 25.3 -14.8 25.6C-13.0 25.9 -10.4 26.2 -9.6 26.3L-5.5 15.0C-5.9 14.9 -7.5 14.8 -8.3 14.4C-9.2 14.1 -10.0 13.5 -10.8 12.8C-11.5 12.2 -12.1 11.4 -12.7 10.7C-13.4 10.0 -13.9 9.2 -14.5 8.4C-15.2 7.6 -15.9 6.9 -16.6 6.0C-17.2 5.2 -17.9 4.3 -18.5 3.3C-19.0 2.3 -19.5 1.2 -19.9 -0.0C-20.2 -1.2 -20.5 -2.4 -20.7 -3.6C-20.8 -4.9 -20.9 -6.2 -20.8 -7.6C-20.7 -8.9 -20.5 -10.3 -20.1 -11.6C-19.8 -13.0 -19.2 -14.3 -18.7 -15.7C-18.1 -17.1 -17.5 -18.4 -16.8 -20.0C-16.1 -21.6 -15.5 -23.5 -14.6 -25.3C-13.6 -27.0 -12.6 -29.1 -11.2 -30.8C-9.8 -32.5 -8.1 -34.2 -6.2 -35.4C-4.4 -36.6 -1.0 -37.6 0.0 -38.0Z";
const TURNS = [0, 120, 240] as const;
const HUES = ["var(--mark-from)", "color-mix(in oklab, var(--mark-from), var(--mark-to))", "var(--mark-to)"];

/** The assistant's mark: a three-fold twisted (Möbius) loop in the token gradient (decorative). `busy` adds a slow sheen. */
export function Mark({ size = 20, busy = false, className }: { size?: number; busy?: boolean; className?: string }) {
  const id = useId();
  const shade = `${id}s`;
  const shine = `${id}h`;
  const band = `${id}b`;
  const tip = `${id}t`;
  const segment = (turn: number, i: number) => (
    <g key={turn} transform={`rotate(${turn})`}>
      <path d={SEGMENT} style={{ fill: HUES[i] }} />
      <path d={SEGMENT} fill={`url(#${shade})`} />
    </g>
  );
  return (
    <svg
      className={`${styles.mark} ${busy ? styles.busy : ""} ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="-50 -50 100 100"
      aria-hidden
      focusable="false"
      data-mark=""
    >
      <defs>
        <linearGradient id={shade} gradientUnits="userSpaceOnUse" x1="0" y1="-40" x2="-7.5" y2="20.7">
          <stop offset="0" stopColor="#fff" stopOpacity="0.35" />
          <stop offset="0.4" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.85" className={styles.dark} />
        </linearGradient>
        <linearGradient id={shine} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.5" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <clipPath id={band}>
          {TURNS.map((t) => <path key={t} d={SEGMENT} transform={`rotate(${t})`} />)}
        </clipPath>
        <clipPath id={tip}>
          <circle cx="0" cy="-40" r="26" />
        </clipPath>
      </defs>
      {TURNS.map(segment)}
      {/* The first segment's start again, over the last segment's end, closes the loop. */}
      <g clipPath={`url(#${tip})`}>{segment(0, 0)}</g>
      <g clipPath={`url(#${band})`}>
        <rect className={styles.sheen} x="-90" y="-50" width="40" height="100" fill={`url(#${shine})`} />
      </g>
    </svg>
  );
}
