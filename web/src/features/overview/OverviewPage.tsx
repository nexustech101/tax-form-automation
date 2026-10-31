import { ArrowRight, Upload } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { errorMessage } from "../../api/client";
import { useDocuments, useStats, useTypeName } from "../../api/library";
import type { LibraryStats } from "../../api/types";
import { Alert } from "../../components/Alert";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Skeleton } from "../../components/Skeleton";
import table from "../../components/Table.module.css";
import { formatCount, plural, timeAgo } from "../../lib/format";
import styles from "./OverviewPage.module.css";

export function OverviewPage() {
  const navigate = useNavigate();
  const stats = useStats();
  const upload = (
    <Button variant="primary" icon={<Upload />} onClick={() => navigate("/documents?upload=1")}>
      Upload documents
    </Button>
  );
  return (
    <div>
      <PageHeader title="Overview" description="Documents in the library and where their review stands." actions={upload} />
      {stats.isError ? (
        <Alert title="The overview could not be loaded">{errorMessage(stats.error)}</Alert>
      ) : stats.data?.documents === 0 ? (
        <Card>
          <EmptyState icon={<Upload />} title="No documents yet" action={upload}>
            Upload PDFs or scans. Every tax form found in them is extracted and queued for review.
          </EmptyState>
        </Card>
      ) : (
        <Dashboard stats={stats.data} />
      )}
    </div>
  );
}

function Dashboard({ stats }: { stats?: LibraryStats }) {
  const pending = stats?.forms_by_review_status.pending ?? 0;
  const approved = stats?.forms_by_review_status.approved ?? 0;
  return (
    <div className={styles.grid}>
      <div className={styles.tiles}>
        <Tile label="Documents" value={stats?.documents} caption={stats && plural(stats.clients.length, "client")} />
        <Tile label="Forms extracted" value={stats?.forms} caption={stats && plural(Object.keys(stats.forms_by_type).length, "form type")} />
        <Tile label="Awaiting review" value={stats && pending} caption={stats && `${formatCount(approved)} approved`} />
        <Tile
          label="Open flags"
          value={stats?.open_flags}
          caption="Flagged fields without a decision"
          tone={stats && stats.open_flags > 0 ? "warning" : undefined}
        />
      </div>
      <Card title="Forms by type">{stats ? <TypeBars counts={stats.forms_by_type} /> : <Skeleton height={120} />}</Card>
      <Card title="Review status">
        {stats ? <StatusBar pending={pending} approved={approved} /> : <Skeleton height={120} />}
      </Card>
      <ReviewQueue />
      <Clients stats={stats} />
    </div>
  );
}

function Tile({ label, value, caption, tone }: { label: string; value?: number; caption?: string; tone?: "warning" }) {
  return (
    <div className={styles.tile} role="group" aria-label={label}>
      <span className={styles.tileLabel}>
        {tone && <span className={styles.toneDot} aria-hidden />}
        {label}
      </span>
      <span className={`${styles.tileValue} num`}>{value === undefined ? <Skeleton width={56} height={28} /> : formatCount(value)}</span>
      <span className={styles.tileCaption}>{caption ?? " "}</span>
    </div>
  );
}

function TypeBars({ counts }: { counts: Record<string, number> }) {
  const typeName = useTypeName();
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return <p className="muted">No extracted forms.</p>;
  const max = Math.max(...rows.map(([, n]) => n));
  return (
    <ul className={styles.bars}>
      {rows.map(([type, n]) => (
        <li key={type} className={styles.barRow} title={`${typeName(type)}: ${plural(n, "form")}`}>
          <Link to={`/documents?document_type=${encodeURIComponent(type)}`} className={styles.barLabel}>
            {typeName(type)}
          </Link>
          <span className={styles.barTrack}>
            <span className={styles.bar} style={{ width: `${(n / max) * 100}%` }} />
          </span>
          <span className={`${styles.barValue} num`}>{formatCount(n)}</span>
        </li>
      ))}
    </ul>
  );
}

