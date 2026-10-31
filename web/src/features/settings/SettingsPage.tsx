import { Copy, KeyRound, LogOut, Monitor, Moon, Plus, Sun } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { errorMessage } from "../../api/client";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "../../api/keys";
import type { ApiKeyCreated, ApiKeyInfo } from "../../api/types";
import { useAuth } from "../../app/auth";
import { useTheme } from "../../app/theme";
import { Alert } from "../../components/Alert";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Dialog } from "../../components/Dialog";
import { Field, Input } from "../../components/Field";
import { PageHeader } from "../../components/PageHeader";
import { Segmented } from "../../components/Segmented";
import { Skeleton } from "../../components/Skeleton";
import table from "../../components/Table.module.css";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import styles from "./SettingsPage.module.css";

export function SettingsPage() {
  const { me, signOut } = useAuth();
  const { choice, setChoice } = useTheme();
  return (
    <div className={styles.page}>
      <PageHeader title="Settings" description="Appearance, your API key and, for administrators, key management." />
      <Card title="Appearance">
        <div className={styles.row}>
          <div>
            <p className={styles.rowTitle}>Theme</p>
            <p className={styles.rowText}>Stored on this device.</p>
          </div>
          <Segmented
            label="Theme"
            value={choice}
            onChange={setChoice}
            options={[
              { value: "light", label: <><Sun /> Light</> },
              { value: "dark", label: <><Moon /> Dark</> },
              { value: "system", label: <><Monitor /> System</> },
            ]}
          />
        </div>
      </Card>
      <Card title="Your API key" actions={<Button icon={<LogOut />} onClick={() => signOut()}>Sign out</Button>}>
        {me && (
          <dl className={styles.facts}>
            <Fact label="Name">{me.name}</Fact>
            <Fact label="Prefix"><code>{me.prefix}…</code></Fact>
            <Fact label="Role">{me.is_admin ? <Badge tone="accent">Admin</Badge> : <Badge>Client</Badge>}</Fact>
            <Fact label="Created">{formatDate(me.created_at)}</Fact>
            <Fact label="Expires">{me.expires_at ? formatDate(me.expires_at) : "Never"}</Fact>
          </dl>
        )}
      </Card>
      {me?.is_admin && <ApiKeys me={me} />}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function ApiKeys({ me }: { me: ApiKeyInfo }) {
  const keys = useApiKeys(true);
  const revoke = useRevokeApiKey();
  const notify = useToast();
  const [creating, setCreating] = useState(false);
  const [toRevoke, setToRevoke] = useState<ApiKeyInfo | null>(null);
  return (
    <Card title="API keys" flush actions={<Button icon={<Plus />} onClick={() => setCreating(true)}>New key</Button>}>
      {keys.isError ? (
        <div className={styles.pad}>
          <Alert>{errorMessage(keys.error)}</Alert>
        </div>
      ) : !keys.data ? (
        <div className={styles.pad}>
          <Skeleton height={80} />
        </div>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Role</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Status</th>
                <th className={table.shrink}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {keys.data.keys.map((k) => (
                <tr key={k.id}>
                  <td>
                    {k.name} {k.id === me.id && <span className="muted">(you)</span>}
                  </td>
                  <td><code>{k.prefix}…</code></td>
                  <td>{k.is_admin ? <Badge tone="accent">Admin</Badge> : <Badge>Client</Badge>}</td>
                  <td className="num">{formatDate(k.created_at)}</td>
                  <td className="num">{k.expires_at ? formatDate(k.expires_at) : <span className="muted">Never</span>}</td>
                  <td>{k.revoked_at ? <Badge tone="danger">Revoked</Badge> : <Badge tone="success">Active</Badge>}</td>
                  <td className={table.shrink}>
                    {!k.revoked_at && k.id !== me.id && (
                      <Button size="sm" variant="ghost" onClick={() => setToRevoke(k)}>
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <CreateKeyDialog open={creating} onClose={() => setCreating(false)} />
      <Dialog
        open={toRevoke !== null}
        onClose={() => setToRevoke(null)}
        title="Revoke key?"
        footer={
          <>
            <Button onClick={() => setToRevoke(null)}>Cancel</Button>
            <Button
              variant="danger"
              loading={revoke.isPending}
              onClick={() =>
                toRevoke &&
                revoke.mutate(toRevoke.id, {
                  onSuccess: () => {
                    notify(`Revoked ${toRevoke.name}`);
                    setToRevoke(null);
                  },
                  onError: (e) => notify(errorMessage(e), "danger"),
                })
              }
            >
              Revoke
            </Button>
          </>
        }
      >
        <p>
          Requests with <strong>{toRevoke?.name}</strong> stop working at once. Its documents stay in the library (visible to admin keys).
        </p>
      </Dialog>
    </Card>
  );
}

function CreateKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateApiKey();
  const notify = useToast();
  const [name, setName] = useState("");
  const [admin, setAdmin] = useState(false);
  const [days, setDays] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const close = () => {
    setName("");
    setAdmin(false);
    setDays("");
    setCreated(null);
    create.reset();
    onClose();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ name: name.trim(), is_admin: admin, expires_in_days: Number(days) || null }, { onSuccess: setCreated });
  };

  if (created) {
    return (
      <Dialog open={open} onClose={close} title="Key created" footer={<Button variant="primary" onClick={close}>Done</Button>}>
        <div className={styles.created}>
          <p>Copy the key now: it is shown only once.</p>
          <div className={styles.secret}>
            <KeyRound aria-hidden />
            <code className="truncate">{created.key}</code>
            <Button
              size="sm"
              icon={<Copy />}
              onClick={() => navigator.clipboard.writeText(created.key).then(() => notify("Key copied"), () => notify("Copy failed: select the key and copy it", "danger"))}
            >
              Copy
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog
      open={open}
      onClose={close}
      title="New API key"
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button variant="primary" type="submit" form="create-key" loading={create.isPending} disabled={!name.trim()}>
            Create key
          </Button>
        </>
      }
    >
      <form id="create-key" className={styles.form} onSubmit={submit}>
        {create.isError && <Alert>{errorMessage(create.error)}</Alert>}
        <Field label="Name" hint="Who or what uses it, e.g. a reviewer or the n8n workflow.">
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} autoFocus required />
        </Field>
        <Field label="Expires after (days)" hint="Empty: never expires.">
          <Input value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, "").slice(0, 4))} inputMode="numeric" placeholder="e.g. 365" />
        </Field>
        <label className={styles.check}>
          <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
          Admin key: sees every document and manages keys
        </label>
      </form>
    </Dialog>
  );
}
