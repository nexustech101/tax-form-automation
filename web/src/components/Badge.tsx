import type { ReactNode } from "react";
import type { Tone } from "../lib/flags";
import styles from "./Badge.module.css";

type Props = { tone?: Tone; icon?: ReactNode; children: ReactNode; title?: string; dot?: boolean };

export function Badge({ tone = "neutral", icon, children, title, dot }: Props) {
  return (
    <span className={`${styles.badge} ${styles[tone]}`} title={title}>
      {dot && <span className={styles.dot} aria-hidden />}
      {icon}
      {children}
    </span>
  );
}
