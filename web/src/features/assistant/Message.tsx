import { Check, ChevronRight, CircleAlert, Copy, RotateCcw, Wrench } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { errorMessage } from "../../api/client";
import { useTypeName } from "../../api/library";
import type { ChatResponse, Source } from "../../api/types";
import { useAssistant, type Turn } from "../../app/assistant";
import { describeTool } from "./chatUtils";
import { splitCitations } from "./citations";
import { Mark } from "./Mark";
import { parseMarkdown, type Inline } from "./markdown";
import styles from "./Message.module.css";

export function UserMessage({ text }: { text: string }) {
  return (
    <div className={styles.userRow}>
      <div className={styles.bubble}>{text}</div>
    </div>
  );
}

/** An answer: text with citation chips, the cited sources, then the tools that ran and a copy action. */
export function AssistantMessage({ turn, last }: { turn: Extract<Turn, { role: "assistant" }>; last: boolean }) {
  const { scope } = useAssistant();
  const { sources, tool_calls } = turn.reply;
  const titles = new Map([...scope.documents.map((d) => [d.id, d.title] as const), ...sources.map((s) => [s.document_id, s.title] as const)]);
  return (
    <article className={`${styles.assistant} ${last ? styles.last : ""}`} aria-label="Assistant answer">
      <Mark className={styles.avatar} />
      <div className={styles.body}>
        <Answer text={turn.content} sources={sources} />
        <Sources text={turn.content} sources={sources} />
        <footer className={styles.footer}>
          <ToolActivity calls={tool_calls} titles={titles} />
          <CopyButton text={turn.content} />
        </footer>
      </div>
    </article>
  );
}

/** Shown while the request runs. The API answers once, so the wording follows the scope, with the elapsed time. */
export function Thinking() {
  const { scope } = useAssistant();
  const seconds = useSeconds();
  const label = scope.documents.length === 1 ? `Reading ${scope.documents[0]!.title}…` : "Searching documents…";
  return (
    <div className={styles.assistant} role="status">
      <Mark busy className={styles.avatar} />
      <div className={styles.thinking}>
        <span className={styles.shimmer}>{label}</span>
        {seconds >= 5 && <span className={`${styles.elapsed} num`}>{seconds}s</span>}
      </div>
    </div>
  );
}

export function Failure() {
  const { error, stopped, retry } = useAssistant();
  return (
    <div className={styles.assistant}>
      <span className={styles.avatar} />
      {stopped ? (
        <div className={styles.stopped}>
          Stopped.
          <button type="button" className={styles.textButton} onClick={retry}>
            <RotateCcw aria-hidden /> Retry
          </button>
        </div>
      ) : (
        <div className={styles.error} role="alert">
          <CircleAlert aria-hidden />
          <span>
            <strong>No answer.</strong> {errorMessage(error)}
          </span>
          <button type="button" className={styles.textButton} onClick={retry}>
            <RotateCcw aria-hidden /> Retry
          </button>
        </div>
      )}
    </div>
  );
}

function useSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);
  return seconds;
}

function Answer({ text, sources }: { text: string; sources: Source[] }) {
  const byNumber = new Map(sources.map((s) => [s.n, s]));
  const known = new Set(byNumber.keys());
  const inline = (parts: Inline[]) =>
    parts.map((part, i) => {
      if ("code" in part) return <code key={i}>{part.code}</code>;
      const content = splitCitations(part.text, known).map((p, j) =>
        "text" in p ? p.text : <Citation key={`c${j}`} source={byNumber.get(p.cite)!} />,
      );
      return part.bold ? <strong key={i}>{content}</strong> : <span key={i}>{content}</span>;
    });
  return (
    <div className={styles.answer}>
      {parseMarkdown(text).map((block, i): ReactNode => {
        switch (block.kind) {
          case "p":
            return <p key={i}>{inline(block.inlines)}</p>;
          case "h":
            return <h4 key={i}>{inline(block.inlines)}</h4>;
          case "ul":
            return <ul key={i}>{block.items.map((item, j) => <li key={j}>{inline(item)}</li>)}</ul>;
          case "ol":
            return (
              <ol key={i} start={block.start}>
                {block.items.map((item, j) => (
                  <li key={j}>{inline(item)}</li>
                ))}
              </ol>
            );
          case "code":
            return <pre key={i}>{block.text}</pre>;
        }
      })}
    </div>
  );
}

