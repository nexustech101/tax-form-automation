import { SquarePen } from "lucide-react";
import { useAssistant } from "../../app/assistant";
import { Button } from "../../components/Button";
import { Chat } from "./Chat";
import styles from "./AssistantPage.module.css";

/** Full-page assistant (fullBleed): the conversation scrolls in its own column; "New chat" once there is one. */
export function AssistantPage() {
  const { turns, reset } = useAssistant();
  return (
    <div className={styles.page}>
      {turns.length > 0 && (
        <header className={styles.bar}>
          <Button variant="ghost" size="sm" icon={<SquarePen />} onClick={reset}>
            New chat
          </Button>
        </header>
      )}
      <Chat variant="page" />
    </div>
  );
}
