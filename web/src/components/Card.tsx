import type { ReactNode } from "react";
import styles from "./Card.module.css";

type Props = { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string };

/** A bordered surface; `flush` drops the body padding (tables, lists). */
export function Card({ title, actions, children, flush, className }: Props) {
  return (
    <section className={[styles.card, className].filter(Boolean).join(" ")}>
      {(title || actions) && (
        <header className={styles.header}>
          {title && <h2 className={styles.title}>{title}</h2>}
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={flush ? styles.flush : styles.body}>{children}</div>
    </section>
  );
}
