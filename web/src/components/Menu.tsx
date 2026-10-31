import { Check } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import styles from "./Menu.module.css";

const ITEMS = '[role^="menuitem"]:not(:disabled)';

type Props = {
  /** Accessible name of the trigger and the menu. */
  label: string;
  /** Trigger content; receives whether the menu is open. */
  trigger: (open: boolean) => ReactNode;
  triggerClassName?: string;
  title?: string;
  disabled?: boolean;
  /** Opens above the trigger (composers docked at the bottom). */
  placement?: "top" | "bottom";
  align?: "start" | "end";
  className?: string;
  onOpenChange?: (open: boolean) => void;
  children: (close: () => void) => ReactNode;
};

/**
 * A button with a popover menu. Opening focuses the first field or item; arrows, Home and End move between items;
 * Esc closes and returns focus to the trigger; Tab or a click outside closes.
 */
export function Menu({ label, trigger, triggerClassName, title, disabled, placement = "bottom", align = "start", className, onOpenChange, children }: Props) {
  const [open, setOpenState] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const id = useId();

  const setOpen = useCallback(
    (next: boolean) => {
      setOpenState(next);
      onOpenChange?.(next);
    },
    [onOpenChange],
  );
  const close = useCallback(() => {
    setOpen(false);
    button.current?.focus();
  }, [setOpen]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open, setOpen]);

  useEffect(() => {
    if (open) popover.current?.querySelector<HTMLElement>(`input, ${ITEMS}`)?.focus();
  }, [open]);

  const keyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === "Tab") {
      setOpen(false);
      return;
    }
    if ((e.key === "Home" || e.key === "End") && e.target instanceof HTMLInputElement) return;
    const items = [...(popover.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])];
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    const move = { ArrowDown: index + 1, ArrowUp: index < 0 ? items.length - 1 : index - 1, Home: 0, End: items.length - 1 }[e.key];
    if (move === undefined) return;
    e.preventDefault();
    items[(move + items.length) % items.length]?.focus();
  };

  return (
    <div ref={wrap} className={[styles.wrap, className].filter(Boolean).join(" ")}>
      <button
        ref={button}
        type="button"
        className={triggerClassName}
        aria-label={label}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {trigger(open)}
      </button>
      {open && (
        <div
          ref={popover}
          id={id}
          role="menu"
          aria-label={label}
          className={`${styles.popover} ${styles[placement]} ${styles[align]}`}
          onKeyDown={keyDown}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

type ItemProps = ButtonHTMLAttributes<HTMLButtonElement> & { icon?: ReactNode; hint?: ReactNode; checked?: boolean };

/** A menu entry; `checked` makes it a radio item with a check mark. */
export function MenuItem({ icon, hint, checked, children, className, ...rest }: ItemProps) {
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemradio"}
      aria-checked={checked}
      tabIndex={-1}
      {...rest}
      className={[styles.item, className].filter(Boolean).join(" ")}
    >
      {icon && <span className={styles.icon}>{icon}</span>}
      <span className={styles.label}>{children}</span>
      {hint && <span className={styles.hint}>{hint}</span>}
      {checked && <Check className={styles.check} aria-hidden />}
    </button>
  );
}

export function MenuSeparator() {
  return <div className={styles.separator} role="separator" />;
}

export function MenuHeading({ children }: { children: ReactNode }) {
  return <div className={styles.heading}>{children}</div>;
}