function where(source: Source): string | null {
  return source.page_number ? `p. ${source.page_number}` : source.sheet_name;
}

function useOpen(source: Source) {
  const navigate = useNavigate();
  return () => navigate(`/documents/${source.document_id}${source.page_number ? `?page=${source.page_number}` : ""}`);
}

/** `[n]` in the answer: a small chip naming the source on hover; opens the document at the page. */
function Citation({ source }: { source: Source }) {
  const open = useOpen(source);
  const label = [source.title, where(source)].filter(Boolean).join(" · ");
  return (
    <button type="button" className={styles.cite} onClick={open} data-tip={label} aria-label={`Source ${source.n}: ${label}`} data-testid="citation">
      {source.n}
    </button>
  );
}

/** The cited excerpts as cards; the others the search returned behind "n more". */
function Sources({ text, sources }: { text: string; sources: Source[] }) {
  const [more, setMore] = useState(false);
  if (!sources.length) return null;
  const cited = new Set(splitCitations(text, new Set(sources.map((s) => s.n))).flatMap((p) => ("cite" in p ? [p.cite] : [])));
  const shown = sources.filter((s) => cited.has(s.n));
  const rest = sources.filter((s) => !cited.has(s.n));
  return (
    <section className={styles.sources} aria-label="Sources">
      <h4 className={styles.sourcesLabel}>Sources</h4>
      <div className={styles.sourceRow}>
        {shown.map((s) => (
          <SourceCard key={s.n} source={s} />
        ))}
        {more && rest.map((s) => <SourceCard key={s.n} source={s} />)}
        {rest.length > 0 && (
          <button type="button" className={styles.moreSources} aria-expanded={more} onClick={() => setMore(!more)}>
            {more ? "Show cited only" : shown.length ? `+${rest.length} more retrieved` : `${rest.length} excerpts retrieved`}
          </button>
        )}
      </div>
    </section>
  );
}

function SourceCard({ source }: { source: Source }) {
  const open = useOpen(source);
  const typeName = useTypeName();
  const meta = [source.document_type !== "text" ? typeName(source.document_type) : null, where(source)].filter(Boolean).join(" · ");
  return (
    <button type="button" className={styles.source} onClick={open} title={`${source.title}${source.client ? ` (${source.client})` : ""}`} data-testid="source">
      <span className={`${styles.sourceN} num`}>{source.n}</span>
      <span className={styles.sourceText}>
        <span className={`${styles.sourceTitle} truncate`}>{source.title}</span>
        <span className={`${styles.sourceMeta} truncate`}>{meta || source.section_title}</span>
      </span>
    </button>
  );
}

/** "Searched “wages” · Read w2", expandable to each call with its arguments. */
function ToolActivity({ calls, titles }: { calls: ChatResponse["tool_calls"]; titles: Map<string, string> }) {
  if (!calls.length) return <span className={styles.spacer} />;
  return (
    <details className={styles.activity}>
      <summary data-testid="tool-activity">
        <Wrench aria-hidden />
        <span className="truncate">{calls.map((c) => describeTool(c, titles)).join(" · ")}</span>
        <ChevronRight className={styles.disclosure} aria-hidden />
      </summary>
      <ol className={styles.calls}>
        {calls.map((call, i) => (
          <li key={i}>
            <code className={styles.callName}>{call.name}</code>
            <code className={styles.callArgs}>{JSON.stringify(call.arguments)}</code>
          </li>
        ))}
      </ol>
    </details>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      className={styles.action}
      aria-label={copied ? "Copied" : "Copy answer"}
      title={copied ? "Copied" : "Copy"}
      onClick={() => navigator.clipboard?.writeText(text).then(() => setCopied(true), () => {})}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
    </button>
  );
}
