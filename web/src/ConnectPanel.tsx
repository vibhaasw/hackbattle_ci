import { useEffect, useRef, useState } from "react";
import {
  fetchWorkflows,
  primeDemo,
  putWorkflows,
  saveSetup,
  type Status,
  type Workflow,
} from "./api";

interface Theme {
  bg: string;
  card: string;
  border: string;
  textPrimary: string;
  textMuted: string;
  textDim: string;
  rowHover: string;
}

const MONO = "'JetBrains Mono', ui-monospace, monospace";

function IconPlug() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
      <path
        d="M9.5 4.5L4.5 9.5M5 2L2 5l2.5 2.5M9 12l3-3-2.5-2.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ConnectPanel({ t, status, onSaved }: { t: Theme; status: Status | null; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [hBtn, setHBtn] = useState(false);
  const [tab, setTab] = useState<"github" | "slack" | "workflows">("github");
  const [repo, setRepo] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [wfName, setWfName] = useState("");
  const [wfRepo, setWfRepo] = useState("");
  const [wfKeywords, setWfKeywords] = useState("");
  const [wfProfile, setWfProfile] = useState("shipping");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (status?.repo) setRepo(status.repo);
  }, [status?.repo]);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    fetchWorkflows()
      .then((data) => setWorkflows(data.workflows || []))
      .catch(() => undefined);
  }, [open]);

  async function submit() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await saveSetup({
        github_repo: repo,
        github_token: githubToken,
        slack_bot_token: bot,
        slack_app_token: app,
      });
      setGithubToken("");
      setBot("");
      setApp("");
      const bits = [`saved ${result.repo || "config"}`];
      if (result.webhook_id) bits.push(`webhook ${result.webhook_action} #${result.webhook_id}`);
      if (result.slack_workspace) bits.push(result.slack_workspace);
      if (result.webhook_action === "pending_ngrok") bits.push("waiting for ngrok");
      setMessage(bits.join(" · "));
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const githubOn = Boolean(status?.github_token_set);
  const slackOn = Boolean(status?.slack_bot_set && status?.slack_app_set);

  return (
    <div ref={ref} style={{ position: "relative", flexShrink: 0 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setHBtn(true)}
        onMouseLeave={() => setHBtn(false)}
        style={{
          background: open || hBtn ? t.rowHover : "transparent",
          border: `1.5px solid ${open ? "#38BDF8" : hBtn ? t.textMuted : t.border}`,
          color: open ? "#38BDF8" : hBtn ? t.textPrimary : t.textMuted,
          height: 28,
          padding: "0 8px",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          borderRadius: 4,
          outline: "none",
        }}
        title="Add GitHub and Slack API keys"
      >
        <IconPlug />
        <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.04em", marginLeft: 6 }}>
          keys
        </span>
      </button>
      <div
        style={{
          position: "absolute",
          top: "calc(100% + 8px)",
          right: 0,
          zIndex: 200,
          width: tab === "workflows" ? 380 : 340,
          backgroundColor: t.card,
          border: `1.5px solid ${t.border}`,
          borderRadius: 8,
          overflow: "hidden",
          opacity: open ? 1 : 0,
          transform: open ? "scale(1) translateY(0)" : "scale(0.97) translateY(-4px)",
          pointerEvents: open ? "auto" : "none",
          transition: "opacity 0.15s ease, transform 0.15s ease",
          transformOrigin: "top right",
        }}
      >
        <div style={{ padding: "10px 16px 9px", borderBottom: `1.5px solid ${t.border}` }}>
          <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.08em", color: t.textMuted }}>
            CONNECT YOUR TOOLS
          </span>
        </div>
        <div style={{ display: "flex", borderBottom: `1.5px solid ${t.border}` }}>
          {(
            [
              ["github", "GitHub", githubOn],
              ["slack", "Slack", slackOn],
              ["workflows", "Flows", workflows.length > 0],
            ] as const
          ).map(([id, label, on]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              style={{
                flex: 1,
                background: tab === id ? t.rowHover : "transparent",
                border: "none",
                borderBottom: tab === id ? "2px solid #38BDF8" : "2px solid transparent",
                color: tab === id ? t.textPrimary : t.textMuted,
                fontFamily: MONO,
                fontSize: 11,
                padding: "8px 0",
                cursor: "pointer",
              }}
            >
              {label}
              <span style={{ color: on ? "#38BDF8" : t.textDim, marginLeft: 6 }}>{on ? "●" : "○"}</span>
            </button>
          ))}
        </div>
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          {tab === "github" ? (
            <>
              <Field t={t} label="repo (owner/repo)" value={repo} onChange={setRepo} placeholder="you/your-repo" />
              <Field
                t={t}
                label="personal access token"
                value={githubToken}
                onChange={setGithubToken}
                placeholder={status?.github_token_set ? "leave blank to keep current" : "github_pat_… or ghp_…"}
                secret
              />
              <p style={{ margin: 0, fontSize: 10, color: t.textDim, lineHeight: 1.45 }}>
                Needs admin:repo_hooks (classic) or Webhooks: Read and write. The launcher updates the webhook to the
                current ngrok URL automatically.
              </p>
            </>
          ) : tab === "slack" ? (
            <>
              <Field
                t={t}
                label="SLACK_BOT_TOKEN"
                value={bot}
                onChange={setBot}
                placeholder={status?.slack_bot_set ? "leave blank to keep current" : "xoxb-…"}
                secret
              />
              <Field
                t={t}
                label="SLACK_APP_TOKEN"
                value={app}
                onChange={setApp}
                placeholder={status?.slack_app_set ? "leave blank to keep current" : "xapp-…"}
                secret
              />
              <p style={{ margin: 0, fontSize: 10, color: t.textDim, lineHeight: 1.45 }}>
                Create these in Slack’s app UI first (Socket Mode + bot token). This form cannot generate them.
              </p>
            </>
          ) : (
            <>
              <p style={{ margin: 0, fontSize: 10, color: t.textDim, lineHeight: 1.45 }}>
                One pane per workflow. Match a repo, Slack channel, or keyword. Priority rules stay in Python.
              </p>
              {workflows.map((wf) => (
                <div key={wf.id} style={{ fontSize: 11, color: t.textPrimary, display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span>
                    <span style={{ color: wf.color || "#38BDF8" }}>●</span> {wf.name}
                    {wf.catch_all ? " · catch-all" : ""}
                  </span>
                  <span style={{ color: t.textMuted }}>{wf.urgency_profile}</span>
                </div>
              ))}
              <Field t={t} label="new workflow name" value={wfName} onChange={setWfName} placeholder="shipping / oncall / reviews" />
              <Field t={t} label="github repo (optional)" value={wfRepo} onChange={setWfRepo} placeholder="you/your-repo" />
              <Field t={t} label="keywords (comma)" value={wfKeywords} onChange={setWfKeywords} placeholder="prod, incident" />
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 10, letterSpacing: "0.06em", color: t.textMuted }}>priority profile</span>
                <select
                  value={wfProfile}
                  onChange={(e) => setWfProfile(e.target.value)}
                  style={{
                    background: t.bg,
                    border: `1.5px solid ${t.border}`,
                    borderRadius: 4,
                    color: t.textPrimary,
                    fontFamily: MONO,
                    fontSize: 11,
                    padding: "6px 8px",
                    outline: "none",
                  }}
                >
                  <option value="shipping">shipping — prod/fail first</option>
                  <option value="oncall">oncall — Slack pings first</option>
                  <option value="review">review — PRs first, chat quiet</option>
                  <option value="quiet">quiet — incidents only</option>
                </select>
              </label>
            </>
          )}
          {error ? <span style={{ color: "#E5484D", fontSize: 11 }}>{error}</span> : null}
          {message ? <span style={{ color: "#38BDF8", fontSize: 11 }}>{message}</span> : null}
          {tab !== "workflows" ? (
          <button
            onClick={submit}
            disabled={busy}
            style={{
              marginTop: 4,
              background: "transparent",
              border: "1.5px solid #38BDF8",
              color: "#38BDF8",
              fontFamily: MONO,
              fontSize: 11,
              padding: "7px 0",
              cursor: busy ? "wait" : "pointer",
              borderRadius: 4,
            }}
          >
            {busy ? "saving…" : "save & connect"}
          </button>
          ) : (
          <button
            onClick={async () => {
              if (!wfName.trim()) {
                setError("name the workflow");
                return;
              }
              setBusy(true);
              setError("");
              setMessage("");
              try {
                const slug = wfName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "workflow";
                const next = [
                  ...workflows.filter((w) => !w.catch_all && w.id !== slug),
                  {
                    id: slug,
                    name: wfName.trim(),
                    color: "#34D399",
                    urgency_profile: wfProfile,
                    github_repos: wfRepo.trim() ? [wfRepo.trim()] : [],
                    slack_channels: [],
                    keywords: wfKeywords.split(",").map((part) => part.trim()).filter(Boolean),
                  },
                  ...workflows.filter((w) => w.catch_all),
                ];
                const saved = await putWorkflows(next);
                setWorkflows(saved.workflows || next);
                setWfName("");
                setWfRepo("");
                setWfKeywords("");
                setMessage(`workflow ${slug} saved`);
                onSaved();
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : String(exc));
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            style={{
              marginTop: 4,
              background: "transparent",
              border: "1.5px solid #38BDF8",
              color: "#38BDF8",
              fontFamily: MONO,
              fontSize: 11,
              padding: "7px 0",
              cursor: busy ? "wait" : "pointer",
              borderRadius: 4,
            }}
          >
            {busy ? "saving…" : "add workflow"}
          </button>
          )}
          <button
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                const result = await primeDemo();
                setMessage(`cleared ${result.dismissed} leftover item(s) — stats at 0`);
                onSaved();
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : String(exc));
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            style={{
              background: "transparent",
              border: `1.5px solid ${t.border}`,
              color: t.textMuted,
              fontFamily: MONO,
              fontSize: 11,
              padding: "7px 0",
              cursor: busy ? "wait" : "pointer",
              borderRadius: 4,
            }}
          >
            clear leftover test items
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  t,
  label,
  value,
  onChange,
  placeholder,
  secret,
}: {
  t: Theme;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  secret?: boolean;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 10, letterSpacing: "0.06em", color: t.textMuted }}>{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        style={{
          background: t.bg,
          border: `1.5px solid ${t.border}`,
          borderRadius: 4,
          color: t.textPrimary,
          fontFamily: MONO,
          fontSize: 11,
          padding: "6px 8px",
          outline: "none",
        }}
      />
    </label>
  );
}
