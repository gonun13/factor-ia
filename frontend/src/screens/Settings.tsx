/**
 * Settings — agent accounts (Claude + Cursor) and git access.
 *
 * Deliberately a small, plain screen and not a second canvas. `DESIGN.md`'s
 * defining constraint is that *building and operating* are one surface; this is
 * neither. It is the once-per-machine setup the happy path opens with, so it
 * reads as a list of facts with a button beside each, in the same visual
 * language as the project list.
 *
 * The sign-in flow is the only interesting part, and its shape comes from the
 * container being headless: the CLI cannot open a browser, so the backend
 * spawns its login and hands the URL it prints out to *this page*, which does
 * have a browser — the operator clicks it, approves, and the value the provider
 * shows them comes back through the paste box. That value is `<code>#<state>`
 * and is sent whole; the `#` is not a fragment, and stripping it fails every
 * login.
 *
 * Claude and Cursor are separate credentials on the same agent-home volume —
 * signing into one does not cover the other.
 *
 * The URL arrives from a poll rather than from a click, so it is rendered as a
 * link and never opened programmatically — a `window.open()` outside a user
 * gesture is what popup blockers exist for, and the failure would be silent.
 *
 * Polled, not streamed: SSE arrives in stage 5, and a login has four state
 * changes over a minute of human time.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";
import { api } from "../api";
import type { AccountStatus, AgentStatus, LoginState, SshKeyStatus } from "../types";

const POLL_MS = 1000;

function ProviderAccount({
  title,
  cliLabel,
  account,
  defaultModel,
  defaultModelDetail,
  login,
  code,
  setCode,
  urlCopied,
  setUrlCopied,
  busy,
  onBegin,
  onSubmit,
  onCancel,
}: {
  title: string;
  cliLabel: string;
  account: AccountStatus | undefined;
  defaultModel: string | null | undefined;
  defaultModelDetail: string | null | undefined;
  login: LoginState | null;
  code: string;
  setCode: (value: string) => void;
  urlCopied: boolean;
  setUrlCopied: (value: boolean) => void;
  busy: boolean;
  onBegin: () => Promise<void>;
  onSubmit: (code: string) => Promise<void>;
  onCancel: () => Promise<void>;
}): ReactElement {
  const signedIn = account?.authenticated === true;
  return (
    <section className="setting">
      <div className="setting-head">
        <span className="eyebrow">{title}</span>
        <span className={signedIn ? "ok" : "bad"}>
          {account === undefined
            ? "checking…"
            : !account.reachable
              ? "CLI unavailable"
              : signedIn
                ? "signed in"
                : "not signed in"}
        </span>
      </div>
      <dl className="facts">
        <dt>{cliLabel}</dt>
        <dd className="mono">{account?.version ?? "—"}</dd>
        <dt>account</dt>
        <dd>{account?.detail || "—"}</dd>
        <dt>default model</dt>
        <dd className="mono">{defaultModel ?? defaultModelDetail ?? "—"}</dd>
      </dl>

      {!signedIn && !login?.active && (
        <div className="setting-actions">
          <button className="primary" disabled={busy} onClick={() => void onBegin()}>
            Sign in
          </button>
          {login?.phase === "failed" && login.detail && (
            <span className="bad">{login.detail}</span>
          )}
        </div>
      )}

      {login?.active && (
        <div className="login-flow">
          {login.verification_url ? (
            <>
              <p className="lede">
                Approve the sign-in in your browser, then paste the code it gives
                you back here.
              </p>
              <a
                className="open-signin"
                href={login.verification_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open sign-in page ↗
              </a>
              <div className="url-row">
                <input
                  className="verification-url mono"
                  readOnly
                  value={login.verification_url}
                  aria-label="Verification URL"
                  onFocus={(event) => event.currentTarget.select()}
                />
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard?.writeText(login.verification_url ?? "");
                    setUrlCopied(true);
                    window.setTimeout(() => setUrlCopied(false), 1500);
                  }}
                >
                  {urlCopied ? "Copied" : "Copy link"}
                </button>
              </div>
            </>
          ) : (
            <p className="lede">Starting sign-in…</p>
          )}

          <form
            className="create-row"
            onSubmit={(event) => {
              event.preventDefault();
              const value = code.trim();
              if (!value) return;
              void onSubmit(value);
            }}
          >
            <input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="Paste the code, including everything after the #"
              aria-label="Verification code"
              spellCheck={false}
              autoComplete="off"
              disabled={login.phase === "verifying"}
            />
            <button className="primary" type="submit" disabled={!code.trim() || busy}>
              {login.phase === "verifying" ? "Verifying…" : "Submit"}
            </button>
            <button type="button" disabled={busy} onClick={() => void onCancel()}>
              Cancel
            </button>
          </form>
          {login.detail && (
            <p className={login.retryable ? "bad" : "lede"}>
              {login.detail}
              {login.retryable && " — the link above is still good, try again."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export function Settings({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [login, setLogin] = useState<LoginState | null>(null);
  const [cursorLogin, setCursorLogin] = useState<LoginState | null>(null);
  const [key, setKey] = useState<SshKeyStatus | null>(null);
  const [code, setCode] = useState("");
  const [cursorCode, setCursorCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [urlCopied, setUrlCopied] = useState(false);
  const [cursorUrlCopied, setCursorUrlCopied] = useState(false);
  const live = useRef(true);

  const refreshStatus = useCallback(async () => {
    const next = await api.agentStatus();
    if (live.current) setStatus(next);
  }, []);

  useEffect(() => {
    live.current = true;
    Promise.all([
      api.agentStatus(),
      api.loginState(),
      api.cursorLoginState(),
      api.sshKey(),
    ])
      .then(([account, flow, cursorFlow, sshKey]) => {
        if (!live.current) return;
        setStatus(account);
        setLogin(flow);
        setCursorLogin(cursorFlow);
        setKey(sshKey);
      })
      .catch((cause: unknown) => live.current && setError(String(cause)));
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    if (!login?.active) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.loginState();
        if (!live.current) return;
        setLogin(next);
        if (!next.active) void refreshStatus();
      } catch {
        /* retried next tick */
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [login?.active, refreshStatus]);

  useEffect(() => {
    if (!cursorLogin?.active) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.cursorLoginState();
        if (!live.current) return;
        setCursorLogin(next);
        if (!next.active) void refreshStatus();
      } catch {
        /* retried next tick */
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [cursorLogin?.active, refreshStatus]);

  async function guard(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (live.current) setBusy(false);
    }
  }

  const account = status?.account;
  const handshake = status?.handshake;
  const cursorAccount = status?.cursor_account;
  const cursorOffer = status?.providers?.cursor;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <span className="dot" />
          Factor-ia
          <small>· settings</small>
        </span>
        <span className="spacer" />
        <button className="link-button" onClick={onBack}>
          ← Projects
        </button>
      </header>

      <div className="screen">
        <div className="screen-inner">
          <h1>Settings</h1>
          <p className="lede">
            Once per machine: sign the container in to each agent provider you
            use, and give it a key so a run can push.
          </p>

          {error && <div className="error">{error}</div>}

          <ProviderAccount
            title="Claude account"
            cliLabel="claude CLI"
            account={account}
            defaultModel={handshake?.default_model}
            defaultModelDetail={handshake?.detail}
            login={login}
            code={code}
            setCode={setCode}
            urlCopied={urlCopied}
            setUrlCopied={setUrlCopied}
            busy={busy}
            onBegin={async () => {
              await guard(async () => {
                setCode("");
                setLogin(await api.beginLogin());
              });
            }}
            onSubmit={async (value) => {
              await guard(async () => {
                setLogin(await api.submitLoginCode(value));
                setCode("");
              });
            }}
            onCancel={async () => {
              await guard(async () => {
                setLogin(await api.cancelLogin());
                await refreshStatus();
              });
            }}
          />

          <ProviderAccount
            title="Cursor account"
            cliLabel="cursor agent CLI"
            account={cursorAccount}
            defaultModel={cursorOffer?.default_model}
            defaultModelDetail={
              cursorAccount && !cursorAccount.reachable
                ? cursorAccount.detail
                : cursorOffer?.default_model_label
            }
            login={cursorLogin}
            code={cursorCode}
            setCode={setCursorCode}
            urlCopied={cursorUrlCopied}
            setUrlCopied={setCursorUrlCopied}
            busy={busy}
            onBegin={async () => {
              await guard(async () => {
                setCursorCode("");
                setCursorLogin(await api.beginCursorLogin());
              });
            }}
            onSubmit={async (value) => {
              await guard(async () => {
                setCursorLogin(await api.submitCursorLoginCode(value));
                setCursorCode("");
              });
            }}
            onCancel={async () => {
              await guard(async () => {
                setCursorLogin(await api.cancelCursorLogin());
                await refreshStatus();
              });
            }}
          />

          <section className="setting">
            <div className="setting-head">
              <span className="eyebrow">Git access</span>
              <span className={key?.exists ? "ok" : "bad"}>
                {key === null ? "checking…" : key.exists ? "key present" : "no key"}
              </span>
            </div>
            <p className="lede">
              One ed25519 keypair, generated inside the container. Add the
              public half to your forge to let a run push.
            </p>

            {key?.exists ? (
              <>
                <textarea
                  className="pubkey mono"
                  readOnly
                  rows={3}
                  value={key.public_key ?? ""}
                  onFocus={(event) => event.currentTarget.select()}
                />
                <div className="setting-actions">
                  <button
                    onClick={() => {
                      void navigator.clipboard?.writeText(key.public_key ?? "");
                      setCopied(true);
                      window.setTimeout(() => setCopied(false), 1500);
                    }}
                  >
                    {copied ? "Copied" : "Copy public key"}
                  </button>
                  <span className="mono muted">{key.fingerprint}</span>
                </div>
              </>
            ) : (
              <div className="setting-actions">
                <button
                  className="primary"
                  disabled={busy || key === null}
                  onClick={() => guard(async () => setKey(await api.generateSshKey()))}
                >
                  Generate key
                </button>
                <span className="muted">No passphrase; host keys are accept-new.</span>
              </div>
            )}
          </section>

          <div className="footnote">
            <span>
              Credentials live in a container volume, never bound to the host,
              and leave it only as traffic to the provider.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