function StatusBar({ pending, approved }: { pending: number; approved: number }) {
  const total = pending + approved;
  if (!total) return <p className="muted">No extracted forms.</p>;
  const share = Math.round((approved / total) * 100);
  return (
    <div className={styles.status}>
      <div className={styles.statusHead}>
        <span className={`${styles.statusShare} num`}>{share}%</span>
        <span className="muted">of {plural(total, "form")} approved</span>
      </div>
      <div className={styles.stack} role="img" aria-label={`${approved} approved, ${pending} pending`}>
        {approved > 0 && <span className={styles.approved} style={{ flexGrow: approved }} title={`Approved: ${approved}`} />}
        {pending > 0 && <span className={styles.pending} style={{ flexGrow: pending }} title={`Pending: ${pending}`} />}
      </div>
      <dl className={styles.legend}>
        <div>
          <dt>
            <span className={`${styles.swatch} ${styles.approved}`} /> Approved
          </dt>
          <dd className="num">{formatCount(approved)}</dd>
        </div>
        <div>
          <dt>
            <span className={`${styles.swatch} ${styles.pending}`} /> Pending review
          </dt>
          <dd className="num">{formatCount(pending)}</dd>
        </div>
      </dl>
    </div>
  );
}

function ReviewQueue() {
  const navigate = useNavigate();
  const queue = useDocuments({ review_status: "pending", order: "oldest", limit: 6 });
  const docs = queue.data?.documents ?? [];
  return (
    <Card
      title="Review queue"
      flush
      className={styles.wide}
      actions={
        <Link to="/documents?review_status=pending" className={styles.more}>
          All pending <ArrowRight aria-hidden />
        </Link>
      }
    >
      {queue.isPending ? (
        <div className={styles.pad}>
          <Skeleton height={120} />
        </div>
      ) : docs.length === 0 ? (
        <EmptyState compact title="Nothing to review">Every extracted form is approved.</EmptyState>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th>Document</th>
                <th>Client</th>
                <th className={table.num}>Forms pending</th>
                <th>Flags</th>
                <th className={table.num}>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className={table.link} onClick={() => navigate(`/documents/${d.id}`)}>
                  <td>
                    <Link to={`/documents/${d.id}`} className={styles.docLink} onClick={(e) => e.stopPropagation()}>
                      {d.title}
                    </Link>
                  </td>
                  <td className={d.client ? undefined : "muted"}>{d.client ?? "—"}</td>
                  <td className={table.num}>{d.forms_pending}</td>
                  <td>{d.open_flags > 0 ? <Badge tone="warning">{plural(d.open_flags, "open flag")}</Badge> : <Badge>No open flags</Badge>}</td>
                  <td className={`${table.num} muted`}>{timeAgo(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Clients({ stats }: { stats?: LibraryStats }) {
  const navigate = useNavigate();
  return (
    <Card title="Clients" flush className={styles.wide}>
      {!stats ? (
        <div className={styles.pad}>
          <Skeleton height={120} />
        </div>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th>Client</th>
                <th className={table.num}>Documents</th>
                <th className={table.num}>Forms pending</th>
                <th className={table.num}>Open flags</th>
              </tr>
            </thead>
            <tbody>
              {stats.clients.map((c) => (
                <tr
                  key={c.client ?? ""}
                  className={c.client ? table.link : undefined}
                  onClick={() => c.client && navigate(`/documents?client=${encodeURIComponent(c.client)}`)}
                >
                  <td className={c.client ? undefined : "muted"}>{c.client ?? "No client"}</td>
                  <td className={table.num}>{formatCount(c.documents)}</td>
                  <td className={table.num}>{formatCount(c.forms_pending)}</td>
                  <td className={table.num}>
                    {c.open_flags > 0 ? <span className={styles.warnText}>{formatCount(c.open_flags)}</span> : <span className="muted">0</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
