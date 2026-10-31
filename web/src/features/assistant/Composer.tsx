import { ArrowLeft, ArrowUp, CalendarDays, ChevronDown, ChevronRight, CircleAlert, FileText, FolderOpen, Plus, Search, Square, Upload, Users, X } from "lucide-react";
import { useDeferredValue, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { errorMessage } from "../../api/client";
import { useDocuments, useStats, useUploadDocument } from "../../api/library";
import { useAssistant, withDocument, type ScopedDocument } from "../../app/assistant";
import { Menu, MenuHeading, MenuItem, MenuSeparator } from "../../components/Menu";
import { Spinner } from "../../components/Spinner";
import styles from "./Composer.module.css";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt,.md,.csv,.xlsx";
const LINE = 24;
const MAX_ROWS = 8;

type Props = {
  /** Where menus open: above a composer docked at the bottom, below one in the middle of the page. */
  placement: "top" | "bottom";
  minRows?: number;
  autoFocus?: boolean;
};

/**
 * The question box shared by the page and the panel: auto-growing textarea (Enter sends, Shift+Enter breaks the
 * line), the "+" menu (upload or attach a document), scope pills or document chips, and send / stop.
 */
export function Composer({ placement, minRows = 1, autoFocus }: Props) {
  const { ask, pending, stop, scope, setScope } = useAssistant();
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const upload = useUploadDocument();
  const uploading = upload.isPending ? (upload.variables?.file.name ?? "") : null;
  const canSend = text.trim() !== "" && !pending && uploading === null;

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    const max = LINE * MAX_ROWS + el.offsetHeight - el.clientHeight + paddingY(el);
    el.style.height = `${Math.min(el.scrollHeight + el.offsetHeight - el.clientHeight, max)}px`;
    el.style.overflowY = el.scrollHeight + el.offsetHeight - el.clientHeight > max ? "auto" : "hidden";
  }, [text]);

  useEffect(() => {
    if (autoFocus) area.current?.focus();
  }, [autoFocus]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!canSend) return;
    ask(text.trim());
    setText("");
  };

  const keyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const attach = (document: ScopedDocument) => {
    setScope((s) => withDocument(s, document));
    area.current?.focus();
  };

  const uploadFile = (file: File) =>
    upload.mutate(
      { file, client: scope.client, tax_year: scope.tax_year },
      {
        onSuccess: (doc) => attach({ id: doc.id, title: doc.title }),
      },
    );

  return (
    <form className={styles.composer} onSubmit={submit} onClick={(e) => e.target === e.currentTarget && area.current?.focus()}>
      <textarea
        ref={area}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={keyDown}
        placeholder={placeholder(scope.documents)}
        rows={minRows}
        aria-label="Question"
        maxLength={20000}
        className={styles.input}
      />
      {upload.isError && (
        <div className={styles.uploadError} role="alert">
          <CircleAlert aria-hidden />
          <span>Upload failed: {errorMessage(upload.error)}</span>
          <button type="button" aria-label="Dismiss" onClick={() => upload.reset()}>
            <X />
          </button>
        </div>
      )}
      <div className={styles.toolbar}>
        <div className={styles.tools}>
          <AddMenu placement={placement} onUpload={uploadFile} onAttach={attach} attached={scope.documents} disabled={uploading !== null} />
          {uploading !== null ? (
            <span className={`${styles.chip} ${styles.uploading}`} role="status">
              <Spinner size={13} />
              <span className="truncate">{uploading}</span>
              <span className={styles.chipNote}>Uploading…</span>
            </span>
          ) : scope.documents.length ? (
            scope.documents.map((d) => (
              <span key={d.id} className={styles.chip} data-testid="scope-document">
                <FileText aria-hidden />
                <span className="truncate" title={d.title}>
                  {d.title}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${d.title}`}
                  onClick={() => setScope((s) => ({ ...s, documents: s.documents.filter((x) => x.id !== d.id) }))}
                >
                  <X />
                </button>
              </span>
            ))
          ) : (
            <>
              <ClientPill placement={placement} />
              <YearPill placement={placement} />
            </>
          )}
        </div>
        {pending ? (
          <button type="button" className={`${styles.send} ${styles.stop}`} onClick={stop} aria-label="Stop" title="Stop">
            <Square aria-hidden />
          </button>
        ) : (
          <button type="submit" className={styles.send} disabled={!canSend} aria-label="Send" title="Send (Enter)">
            <ArrowUp aria-hidden />
          </button>
        )}
      </div>
    </form>
  );
}

function placeholder(documents: ScopedDocument[]): string {
  if (documents.length === 1) return `Ask about ${documents[0]!.title}`;
  if (documents.length > 1) return "Ask about these documents";
  return "Ask about clients, forms or open flags";
}

function paddingY(el: HTMLElement): number {
  const style = getComputedStyle(el);
  return parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
}

type AddMenuProps = {
  placement: "top" | "bottom";
  onUpload: (file: File) => void;
  onAttach: (document: ScopedDocument) => void;
  attached: ScopedDocument[];
  disabled: boolean;
};

function AddMenu({ placement, onUpload, onAttach, attached, disabled }: AddMenuProps) {
  const [view, setView] = useState<"root" | "attach">("root");
  const file = useRef<HTMLInputElement>(null);
  return (
    <>
      <Menu
        label="Add a document"
        title="Add a document"
        placement={placement}
        disabled={disabled}
        triggerClassName={styles.plus}
        trigger={() => <Plus aria-hidden />}
        onOpenChange={(open) => !open && setView("root")}
      >
        {(close) =>
          view === "root" ? (
            <>
              <MenuItem
                icon={<Upload />}
                onClick={() => {
                  close();
                  file.current?.click();
                }}
              >
                Upload a document
              </MenuItem>
              <MenuItem icon={<FolderOpen />} hint={<ChevronRight className={styles.hintIcon} aria-hidden />} onClick={() => setView("attach")}>
                Attach a library document
              </MenuItem>
            </>
          ) : (
            <DocumentPicker
              attached={attached}
              onBack={() => setView("root")}
              onPick={(d) => {
                onAttach(d);
                close();
              }}
            />
          )
        }
      </Menu>
      <input
        ref={file}
        type="file"
        accept={ACCEPT}
        hidden
        data-testid="assistant-upload"
        onChange={(e) => {
          const picked = e.target.files?.[0];
          e.target.value = "";
          if (picked) onUpload(picked);
        }}
      />
    </>
  );
}

function DocumentPicker({ attached, onPick, onBack }: { attached: ScopedDocument[]; onPick: (d: ScopedDocument) => void; onBack: () => void }) {
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const { data, isPending } = useDocuments({ q: query || undefined, limit: 8 });
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const ids = new Set(attached.map((d) => d.id));
  const documents = data?.documents ?? [];
  return (
    <div className={styles.picker}>
      <div className={styles.pickerHead}>
        <button type="button" className={styles.back} aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </button>
        <label className={styles.search}>
          <Search aria-hidden />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              const first = documents.find((d) => !ids.has(d.id));
              if (e.key === "Enter" && first) {
                e.preventDefault();
                onPick({ id: first.id, title: first.title });
              }
            }}
            placeholder="Search documents"
            aria-label="Search documents"
          />
        </label>
      </div>
      <MenuSeparator />
      {isPending ? (
        <div className={styles.pickerNote}>
          <Spinner size={13} /> Loading…
        </div>
      ) : documents.length === 0 ? (
        <div className={styles.pickerNote}>{query ? "No matching documents" : "No documents yet"}</div>
      ) : (
        <>
          <MenuHeading>{query ? "Matching documents" : "Recent documents"}</MenuHeading>
          {documents.map((d) => (
            <MenuItem
              key={d.id}
              icon={<FileText />}
              hint={[d.client, d.tax_year].filter(Boolean).join(" · ") || undefined}
              disabled={ids.has(d.id)}
              onClick={() => onPick({ id: d.id, title: d.title })}
            >
              {d.title}
            </MenuItem>
          ))}
        </>
      )}
    </div>
  );
}

function ClientPill({ placement }: { placement: "top" | "bottom" }) {
  const { scope, setScope } = useAssistant();
  const clients = useStats().data?.clients.flatMap((c) => (c.client ? [{ name: c.client, documents: c.documents }] : [])) ?? [];
  const set = (client: string | undefined) => setScope((s) => ({ ...s, client }));
  return (
    <Menu
      label={`Client scope: ${scope.client ?? "All clients"}`}
      placement={placement}
      triggerClassName={`${styles.pill} ${scope.client ? styles.pillSet : ""}`}
      trigger={() => (
        <>
          <Users aria-hidden />
          <span className="truncate">{scope.client ?? "All clients"}</span>
          <ChevronDown className={styles.chevron} aria-hidden />
        </>
      )}
    >
      {(close) => (
        <>
          <MenuItem checked={!scope.client} onClick={() => (set(undefined), close())}>
            All clients
          </MenuItem>
          {clients.length > 0 && <MenuSeparator />}
          {clients.map((c) => (
            <MenuItem key={c.name} checked={scope.client === c.name} hint={c.documents} onClick={() => (set(c.name), close())}>
              {c.name}
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  );
}

function YearPill({ placement }: { placement: "top" | "bottom" }) {
  const { scope, setScope } = useAssistant();
  const set = (tax_year: number | undefined) => setScope((s) => ({ ...s, tax_year }));
  const now = new Date().getFullYear();
  return (
    <Menu
      label={`Tax year scope: ${scope.tax_year ?? "Any year"}`}
      placement={placement}
      triggerClassName={`${styles.pill} ${scope.tax_year ? styles.pillSet : ""}`}
      trigger={() => (
        <>
          <CalendarDays aria-hidden />
          <span className="num">{scope.tax_year ?? "Any year"}</span>
          <ChevronDown className={styles.chevron} aria-hidden />
        </>
      )}
    >
      {(close) => (
        <>
          <MenuItem checked={!scope.tax_year} onClick={() => (set(undefined), close())}>
            Any year
          </MenuItem>
          <MenuSeparator />
          {Array.from({ length: 8 }, (_, i) => now - i).map((y) => (
            <MenuItem key={y} checked={scope.tax_year === y} onClick={() => (set(y), close())} className="num">
              {y}
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  );
}
