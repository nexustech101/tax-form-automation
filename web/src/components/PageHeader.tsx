import type { ReactNode } from "react";
import styles from "./PageHeader.module.css";

type Props = { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode };

export function PageHeader({ title, description, actions, eyebrow }: Props) {
  return (
    <header className={styles.header}>
      <div className={styles.text}>
        {eyebrow && <div className={styles.eyebrow}>{eyebrow}</div>}
        <h1 className="truncate">{title}</h1>
        {description && <p className={styles.description}>{description}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
