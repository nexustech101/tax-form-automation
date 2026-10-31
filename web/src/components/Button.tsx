import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";
import styles from "./Button.module.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: ReactNode;
  loading?: boolean;
};

export function Button({ variant = "secondary", size = "md", icon, loading, children, className, ...rest }: Props) {
  const iconOnly = !children;
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || loading}
      className={[styles.button, styles[variant], styles[size], iconOnly && styles.iconOnly, className]
        .filter(Boolean)
        .join(" ")}
    >
      {loading ? <Spinner size={14} /> : icon}
      {children}
    </button>
  );
}
