import { Banknote, FileSearch, FileText, Flag, Hourglass, ListChecks, type LucideIcon } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef } from "react";
import { Link } from "react-router";
import { useDocuments } from "../../api/library";
import { useAssistant, type Scope } from "../../app/assistant";
import { plural } from "../../lib/format";
import { greeting, isNearBottom } from "./chatUtils";
import { Composer } from "./Composer";
import { Mark } from "./Mark";
import { AssistantMessage, Failure, Thinking, UserMessage } from "./Message";
import styles from "./Chat.module.css";

type Variant = "page" | "panel";
type Suggestion = { icon: LucideIcon; label: string; prompt: string };

const LIBRARY: Suggestion[] = [
  { icon: Flag, label: "Documents with open flags", prompt: "List the documents that still have open flags" },
  { icon: Hourglass, label: "Forms awaiting approval", prompt: "Which clients have forms waiting for approval?" },
  { icon: Banknote, label: "W-2 wages", prompt: "What were the wages, tips and other compensation?" },
];

const DOCUMENT: Suggestion[] = [
  { icon: ListChecks, label: "Summarize", prompt: "Summarize this document: its forms, key amounts and review status." },
  { icon: Flag, label: "What is flagged?", prompt: "Which values are flagged in this document, and why?" },
  { icon: FileSearch, label: "Key amounts", prompt: "What are the key amounts on this document?" },
];

/** The assistant conversation: a welcome until the first question, then the thread with the composer docked below. */
export function Chat({ variant }: { variant: Variant }) {
  const { turns } = useAssistant();
  if (turns.length > 0) return <Conversation variant={variant} />;
  return variant === "page" ? <Welcome /> : <PanelWelcome />;
}

function useSuggestions(): Suggestion[] {
  return useAssistant().scope.documents.length ? DOCUMENT : LIBRARY;
}

function Welcome() {
  const { ask } = useAssistant();
  const suggestions = useSuggestions();
  return (
    <div className={styles.welcome}>
      <div className={styles.welcomeMain}>
        <h1 className={styles.greeting}>
          <Mark size={28} />
          {greeting(new Date().getHours())}
        </h1>
        <Composer placement="bottom" minRows={2} autoFocus />
      </div>
      <div className={styles.welcomeBelow}>
        <div className={styles.pills}>
          {suggestions.map(({ icon: Icon, label, prompt }) => (
            <button key={label} type="button" className={styles.pill} title={prompt} onClick={() => ask(prompt)}>
              <Icon aria-hidden />
              {label}
            </button>
          ))}
        </div>
        <NeedsReview />
      </div>
    </div>
  );
}

/** Up to three documents with open flags; a card starts a chat scoped to its document. */
function NeedsReview() {
  const { ask } = useAssistant();
  const id = useId();
  const { data } = useDocuments({ review_status: "pending", limit: 25 });
  const documents = data?.documents.filter((d) => d.open_flags > 0).slice(0, 3) ?? [];
  if (!documents.length) return null;
  return (
    <section className={styles.review} aria-labelledby={id}>
      <header className={styles.reviewHead}>
        <h2 id={id}>Needs review</h2>
        <Link to="/documents?review_status=pending">View all</Link>
      </header>
      <div className={styles.reviewGrid}>
        {documents.map((d) => {
          const scope: Scope = { documents: [{ id: d.id, title: d.title }] };
          return (
            <button key={d.id} type="button" className={styles.reviewCard} onClick={() => ask("What still needs review in this document?", scope)}>
              <span className={styles.reviewIcon}>
                <FileText aria-hidden />
              </span>
              <span className={styles.reviewText}>
                <span className={`${styles.reviewTitle} truncate`}>{d.title}</span>
                <span className={`${styles.reviewMeta} truncate num`}>
                  {[d.client ?? "No client", d.tax_year].filter(Boolean).join(" · ")}
                </span>
              </span>
              <span className={`${styles.flags} num`}>{plural(d.open_flags, "flag")}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PanelWelcome() {
  const { ask } = useAssistant();
  const suggestions = useSuggestions();
  return (
    <>
      <div className={styles.panelWelcome}>
        <div className={styles.panelIntro}>
          <Mark size={24} />
          <h3>What can I help with?</h3>
          <p>Answers come from stored documents and reviewed values, with sources. Nothing is changed.</p>
        </div>
        <div className={styles.list}>
          {suggestions.map(({ icon: Icon, prompt }) => (
            <button key={prompt} type="button" className={styles.listItem} onClick={() => ask(prompt)}>
              <Icon aria-hidden />
              <span>{prompt}</span>
            </button>
          ))}
        </div>
      </div>
      <Dock variant="panel" />
    </>
  );
}

function Conversation({ variant }: { variant: Variant }) {
  const { turns, pending, error } = useAssistant();
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // Follow new messages unless the user scrolled up; their own question always brings the end back into view.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (turns.at(-1)?.role === "user") stick.current = true;
    if (stick.current) el.scrollTop = el.scrollHeight;
  }, [turns, pending, error]);

  // Also when the column or the viewport changes size (window resize, the composer growing).
  useEffect(() => {
    const el = scroller.current;
    if (!el?.firstElementChild) return;
    const observer = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);

  const lastAnswer = turns.findLastIndex((t) => t.role === "assistant");
  return (
    <div className={`${styles.conversation} ${styles[variant]}`}>
      <div ref={scroller} className={styles.scroll} onScroll={(e) => (stick.current = isNearBottom(e.currentTarget))}>
        <div className={styles.thread} role="log" aria-label="Conversation">
          {turns.map((turn, i) =>
            turn.role === "user" ? <UserMessage key={i} text={turn.content} /> : <AssistantMessage key={i} turn={turn} last={i === lastAnswer} />,
          )}
          {pending && <Thinking />}
          {error && !pending && <Failure />}
        </div>
      </div>
      <Dock variant={variant} />
    </div>
  );
}

function Dock({ variant }: { variant: Variant }) {
  return (
    <div className={`${styles.dock} ${styles[variant]}`}>
      <div className={styles.dockInner}>
        <Composer placement="top" autoFocus />
        {variant === "page" && <p className={styles.note}>Answers come from stored documents and reviewed values. The assistant never changes records.</p>}
      </div>
    </div>
  );
}
