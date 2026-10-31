import { CheckCircle2, CircleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import styles from "./Toast.module.css";

type Toast = { id: number; tone: "success" | "danger"; text: string };
type Notify = (text: string, tone?: Toast["tone"]) => void;

const ToastContext = createContext<Notify>(() => {});

export const useToast = () => useContext(ToastContext);

let next = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const notify = useCallback<Notify>(
    (text, tone = "success") => {
      const id = ++next;
      setToasts((all) => [...all.slice(-2), { id, tone, text }]);
      setTimeout(() => dismiss(id), tone === "danger" ? 7000 : 3500);
    },
    [dismiss],
  );
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className={styles.region} role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`${styles.toast} ${styles[t.tone]}`}>
            {t.tone === "success" ? <CheckCircle2 aria-hidden /> : <CircleAlert aria-hidden />}
            <span>{t.text}</span>
            <button className={styles.close} onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
