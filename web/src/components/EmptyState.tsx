import type { ReactNode } from "react";
import styles from "./EmptyState.module.css";

type Props = { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode; compact?: boolean };

export function EmptyState({ icon, title, children, action, compact }: Props) {
  return (
    <div className={`${styles.empty} ${compact ? styles.compact : ""}`}>
      {icon && <div className={styles.icon}>{icon}</div>}
      <p className={styles.title}>{title}</p>
      {children && <p className={styles.text}>{children}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
