import { AlertTriangle, CircleAlert, Info } from "lucide-react";
import type { ReactNode } from "react";
import type { Tone } from "../lib/flags";
import styles from "./Alert.module.css";

type Props = { tone?: Extract<Tone, "danger" | "warning" | "info">; title?: ReactNode; children?: ReactNode; action?: ReactNode };

const ICONS = { danger: CircleAlert, warning: AlertTriangle, info: Info };

export function Alert({ tone = "danger", title, children, action }: Props) {
  const Icon = ICONS[tone];
  return (
    <div className={`${styles.alert} ${styles[tone]}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon className={styles.icon} aria-hidden />
      <div className={styles.content}>
        {title && <p className={styles.title}>{title}</p>}
        {children && <div className={styles.text}>{children}</div>}
      </div>
      {action}
    </div>
  );
}
