import { useState, type FormEvent, type ReactNode } from "react";
import { api, type Me } from "../lib/api";

function AuthCard({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            V2
          </span>
          <span className="brand-name">MPOS Collection</span>
        </div>
        <h1>{title}</h1>
        <p className="hint">{sub}</p>
        {children}
      </div>
    </div>
  );
}

export function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(username, password));
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Sign in" sub="Use the username and password your admin gave you.">
      <form className="auth-form" onSubmit={submit}>
        <label>
          Username
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <div className="err" role="alert">
            {error}
          </div>
        )}
        <button type="submit" className="primary" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </AuthCard>
  );
}

interface ChangeProps {
  me: Me;
  forced: boolean;
  onDone: (me: Me) => void;
  onCancel?: () => void; // voluntary change: back to the app
  onSignOut?: () => void; // forced change: back to the sign-in screen (e.g. wrong user)
}

export function ChangePassword({ me, forced, onDone, onCancel, onSignOut }: ChangeProps) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = confirm.length > 0 && next !== confirm;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.changePassword(current, next));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title={forced ? "Set a new password" : "Change password"}
      sub={
        forced
          ? `Welcome, ${me.full_name}. Your admin set a temporary password - choose your own to continue.`
          : `Signed in as ${me.username}.`
      }
    >
      <form className="auth-form" onSubmit={submit}>
        <label>
          Current password
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          New password
          <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          <span className="field-hint">At least 8 characters, mixing letters with numbers or symbols.</span>
        </label>
        <label>
          Confirm new password
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={mismatch}
          />
          {mismatch && <span className="field-error">Passwords don't match</span>}
        </label>
        {error && (
          <div className="err" role="alert">
            {error}
          </div>
        )}
        <div className="auth-actions">
          {onSignOut && (
            <button type="button" className="ghost back-btn" onClick={onSignOut}>
              ‹ Back to sign in
            </button>
          )}
          {onCancel && (
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button type="submit" className="primary" disabled={busy || !current || !next || next !== confirm}>
            {busy ? "Saving…" : "Save password"}
          </button>
        </div>
      </form>
    </AuthCard>
  );
}
