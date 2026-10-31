import { Check as CheckIcon, CircleDashed, Pencil, RotateCcw, TriangleAlert, X } from "lucide-react";
import { forwardRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { usePageImage } from "../../api/library";
import type { Check, DocumentDetail, ReviewRequest } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Input, Select } from "../../components/Field";
import { Kbd } from "../../components/Kbd";
import { DECISIONS, FLAGS } from "../../lib/flags";
import { formatDateTime, formatValue } from "../../lib/format";
import { cropStyle, pageUnits, type Box } from "./geometry";
import { MONTHS, parseInput, toInput } from "./values";
import styles from "./CheckRow.module.css";

type Props = {
  check: Check;
  document: DocumentDetail;
  selected: boolean;
  editing: boolean;
  busy: boolean;
  error: string | null;
  onSelect: () => void;
  onDecide: (review: ReviewRequest | null) => void;
  onEdit: (editing: boolean) => void;
};

/** One field: label and flags, then the crop of the page where it was read beside the extracted value. */
export const CheckRow = forwardRef<HTMLLIElement, Props>(function CheckRow(
  { check, document, selected, editing, busy, error, onSelect, onDecide, onEdit },
  ref,
) {
  const review = check.review;
  return (
    <li
      ref={ref}
      className={`${styles.row} ${selected ? styles.selected : ""}`}
      data-field={check.field}
      data-decision={review?.decision ?? "none"}
      aria-current={selected || undefined}
      onClick={onSelect}
    >
      <StatusIcon check={check} />
      <div className={styles.main}>
        <div className={styles.label} title={check.field}>
          {check.label}
        </div>
        <div className={styles.pair}>
          <Crop check={check} document={document} />
          <Value check={check} />
        </div>
        {check.flags.map((flag, i) => (
          <div key={i} className={styles.flag}>
            <Badge tone={FLAGS[flag.code].tone}>{FLAGS[flag.code].label}</Badge>
            <span className={styles.flagText}>{flag.message}</span>
          </div>
        ))}
        {review && (
          <div className={styles.reviewMeta}>
            <Badge tone={DECISIONS[review.decision].tone}>{DECISIONS[review.decision].label}</Badge>
            <span>
              {review.reviewer} · {formatDateTime(review.updated_at)}
              {review.note && <> · “{review.note}”</>}
            </span>
          </div>
        )}
        {editing ? (
          <Editor check={check} busy={busy} serverError={error} onSave={(review) => onDecide(review)} onCancel={() => onEdit(false)} />
        ) : (
          selected && (
            <div className={styles.actions} onClick={(e) => e.stopPropagation()}>
              <Button size="sm" icon={<CheckIcon />} onClick={() => onDecide({ decision: "confirmed" })} disabled={busy || review?.decision === "confirmed"}>
                Confirm <Kbd>c</Kbd>
              </Button>
              <Button size="sm" icon={<Pencil />} onClick={() => onEdit(true)} disabled={busy}>
                Correct <Kbd>e</Kbd>
              </Button>
              <Button size="sm" icon={<X />} onClick={() => onDecide({ decision: "rejected" })} disabled={busy || review?.decision === "rejected"}>
                Reject <Kbd>r</Kbd>
              </Button>
              {review && (
                <Button size="sm" variant="ghost" icon={<RotateCcw />} onClick={() => onDecide(null)} disabled={busy}>
                  Undo <Kbd>u</Kbd>
                </Button>
              )}
              {error && <span className={styles.error}>{error}</span>}
            </div>
          )
        )}
      </div>
    </li>
  );
});

function StatusIcon({ check }: { check: Check }) {
  const decision = check.review?.decision;
  if (decision === "confirmed") return <span className={`${styles.status} ${styles.confirmed}`} title="Confirmed"><CheckIcon /></span>;
  if (decision === "corrected") return <span className={`${styles.status} ${styles.corrected}`} title="Corrected"><Pencil /></span>;
  if (decision === "rejected") return <span className={`${styles.status} ${styles.rejected}`} title="Rejected"><X /></span>;
  if (check.flags.length) return <span className={`${styles.status} ${styles.flagged}`} title="Flagged: needs a decision"><TriangleAlert /></span>;
  return <span className={`${styles.status} ${styles.open}`} title="Not checked"><CircleDashed /></span>;
}

