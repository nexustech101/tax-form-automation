import styles from "./Progress.module.css";

export function Progress({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className={styles.track} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <div className={`${styles.bar} ${pct === 100 ? styles.done : ""}`} style={{ width: `${pct}%` }} />
    </div>
  );
}
