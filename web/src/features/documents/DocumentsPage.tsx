import { ChevronLeft, ChevronRight, FileText, Search, Trash2, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import { useDeleteDocument, useDocuments, useDocumentTypes, useStats, useTypeName } from "../../api/library";
import type { DocumentFilters, LibraryDocument, ReviewStatus } from "../../api/types";
import { Alert } from "../../components/Alert";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Dialog } from "../../components/Dialog";
import { EmptyState } from "../../components/EmptyState";
import { Input, Select } from "../../components/Field";
import { PageHeader } from "../../components/PageHeader";
import { Skeleton } from "../../components/Skeleton";
import table from "../../components/Table.module.css";
import { useToast } from "../../components/Toast";
import { formatBytes, formatCount, plural, timeAgo } from "../../lib/format";
import { ReviewState } from "./ReviewState";
import { UploadDialog } from "./UploadDialog";
import styles from "./DocumentsPage.module.css";

const PAGE_SIZE = 25;
const FILTERS = ["q", "client", "tax_year", "document_type", "review_status"] as const;

/** Filters live in the URL, so links from the overview and the search box land on a filtered list. */
function useFilters() {
  const [params, setParams] = useSearchParams();
  const filters: DocumentFilters = {
    q: params.get("q") || undefined,
    client: params.get("client") || undefined,
    tax_year: Number(params.get("tax_year")) || undefined,
    document_type: params.get("document_type") || undefined,
    review_status: (params.get("review_status") as ReviewStatus | null) || undefined,
  };
  const offset = Number(params.get("offset")) || 0;
  const set = (changes: Record<string, string | number | undefined>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(changes)) {
          if (value === undefined || value === "") next.delete(key);
          else next.set(key, String(value));
        }
        if (!("offset" in changes)) next.delete("offset");
        return next;
      },
      { replace: true },
    );
  return { filters, offset, set, params };
}

export function DocumentsPage() {
  const { filters, offset, set, params } = useFilters();
  const list = useDocuments({ ...filters, limit: PAGE_SIZE, offset });
  const [toDelete, setToDelete] = useState<LibraryDocument | null>(null);
  const uploading = params.get("upload") === "1";
  const filtered = FILTERS.some((f) => filters[f] !== undefined);

  return (
    <div>
      <PageHeader
        title="Documents"
        description="Every uploaded file, its forms and their review status."
        actions={
          <Button variant="primary" icon={<Upload />} onClick={() => set({ upload: 1, offset })}>
            Upload
          </Button>
        }
      />
      <Card flush>
        <FilterBar filters={filters} set={set} filtered={filtered} />
        {list.isError ? (
          <div className={styles.pad}>
            <Alert title="Documents could not be loaded">{errorMessage(list.error)}</Alert>
          </div>
        ) : !list.data ? (
          <div className={styles.pad}>
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className={styles.skeletonRow}>
                <Skeleton height={16} />
              </div>
            ))}
          </div>
        ) : list.data.documents.length === 0 ? (
          filtered ? (
            <EmptyState icon={<Search />} title="No matching documents" action={<Button onClick={() => set(Object.fromEntries(FILTERS.map((f) => [f, undefined])))}>Clear filters</Button>}>
              Try a different search or remove a filter.
            </EmptyState>
          ) : (
            <EmptyState
              icon={<FileText />}
              title="No documents yet"
              action={<Button variant="primary" icon={<Upload />} onClick={() => set({ upload: 1 })}>Upload documents</Button>}
            >
              Upload PDFs, scans or text files. Tax forms in them are extracted for review.
            </EmptyState>
          )
        ) : (
          <>
            <DocumentTable documents={list.data.documents} onDelete={setToDelete} />
            <Pager count={list.data.count} offset={offset} shown={list.data.documents.length} onChange={(o) => set({ offset: o || undefined })} />
          </>
        )}
      </Card>
      <UploadDialog open={uploading} onClose={() => set({ upload: undefined, offset })} />
      <DeleteDialog document={toDelete} onClose={() => setToDelete(null)} />
    </div>
  );
}

