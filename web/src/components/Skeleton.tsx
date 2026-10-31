import styles from "./Skeleton.module.css";

export function Skeleton({ width = "100%", height = 14 }: { width?: number | string; height?: number }) {
  return <span className={styles.skeleton} style={{ width, height }} aria-hidden />;
}
