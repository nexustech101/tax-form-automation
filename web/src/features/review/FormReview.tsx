import { CheckCircle2, RotateCcw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, errorMessage } from "../../api/client";
import { useFormStatus, useReviewField } from "../../api/review";
import type { Check, DocumentDetail, FormDetail, ReviewRequest } from "../../api/types";
import { Alert } from "../../components/Alert";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { EmptyState } from "../../components/EmptyState";
import { Kbd } from "../../components/Kbd";
import { Progress } from "../../components/Progress";
import { Segmented } from "../../components/Segmented";
import { useToast } from "../../components/Toast";
import { FLAGS } from "../../lib/flags";
import { formatDateTime, plural } from "../../lib/format";
import { CheckRow } from "./CheckRow";
import styles from "./FormReview.module.css";

export type Filter = "flagged" | "unchecked" | "all";

const FILTERS: Record<Filter, (c: Check) => boolean> = {
  flagged: (c) => c.flags.length > 0,
  unchecked: (c) => c.review === null,
  all: () => true,
};

type Props = {
  form: FormDetail;
  document: DocumentDetail;
  selected: string | null;
  onSelect: (field: string | null) => void;
};

/** The check list of one extracted form: progress, filter, decisions, approval and keyboard shortcuts. */
export function FormReview({ form, document, selected, onSelect }: Props) {
  const notify = useToast();
  const review = useReviewField(form);
  const status = useFormStatus(form);
  const [filter, setFilter] = useState<Filter>(() => (form.open_flags > 0 ? "flagged" : "all"));
  const [editing, setEditing] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ field: string; message: string } | null>(null);
  const rows = useRef(new Map<string, HTMLLIElement>());

  const visible = useMemo(() => form.checks.filter(FILTERS[filter]), [form.checks, filter]);
  const undecided = form.checks.filter((c) => c.flags.length > 0 && c.review === null);
  const counts = { flagged: form.checks.filter(FILTERS.flagged).length, unchecked: form.checks.filter(FILTERS.unchecked).length, all: form.checks.length };

  // Keep a visible row selected; scroll the selected row into view.
  useEffect(() => {
    if (!visible.length) return;
    if (!selected || !visible.some((c) => c.field === selected)) onSelect(visible[0]!.field);
  }, [visible, selected, onSelect]);
  useEffect(() => {
    if (selected) rows.current.get(selected)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const move = (step: number) => {
    const at = visible.findIndex((c) => c.field === selected);
    const next = visible[Math.min(visible.length - 1, Math.max(0, at + step))];
    if (next) onSelect(next.field);
  };

  const decide = (check: Check, body: ReviewRequest | null) => {
    setRowError(null);
    const at = visible.findIndex((c) => c.field === check.field);
    review.mutate(
      { field: check.field, review: body },
      {
        onSuccess: () => {
          setEditing(null);
          // Advance after a decision, so a reviewer can work down the list with the keyboard.
          const next = body && visible[at + 1];
          if (next) onSelect(next.field);
        },
        onError: (e) => setRowError({ field: check.field, message: errorMessage(e) }),
      },
    );
  };

  const approve = () =>
    status.mutate("approve", {
      onSuccess: () => notify(`${form.form_name ?? "Form"} approved`),
      onError: (e) => {
        const undecidedFields = e instanceof ApiError ? (e.details?.undecided as string[] | undefined) : undefined;
        notify(errorMessage(e), "danger");
        if (undecidedFields?.[0]) {
          setFilter("flagged");
          onSelect(undecidedFields[0]);
        }
      },
    });

  // Shortcuts, ignored while typing in a field.
  const current = form.checks.find((c) => c.field === selected) ?? null;
  const handler = useRef<(e: KeyboardEvent) => void>(() => {});
  handler.current = (e) => {
    const typing = e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable], dialog");
    if (e.metaKey || e.ctrlKey || e.altKey || typing) return;
    const actions: Record<string, () => void> = {
      j: () => move(1),
      ArrowDown: () => move(1),
      k: () => move(-1),
      ArrowUp: () => move(-1),
      c: () => current && decide(current, { decision: "confirmed" }),
      e: () => current && setEditing(current.field),
      r: () => current && decide(current, { decision: "rejected" }),
      u: () => current?.review && decide(current, null),
      Escape: () => setEditing(null),
    };
    const action = actions[e.key];
    if (action && !review.isPending) {
      e.preventDefault();
      action();
    }
  };
  useEffect(() => {
    const listener = (e: KeyboardEvent) => handler.current(e);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  const approved = form.review_status === "approved";
  let group: string | null | undefined;

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <div className={styles.titleRow}>
          <div className={styles.titleText}>
            <h2 className="truncate">{form.form_name ?? form.document_type}</h2>
            <span className={styles.sub}>
              {form.pages.length > 1 ? `Pages ${form.pages[0]}–${form.pages.at(-1)}` : `Page ${form.pages[0]}`} · {form.extraction_model ?? "no model"} ·{" "}
              classified by {form.classification.model}
            </span>
          </div>
          {approved ? (
            <Button size="sm" icon={<RotateCcw />} onClick={() => status.mutate("reopen")} loading={status.isPending}>
              Reopen
            </Button>
          ) : (
            <Button
              variant="primary"
              icon={<ShieldCheck />}
              onClick={approve}
              loading={status.isPending}
              disabled={undecided.length > 0}
              title={undecided.length ? `Decide ${plural(undecided.length, "flagged field")} first` : "Mark this form reviewed"}
            >
              Approve
            </Button>
          )}
        </div>
        <div className={styles.progressRow}>
          <Progress value={form.checked} max={form.total} label="Fields checked" />
          <span className={`${styles.progressText} num`}>
            {form.checked} of {form.total} checked
          </span>
        </div>
        {approved ? (
          <p className={styles.approved}>
            <CheckCircle2 aria-hidden /> Approved by {form.approved_by}
            {form.approved_at && ` · ${formatDateTime(form.approved_at)}`}. A changed decision reopens it.
          </p>
        ) : undecided.length > 0 ? (
          <p className={styles.blocked} data-testid="approve-reason">
            Decide {plural(undecided.length, "flagged field")} to approve: confirm, correct or reject {undecided.length === 1 ? "it" : "each"}.
          </p>
        ) : (
          <p className={styles.ready}>Every flagged field has a decision. Approve when the rest looks right.</p>
        )}
        {form.flags.length > 0 && (
          <div className={styles.formFlags}>
            {form.flags.map((f, i) => (
              <Alert key={i} tone={FLAGS[f.code].tone === "danger" ? "danger" : "warning"} title={FLAGS[f.code].label}>
                {f.message}
              </Alert>
            ))}
          </div>
        )}
        <Segmented<Filter>
          label="Show fields"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "flagged", label: <>Flagged <Count n={counts.flagged} /></> },
            { value: "unchecked", label: <>Unchecked <Count n={counts.unchecked} /></> },
            { value: "all", label: <>All <Count n={counts.all} /></> },
          ]}
        />
      </div>
      {visible.length === 0 ? (
        <EmptyState compact icon={<CheckCircle2 />} title={filter === "flagged" ? "No flagged fields" : "Every field is checked"}>
          {filter === "flagged" ? "The extraction raised no flags on this form." : "Switch to All to see the decisions."}
        </EmptyState>
      ) : (
        <ul className={styles.list} aria-label="Fields">
          {visible.map((check) => {
            const header = check.group !== group ? check.group : undefined;
            group = check.group;
            return (
              <FragmentRow key={check.field} header={header === undefined ? undefined : header ?? "Form fields"}>
                <CheckRow
                  ref={(el) => {
                    if (el) rows.current.set(check.field, el);
                    else rows.current.delete(check.field);
                  }}
                  check={check}
                  document={document}
                  selected={check.field === selected}
                  editing={check.field === editing}
                  busy={review.isPending && review.variables?.field === check.field}
                  error={rowError?.field === check.field ? rowError.message : null}
                  onSelect={() => onSelect(check.field)}
                  onDecide={(body) => decide(check, body)}
                  onEdit={(on) => setEditing(on ? check.field : null)}
                />
              </FragmentRow>
            );
          })}
        </ul>
      )}
      <div className={styles.legend} aria-label="Keyboard shortcuts">
        <span><Kbd>j</Kbd> <Kbd>k</Kbd> move</span>
        <span><Kbd>c</Kbd> confirm</span>
        <span><Kbd>e</Kbd> correct</span>
        <span><Kbd>r</Kbd> reject</span>
        <span><Kbd>u</Kbd> undo</span>
      </div>
    </div>
  );
}

function Count({ n }: { n: number }) {
  return <Badge>{n}</Badge>;
}

function FragmentRow({ header, children }: { header?: string; children: ReactNode }) {
  return (
    <>
      {header !== undefined && <li className={styles.group} aria-hidden>{header}</li>}
      {children}
    </>
  );
}
