import type { ReactNode } from "react";
import styles from "./Segmented.module.css";

type Option<T extends string> = { value: T; label: ReactNode; title?: string };
type Props<T extends string> = { value: T; onChange: (value: T) => void; options: Option<T>[]; label: string };

/** A radio group drawn as joined buttons. */
export function Segmented<T extends string>({ value, onChange, options, label }: Props<T>) {
  return (
    <div className={styles.group} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          title={o.title}
          className={styles.option}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
