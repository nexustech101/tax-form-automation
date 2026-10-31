import { CheckCircle2, CircleAlert, FileUp, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { useStats, useUploadDocument } from "../../api/library";
import type { DocumentDetail } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { Field, Input } from "../../components/Field";
import { Spinner } from "../../components/Spinner";
import { formatBytes, plural } from "../../lib/format";
import styles from "./UploadDialog.module.css";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.txt,.md,.csv,.xlsx";

type Item = { file: File; state: "queued" | "uploading" | "done" | "failed"; result?: DocumentDetail; error?: string };

/** Files are sent one at a time (the API processes uploads one per extraction slot); each shows its own result. */
export function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const upload = useUploadDocument();
  const clients = useStats().data?.clients.flatMap((c) => (c.client ? [c.client] : [])) ?? [];
  const [items, setItems] = useState<Item[]>([]);
  const [client, setClient] = useState("");
  const [year, setYear] = useState("");
  const [running, setRunning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const add = (files: FileList | null) => {
    const added = Array.from(files ?? [], (file): Item => ({ file, state: "queued" })); // copy now: the input is reset next
    setItems((all) => [...all, ...added]);
  };
  const update = (i: number, change: Partial<Item>) => setItems((all) => all.map((item, j) => (j === i ? { ...item, ...change } : item)));

  const start = async () => {
    setRunning(true);
    for (const [i, item] of items.entries()) {
      if (item.state === "done") continue;
      update(i, { state: "uploading", error: undefined });
      try {
        const result = await upload.mutateAsync({
          file: item.file,
          client: client.trim() || undefined,
          tax_year: /^\d{4}$/.test(year) ? Number(year) : undefined,
        });
        update(i, { state: "done", result });
      } catch (error) {
        update(i, { state: "failed", error: errorMessage(error) });
      }
    }
    setRunning(false);
  };

  const close = () => {
    if (running) return;
    setItems([]);
    onClose();
  };

  const drop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  };

  const remaining = items.filter((i) => i.state !== "done").length;
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Upload documents"
      wide
      footer={
        <>
          <Button onClick={close} disabled={running}>
            {items.length > 0 && remaining === 0 ? "Done" : "Cancel"}
          </Button>
          <Button variant="primary" onClick={start} loading={running} disabled={remaining === 0}>
            {remaining > 0 ? `Upload ${plural(remaining, "file")}` : "Upload"}
          </Button>
        </>
      }
    >
      <div className={styles.body}>
        <div className={styles.fields}>
          <Field label="Client" hint="Applies to every file in this upload.">
            <Input value={client} onChange={(e) => setClient(e.target.value)} list="upload-clients" placeholder="e.g. Gonzalez" disabled={running} />
            <datalist id="upload-clients">
              {clients.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </Field>
          <Field label="Tax year" hint="Used when the form has none.">
            <Input value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))} inputMode="numeric" placeholder="e.g. 2025" disabled={running} />
          </Field>
        </div>
        <button
          type="button"
          className={`${styles.drop} ${dragging ? styles.dragging : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
          disabled={running}
        >
          <FileUp aria-hidden />
          <span className={styles.dropTitle}>Drop files here or click to choose</span>
          <span className={styles.dropHint}>PDF, PNG, JPEG, TIFF, WEBP, TXT, MD, CSV or XLSX</span>
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          data-testid="upload-input"
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
        {items.length > 0 && (
          <ul className={styles.list}>
            {items.map((item, i) => (
              <li key={`${item.file.name}-${i}`} className={styles.item} data-state={item.state}>
                <ItemIcon state={item.state} />
                <div className={styles.itemText}>
                  <span className={`${styles.name} truncate`}>{item.file.name}</span>
                  <span className={styles.detail}>
                    <ItemDetail item={item} />
                  </span>
                </div>
                {item.state === "queued" && !running && (
                  <Button variant="ghost" size="sm" icon={<X />} aria-label={`Remove ${item.file.name}`} onClick={() => setItems((all) => all.filter((_, j) => j !== i))} />
                )}
                {item.result && (
                  <Link to={`/documents/${item.result.id}`} onClick={close} className={styles.open}>
                    Review
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}

function ItemIcon({ state }: { state: Item["state"] }) {
  if (state === "uploading") return <Spinner size={16} />;
  if (state === "done") return <CheckCircle2 className={styles.ok} aria-label="Done" />;
  if (state === "failed") return <CircleAlert className={styles.failed} aria-label="Failed" />;
  return <FileUp className={styles.queued} aria-hidden />;
}

function ItemDetail({ item }: { item: Item }) {
  if (item.state === "uploading") return <>Extracting forms…</>;
  if (item.state === "failed") return <span className={styles.failed}>{item.error}</span>;
  if (item.result) {
    const extracted = item.result.forms.filter((f) => f.status === "ok").length;
    return (
      <>
        {item.result.forms.length ? plural(extracted, "form") + " extracted" : "Stored as text"}
        {item.result.open_flags > 0 && ` · ${plural(item.result.open_flags, "flagged field")}`}
      </>
    );
  }
  return <>{formatBytes(item.file.size)} · waiting</>;
}
