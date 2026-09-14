/**
 * Settings — the agent account and git access.
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
 * The URL arrives from a poll rather than from a click, so it is rendered as a
 * link and never opened programmatically — a `window.open()` outside a user
 * gesture is what popup blockers exist for, and the failure would be silent.
 *
 * Polled, not streamed: SSE arrives in stage 5, and a login has four state
 * changes over a minute of human time.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AgentStatus, LoginState, SshKeyStatus } from "../types";

const POLL_MS = 1000;

export function Settings({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [login, setLogin] = useState<LoginState | null>(null);
  const [key, setKey] = useState<SshKeyStatus | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [urlCopied, setUrlCopied] = useState(false);
  const live = useRef(true);

  const refreshStatus = useCallback(async () => {
    const next = await api.agentStatus();
    if (live.current) setStatus(next);
  }, []);

  useEffect(() => {
    live.current = true;
    Promise.all([api.agentStatus(), api.loginState(), api.sshKey()])
      .then(([account, flow, sshKey]) => {
        if (!live.current) return;
        setStatus(account);
        setLogin(flow);
        setKey(sshKey);
      })
      .catch((cause: unknown) => live.current && setError(String(cause)));
    return () => {
      live.current = false;
    };
  }, []);

  // Poll only while a flow is in flight. A settings screen left open must not
  // sit there asking a question whose answer cannot change.
  useEffect(() => {
    if (!login?.active) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.loginState();
        if (!live.current) return;
        setLogin(next);
        if (!next.active) void refreshStatus();
      } catch {
        /* a poll that fails is retried on the next tick */
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [login?.active, refreshStatus]);

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
  const signedIn = account?.authenticated === true;

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
            Once per machine: sign the container in to your agent provider, and
            give it a key so a run can push.
          </p>

          {error && <div className="error">{error}</div>}

          {/* ---- agent account ------------------------------------------- */}
          <section className="setting">
            <div className="setting-head">
              <span className="eyebrow">Agent account</span>
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
              <dt>claude CLI</dt>
              <dd className="mono">{account?.version ?? "—"}</dd>
              <dt>account</dt>
              <dd>{account?.detail || "—"}</dd>
              <dt>default model</dt>
              {/* Read from the CLI's own handshake, never hardcoded. */}
              <dd className="mono">
                {handshake?.default_model ?? handshake?.detail ?? "—"}
              </dd>
            </dl>

            {!signedIn && !login?.active && (
              <div className="setting-actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    guard(async () => {
                      setCode("");
                      setLogin(await api.beginLogin());
                    })
                  }
                >
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
                      Approve the sign-in in your browser, then paste the code
                      it gives you back here.
                    </p>
                    {/* A real anchor, clicked by the operator. Never
                        `window.open()` on arrival: the URL lands from a poll,
                        which is not a user gesture, so a popup blocker would
                        swallow it and nothing at all would appear to happen.
                        A clicked link is always a gesture and is never
                        blocked. `noopener` because an OAuth page must not get
                        a handle on `window.opener`. */}
                    <a
                      className="open-signin"
                      href={login.verification_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open sign-in page ↗
                    </a>
                    {/* The raw URL stays visible and selectable: approving in
                        a different browser or a different profile from the one
                        running Factor-ia means carrying it across by hand. */}
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
                          void navigator.clipboard?.writeText(
                            login.verification_url ?? "",
                          );
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
                    void guard(async () => {
                      // Sent whole: the value is `<code>#<state>` and the `#`
                      // is not a URL fragment to trim off.
                      setLogin(await api.submitLoginCode(value));
                      setCode("");
                    });
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
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      guard(async () => {
                        setLogin(await api.cancelLogin());
                        await refreshStatus();
                      })
                    }
                  >
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

          {/* ---- git access ---------------------------------------------- */}
          <section className="setting">
            <div className="setting-head">
              <span className="eyebrow">Git access</span>
              <span className={key?.exists ? "ok" : "bad"}>
                {key === null ? "checking…" : key.exists ? "key present" : "no key"}
              </span>
            </div>
            <p className="lede">
              One ed25519 keypair, generated inside the container. Only the
              public half is ever shown — add it to your forge to let a run push.
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
              Credentials live in a container volume that is never bound to the
              host, and leave it only as traffic to the provider.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
