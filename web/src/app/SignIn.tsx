import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { Field, Input } from "../components/Field";
import { useAuth } from "./auth";
import styles from "./SignIn.module.css";

export function SignIn() {
  const { signIn, notice } = useAuth();
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(key.trim(), remember);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "This key is not valid, expired or revoked." : String((err as Error).message));
      setBusy(false);
    }
  };

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={submit}>
        <span className={styles.logo} aria-hidden>
          <KeyRound />
        </span>
        <div className={styles.heading}>
          <h1>Sign in to Tax Documents</h1>
          <p>Use the API key you were given. An administrator creates keys in Settings or with <code>main.py create-key</code>.</p>
        </div>
        {(error || notice) && <Alert tone={error ? "danger" : "info"}>{error ?? notice}</Alert>}
        <Field label="API key">
          <Input
            type="password"
            autoComplete="current-password"
            placeholder="ak_…"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            aria-invalid={error ? true : undefined}
            autoFocus
            required
          />
        </Field>
        <label className={styles.remember}>
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember on this device
        </label>
        <Button type="submit" variant="primary" loading={busy} disabled={!key.trim()} className={styles.submit}>
          Sign in
        </Button>
      </form>
    </div>
  );
}