function FilterBar({ filters, set, filtered }: { filters: DocumentFilters; set: (c: Record<string, string | number | undefined>) => void; filtered: boolean }) {
  const clients = useStats().data?.clients.flatMap((c) => (c.client ? [c.client] : [])) ?? [];
  const types = useDocumentTypes().data ?? [];
  const [q, setQ] = useState(filters.q ?? "");
  const [year, setYear] = useState(filters.tax_year ? String(filters.tax_year) : "");
  useEffect(() => {
    setQ(filters.q ?? "");
  }, [filters.q]);
  useEffect(() => {
    setYear(filters.tax_year ? String(filters.tax_year) : "");
  }, [filters.tax_year]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (q.trim() !== (filters.q ?? "")) set({ q: q.trim() });
      const y = /^\d{4}$/.test(year) ? Number(year) : undefined;
      if (y !== filters.tax_year && (y !== undefined || year === "")) set({ tax_year: y });
    }, 250);
    return () => clearTimeout(timer);
  }, [q, year]); // eslint-disable-line react-hooks/exhaustive-deps -- only typing should debounce
  return (
    <div className={styles.filters}>
      <div className={styles.search}>
        <Search aria-hidden />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title or client" aria-label="Search title or client" />
      </div>
      <Select value={filters.client ?? ""} onChange={(e) => set({ client: e.target.value })} aria-label="Client" className={styles.select}>
        <option value="">All clients</option>
        {filters.client && !clients.includes(filters.client) && <option>{filters.client}</option>}
        {clients.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </Select>
      <Input
        value={year}
        onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
        placeholder="Tax year"
        inputMode="numeric"
        aria-label="Tax year"
        className={styles.year}
      />
      <Select value={filters.document_type ?? ""} onChange={(e) => set({ document_type: e.target.value })} aria-label="Form type" className={styles.select}>
        <option value="">All form types</option>
        {types.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
        <option value="text">Text (no form)</option>
      </Select>
      <Select value={filters.review_status ?? ""} onChange={(e) => set({ review_status: e.target.value })} aria-label="Review status" className={styles.select}>
        <option value="">Any review status</option>
        <option value="pending">Pending review</option>
        <option value="approved">Approved</option>
      </Select>
      {filtered && (
        <Button variant="ghost" size="sm" icon={<X />} onClick={() => set(Object.fromEntries(FILTERS.map((f) => [f, undefined])))}>
          Clear
        </Button>
      )}
    </div>
  );
}

function DocumentTable({ documents, onDelete }: { documents: LibraryDocument[]; onDelete: (d: LibraryDocument) => void }) {
  const navigate = useNavigate();
  const typeName = useTypeName();
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr>
            <th>Document</th>
            <th>Client</th>
            <th className={table.num}>Year</th>
            <th>Forms</th>
            <th className={table.num}>Pages</th>
            <th>Review</th>
            <th className={table.num}>Uploaded</th>
            <th className={table.shrink}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.map((d) => {
            const forms = d.document_types.filter((t) => t !== "text");
            return (
              <tr key={d.id} className={table.link} onClick={() => navigate(`/documents/${d.id}`)}>
                <td>
                  <div className={styles.titleCell}>
                  <span className={styles.fileIcon} aria-hidden>
                    <FileText />
                  </span>
                  <div className={styles.titleText}>
                    <Link to={`/documents/${d.id}`} className={`${styles.title} truncate`} onClick={(e) => e.stopPropagation()}>
                      {d.title}
                    </Link>
                    <span className={`${styles.meta} truncate`}>
                      {d.location} · {formatBytes(d.length_bytes)}
                    </span>
                  </div>
                  </div>
                </td>
                <td className={d.client ? undefined : "muted"}>{d.client ?? "—"}</td>
                <td className={`${table.num} ${d.tax_year ? "" : "muted"}`}>{d.tax_year ?? "—"}</td>
                <td>
                  <div className={styles.types}>
                    {forms.length === 0 ? <span className="muted">Text only</span> : forms.slice(0, 3).map((t) => <Badge key={t}>{typeName(t)}</Badge>)}
                    {forms.length > 3 && <Badge>+{forms.length - 3}</Badge>}
                  </div>
                </td>
                <td className={table.num}>{d.page_count ? formatCount(d.page_count) : <span className="muted">—</span>}</td>
                <td>
                  <ReviewState document={d} />
                </td>
                <td className={`${table.num} muted`} title={new Date(d.created_at).toLocaleString()}>
                  {timeAgo(d.created_at)}
                </td>
                <td className={table.shrink}>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Trash2 />}
                    aria-label={`Delete ${d.title}`}
                    title="Delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(d);
                    }}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Pager({ count, offset, shown, onChange }: { count: number; offset: number; shown: number; onChange: (offset: number) => void }) {
  return (
    <div className={styles.pager}>
      <span className="muted num">
        {formatCount(offset + 1)}–{formatCount(offset + shown)} of {plural(count, "document")}
      </span>
      {count > PAGE_SIZE && (
        <div className={styles.pagerButtons}>
          <Button size="sm" icon={<ChevronLeft />} aria-label="Previous page" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))} />
          <Button size="sm" icon={<ChevronRight />} aria-label="Next page" disabled={offset + shown >= count} onClick={() => onChange(offset + PAGE_SIZE)} />
        </div>
      )}
    </div>
  );
}

function DeleteDialog({ document, onClose }: { document: LibraryDocument | null; onClose: () => void }) {
  const remove = useDeleteDocument();
  const notify = useToast();
  return (
    <Dialog
      open={document !== null}
      onClose={onClose}
      title="Delete document?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() =>
              document &&
              remove.mutate(document.id, {
                onSuccess: () => {
                  notify(`Deleted ${document.title}`);
                  onClose();
                },
                onError: (e) => notify(errorMessage(e), "danger"),
              })
            }
          >
            Delete
          </Button>
        </>
      }
    >
      <p>
        <strong className={styles.strong}>{document?.title}</strong> will be removed with its forms, review decisions, search
        index entries and the original file. This can't be undone.
      </p>
    </Dialog>
  );
}