function Value({ check }: { check: Check }) {
  const review = check.review;
  const extracted = formatValue(check.kind, check.value);
  if (review?.decision === "corrected") {
    return (
      <div className={styles.value}>
        <span className={`${styles.amount} num`}>{formatValue(check.kind, review.value)}</span>
        <span className={`${styles.was} num`}>{extracted ? <s>{extracted}</s> : "was empty"}</span>
      </div>
    );
  }
  if (review?.decision === "rejected") {
    return (
      <div className={styles.value}>
        <span className={`${styles.amount} ${styles.empty}`}>No value</span>
        {extracted && <span className={`${styles.was} num`}><s>{extracted}</s></span>}
      </div>
    );
  }
  return (
    <div className={styles.value}>
      {extracted ? <span className={`${styles.amount} num`}>{extracted}</span> : <span className={`${styles.amount} ${styles.empty}`}>Empty</span>}
    </div>
  );
}

const CROP_W = 200;
const CROP_H = 36;

function Crop({ check, document }: { check: Check; document: DocumentDetail }) {
  const source = check.source;
  const info = source ? document.pages.find((p) => p.n === source.page) : undefined;
  const { url } = usePageImage(document.id, info ? info.n : null);
  if (!source || !info) {
    return (
      <div className={styles.noCrop} title="Not found in the page text; read this one in the viewer">
        Not located
      </div>
    );
  }
  const style = cropStyle(source.box as Box, pageUnits(info.width, info.height), CROP_W, CROP_H);
  return (
    <div
      className={styles.crop}
      style={{ ...style, backgroundImage: url ? `url(${url})` : undefined }}
      role="img"
      aria-label={`Page ${source.page}${source.label ? `, under “${source.label}”` : ""}`}
      title={`Read on page ${source.page}${source.label ? ` under “${source.label}”` : ""}`}
    />
  );
}

function Editor({ check, busy, serverError, onSave, onCancel }: {
  check: Check;
  busy: boolean;
  serverError: string | null;
  onSave: (review: ReviewRequest) => void;
  onCancel: () => void;
}) {
  const start = check.review?.decision === "corrected" ? check.review.value : check.value;
  const [raw, setRaw] = useState(() => toInput(check.kind, start));
  const [note, setNote] = useState(check.review?.note ?? "");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseInput(check.kind, raw, check.options);
    if (!parsed.ok) return setError(parsed.error);
    setError(null);
    onSave({ decision: "corrected", value: parsed.value, note: note.trim() || null });
  };

  const common = {
    autoFocus: true,
    "aria-label": `Corrected value for ${check.label}`,
    "aria-invalid": error || serverError ? true : undefined,
    onKeyDown: (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), onCancel()),
  };
  const options = check.kind === "check" ? [["true", "Checked"], ["false", "Unchecked"]] : check.kind === "month" ? MONTHS.map((m) => [m, m]) : check.kind === "choice" ? (check.options ?? []).map((o) => [o, o]) : null;

  return (
    <form className={styles.editor} onSubmit={submit} onClick={(e) => e.stopPropagation()}>
      <div className={styles.editorFields}>
        {options ? (
          <Select {...common} value={raw} onChange={(e) => setRaw(e.target.value)}>
            <option value="" disabled>
              Choose…
            </option>
            {options.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        ) : (
          <Input
            {...common}
            type={check.kind === "date" ? "date" : "text"}
            inputMode={check.kind === "money" || check.kind === "percent" || check.kind === "year" ? "decimal" : undefined}
            placeholder={PLACEHOLDERS[check.kind]}
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            className={check.kind === "money" ? "num" : undefined}
          />
        )}
        <Input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" onKeyDown={common.onKeyDown} />
      </div>
      {(error || serverError) && <p className={styles.error}>{error ?? serverError}</p>}
      <div className={styles.editorActions}>
        <Button type="submit" size="sm" variant="primary" loading={busy}>
          Save correction
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <span className={styles.hint}>
          <Kbd>Enter</Kbd> save · <Kbd>Esc</Kbd> cancel
        </span>
      </div>
    </form>
  );
}

const PLACEHOLDERS: Partial<Record<Check["kind"], string>> = {
  money: "1,234.56",
  percent: "25.5",
  tin: "123-45-6789",
  state: "TX",
  year: "2025",
  acquired: "MM/DD/YYYY or VARIOUS",
};
