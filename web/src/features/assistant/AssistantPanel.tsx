import { Maximize2, SquarePen, X } from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router";
import { useAssistant } from "../../app/assistant";
import { Button } from "../../components/Button";
import { Chat } from "./Chat";
import { Mark } from "./Mark";
import styles from "./AssistantPanel.module.css";

/** The slide-over (non-modal: the page stays usable beside it). Esc closes it unless a menu handled it. */
export function AssistantPanel() {
  const { closePanel, turns, reset } = useAssistant();
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !(e.target instanceof Element && e.target.closest("dialog"))) closePanel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closePanel]);
  return (
    <aside className={styles.panel} aria-label="Assistant">
      <header className={styles.header}>
        <h2>
          <Mark size={16} /> Assistant
        </h2>
        <div className={styles.actions}>
          {turns.length > 0 && (
            <Button variant="ghost" size="sm" icon={<SquarePen />} aria-label="New chat" title="New chat" onClick={reset} />
          )}
          <Button
            variant="ghost"
            size="sm"
            icon={<Maximize2 />}
            aria-label="Open full page"
            title="Open full page"
            onClick={() => {
              closePanel();
              navigate("/assistant");
            }}
          />
          <Button variant="ghost" size="sm" icon={<X />} aria-label="Close assistant" title="Close" onClick={closePanel} />
        </div>
      </header>
      <Chat variant="panel" />
    </aside>
  );
}
