import { useState, useEffect, useRef } from "react";
import { ConnectPanel } from "./ConnectPanel";
import {
  deferNotification,
  dismissNotification,
  fetchQueue,
  fetchStatus,
  patchWorkflow,
  releaseQueue,
  type Notification as LiveNotification,
  type Stats,
  type Status,
  type Workflow,
} from "./api";

type Urgency = "urgent" | "normal" | "low";
type Source = "github" | "slack";

interface Notification {
  id: string;
  urgency: Urgency;
  title: string;
  subtitle: string;
  summary: string;
  source: Source;
  timestamp: number;
  projectId: string;
  workflowId?: string;
  url?: string;
  isNew?: boolean;
}

interface Project {
  id: string;
  name: string;
  color: string;
  urgency_profile?: string;
  catch_all?: boolean;
}

const SOURCE_PANES: Project[] = [
  { id: "github", name: "github", color: "#38BDF8", urgency_profile: "shipping" },
  { id: "slack", name: "slack", color: "#A78BFA", urgency_profile: "oncall" },
];

function extraWorkflowPanes(rows: Workflow[]): Project[] {
  return rows
    .filter((row) => !["github", "slack", "inbox"].includes(row.id) && !row.catch_all)
    .map((row) => ({
      id: row.id,
      name: row.name,
      color: row.color || "#34D399",
      urgency_profile: row.urgency_profile,
      catch_all: row.catch_all,
    }));
}

function withProfiles(panes: Project[], rows: Workflow[]): Project[] {
  return panes.map((pane) => {
    const match = rows.find((row) => row.id === pane.id);
    return match
      ? { ...pane, urgency_profile: match.urgency_profile || pane.urgency_profile, color: match.color || pane.color }
      : pane;
  });
}

const MONO = "'JetBrains Mono', ui-monospace, monospace" as const;

interface Theme {
  bg: string; card: string; border: string;
  textPrimary: string; textMuted: string; textDim: string; textFaint: string;
  rowHover: string; gridDot: string; dividerRow: string;
}
const THEMES: Record<string, Theme> = {
  dark:  { bg: "#0F172A", card: "#1E293B", border: "#334155", textPrimary: "#F1F5F9", textMuted: "#94A3B8", textDim: "#64748B", textFaint: "#334155", rowHover: "#253047", gridDot: "rgba(56,189,248,0.07)", dividerRow: "#1A2540" },
  light: { bg: "#F4F5F7", card: "#FFFFFF",  border: "#DDE1E8", textPrimary: "#1A1D23", textMuted: "#6B7280", textDim: "#8B93A5", textFaint: "#B0B8C8", rowHover: "#F8F9FB", gridDot: "rgba(56,189,248,0.12)", dividerRow: "#EEF0F4" },
};
type ThemeKey = "dark" | "light";

const FULL_TITLE = "context interrupter";

// Fake, illustrative "chaos" items for the without/with comparison —
// not real queue data, just a visual metaphor for constant interruption.
const CHAOS_ITEMS = [
  { label: "[Slack]",  text: "3 new messages in #general",   top: "6%",  left: "4%",  rot: -6, delay: 0.0 },
  { label: "[GitHub]", text: "CI pipeline failed",            top: "2%",  left: "50%", rot: 4,  delay: 0.4 },
  { label: "[Slack]",  text: "@here — prod incident",         top: "34%", left: "16%", rot: 3,  delay: 0.9 },
  { label: "[GitHub]", text: "PR review requested",           top: "38%", left: "58%", rot: -4, delay: 1.3 },
  { label: "[Slack]",  text: "DM from @sam",                  top: "62%", left: "6%",  rot: -3, delay: 1.8 },
  { label: "[GitHub]", text: "merge conflict detected",       top: "58%", left: "48%", rot: 5,  delay: 2.2 },
  { label: "[Slack]",  text: "thread reply · #frontend",      top: "82%", left: "22%", rot: -5, delay: 2.6 },
  { label: "[GitHub]", text: "issue #998 commented",          top: "80%", left: "56%", rot: 2,  delay: 3.0 },
];

function relativeTime(ts: number) {
  const d = Math.floor((Date.now() - ts) / 1000);
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
}

function urgencyStripColor(u: Urgency) {
  return u === "urgent" ? "#E5484D" : u === "normal" ? "#C47A1E" : "#3A404D";
}

function urgencyGlow(u: Urgency) {
  if (u === "urgent") return "0 0 0 1.5px rgba(229,72,77,0.25), 0 0 14px 2px rgba(229,72,77,0.10)";
  if (u === "normal") return "0 0 0 1.5px rgba(196,122,30,0.30), 0 0 14px 2px rgba(196,122,30,0.12)";
  return "0 0 0 1.5px rgba(56,189,248,0.18), 0 0 14px 2px rgba(56,189,248,0.07)";
}

// ── Animated stat number ────────────────────────────────────────────────────
function useCountUp(target: number, duration = 900, delay = 0) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const tid = setTimeout(() => {
      const start = performance.now();
      function step(now: number) {
        if (cancelled) return;
        const p = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        setVal(Math.round(target * eased));
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }, delay);
    return () => { cancelled = true; clearTimeout(tid); };
  }, [target, duration, delay]);
  return val;
}

// ── Icons ───────────────────────────────────────────────────────────────────
function LogoMark() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 26, height: 26, borderRadius: 6, backgroundColor: "#38BDF8", flexShrink: 0 }}>
      <svg width="14" height="11" viewBox="0 0 14 11" fill="none">
        <path d="M1 1.5L5 5.5L1 9.5" stroke="#0F172A" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <line x1="7" y1="9.5" x2="13" y2="9.5" stroke="#0F172A" strokeWidth="2" strokeLinecap="round" />
      </svg>
    </span>
  );
}
function IconGithub() {
  return <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>;
}
function IconSlack() {
  return <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" /></svg>;
}
function IconWarning() {
  return <svg width="9" height="9" viewBox="0 0 10 10" fill="none" style={{ flexShrink: 0 }}><path d="M5 1L9.33 8.5H.67L5 1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /><line x1="5" y1="4" x2="5" y2="6.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /><circle cx="5" cy="7.3" r="0.5" fill="currentColor" /></svg>;
}
function IconDash() {
  return <svg width="9" height="9" viewBox="0 0 10 10" fill="none" style={{ flexShrink: 0 }}><line x1="2" y1="5" x2="8" y2="5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>;
}
function IconCircle() {
  return <svg width="9" height="9" viewBox="0 0 10 10" fill="none" style={{ flexShrink: 0 }}><circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.3" /></svg>;
}
function IconExternalLink() {
  return <svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M5 2H2a1 1 0 00-1 1v7a1 1 0 001 1h7a1 1 0 001-1V7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /><path d="M8 1h3v3M11 1L6 6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
function IconClock() {
  return <svg width="11" height="11" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.3" /><path d="M6 3.5V6l2 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
function IconX() {
  return <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>;
}

// ── Blinking cursor ─────────────────────────────────────────────────────────
function BlinkingCursor() {
  return (
    <span
      className="cursor-blink"
      style={{
        display: "inline-block",
        width: 2,
        height: "1em",
        backgroundColor: "#38BDF8",
        marginLeft: 3,
        verticalAlign: "text-bottom",
        borderRadius: 1,
      }}
    />
  );
}

// ── Pulsing status dot ──────────────────────────────────────────────────────
function PulsingDot() {
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 14, height: 14 }}>
      {/* Expanding ring */}
      <span
        className="pulse-ring"
        style={{
          position: "absolute",
          width: 7,
          height: 7,
          borderRadius: "50%",
          backgroundColor: "transparent",
          border: "1.5px solid #38BDF8",
          transformOrigin: "center",
        }}
      />
      {/* Solid core dot */}
      <span style={{ position: "relative", width: 7, height: 7, borderRadius: "50%", backgroundColor: "#38BDF8", flexShrink: 0 }} />
    </span>
  );
}

// ── Typing header title ─────────────────────────────────────────────────────
function TypingTitle({ t }: { t: Theme }) {
  const [chars, setChars] = useState(0);
  const done = chars >= FULL_TITLE.length;
  useEffect(() => {
    if (done) return;
    const id = setTimeout(() => setChars((c) => c + 1), 55);
    return () => clearTimeout(id);
  }, [chars, done]);
  return (
    <span style={{ color: t.textPrimary, fontWeight: 600, fontSize: 13, letterSpacing: "0.02em", transition: "color 0.3s" }}>
      {FULL_TITLE.slice(0, chars)}
      <BlinkingCursor />
    </span>
  );
}

// ── Urgency tag ─────────────────────────────────────────────────────────────
function UrgencyTag({ urgency }: { urgency: Urgency }) {
  const cfg = {
    urgent: { bg: "rgba(229,72,77,0.13)",  color: "#E5484D", border: "rgba(229,72,77,0.28)",  icon: <IconWarning /> },
    normal: { bg: "rgba(196,122,30,0.12)", color: "#C47A1E", border: "rgba(196,122,30,0.25)", icon: <IconCircle /> },
    low:    { bg: "rgba(58,64,77,0.45)",   color: "#5A6478", border: "rgba(58,64,77,0.7)",    icon: <IconDash /> },
  }[urgency];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 3,
      backgroundColor: cfg.bg, color: cfg.color,
      fontWeight: 600, fontSize: 10, letterSpacing: "0.06em",
      padding: "1px 5px 1px 4px", borderRadius: 3, marginLeft: 7,
      verticalAlign: "middle", lineHeight: "16px",
      border: `1px solid ${cfg.border}`,
    }}>
      {cfg.icon}{urgency}
    </span>
  );
}

// ── Row action button ───────────────────────────────────────────────────────
function RowAction({ icon, title, onClick }: { icon: React.ReactNode; title: string; onClick?: () => void }) {
  const [h, setH] = useState(false);
  return (
    <button onClick={onClick} title={title}
      onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{
        background: h ? "rgba(255,255,255,0.06)" : "transparent",
        border: "1.5px solid #2E3440", borderRadius: 3,
        color: h ? "#E7E9EC" : "#5A6478", width: 24, height: 24,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", transition: "all 0.1s", outline: "none", flexShrink: 0,
      }}
    >
      {icon}
    </button>
  );
}

// ── Queue row ────────────────────────────────────────────────────────────────
function QueueRow({ notif, isLast, onDismiss, onDefer, onOpen, t }: { notif: Notification; isLast: boolean; onDismiss: (id: string) => void; onDefer: (id: string) => void; onOpen: (notif: Notification) => void; t: Theme }) {
  const [hovered, setHovered] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [, forceUpdate] = useState(0);
  const titleColor = notif.urgency === "urgent" ? "#E5484D" : notif.urgency === "low" ? t.textDim : t.textPrimary;
  const accentColor = notif.urgency === "urgent" ? "#E5484D" : notif.urgency === "normal" ? "#C47A1E" : "#38BDF8";

  useEffect(() => {
    const id = setInterval(() => forceUpdate((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={notif.isNew ? "row-new" : undefined}
      style={{
        display: "flex", flexDirection: "column",
        borderBottom: isLast ? "none" : `1.5px solid ${t.dividerRow}`,
        backgroundColor: expanded ? t.rowHover : hovered ? t.rowHover : t.card,
        transition: "background-color 0.12s, box-shadow 0.2s",
        boxShadow: hovered || expanded ? urgencyGlow(notif.urgency) : "none",
        position: "relative",
        zIndex: hovered || expanded ? 1 : 0,
        cursor: "pointer",
      }}
      onClick={() => setExpanded((v) => !v)}
    >
      {/* Main row */}
      <div style={{ display: "flex", alignItems: "stretch" }}>
        <div style={{ width: 3, flexShrink: 0, backgroundColor: urgencyStripColor(notif.urgency) }} />
        <div style={{
          flex: 1, display: "grid", gridTemplateColumns: "1fr auto",
          gap: "0 12px", padding: "9px 14px", alignItems: "center", minWidth: 0,
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ color: titleColor, fontWeight: 700, fontSize: 12.5, lineHeight: 1.4, wordBreak: "break-word" }}>
                {notif.title}
              </span>
              <UrgencyTag urgency={notif.urgency} />
            </div>
            <div style={{ color: t.textDim, fontSize: 11, marginTop: 2, lineHeight: 1.4 }}>{notif.subtitle}</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <div style={{ display: "flex", gap: 4, opacity: hovered ? 1 : 0, transition: "opacity 0.15s", pointerEvents: hovered ? "auto" : "none" }}
              onClick={(e) => e.stopPropagation()}
            >
              <RowAction icon={<IconExternalLink />} title="Open" onClick={() => onOpen(notif)} />
              <RowAction icon={<IconClock />} title="Defer" onClick={() => onDefer(notif.id)} />
              <RowAction icon={<IconX />} title="Dismiss" onClick={() => onDismiss(notif.id)} />
            </div>
            <div className="timestamp-col" style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color: t.textPrimary, fontSize: 11 }}>
                {notif.source === "github" ? <IconGithub /> : <IconSlack />}
                {notif.source}
              </span>
              <span style={{ color: t.textPrimary, fontSize: 11, whiteSpace: "nowrap" }}>{relativeTime(notif.timestamp)}</span>
            </div>
            {/* Expand chevron */}
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ color: t.textMuted, flexShrink: 0, transition: "transform 0.2s", transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}>
              <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
        </div>
      </div>

      {/* Summary panel */}
      <div style={{
        overflow: "hidden",
        maxHeight: expanded ? 400 : 0,
        transition: "max-height 0.3s cubic-bezier(0.4,0,0.2,1)",
      }}>
        <div style={{
          margin: "0 14px 12px 17px",
          padding: "10px 14px",
          borderRadius: 6,
          border: `1.5px solid ${accentColor}33`,
          backgroundColor: `${accentColor}08`,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ color: accentColor, flexShrink: 0 }}>
              <circle cx="5" cy="5" r="4" stroke="currentColor" strokeWidth="1.3" />
              <path d="M5 3.5v2.5M5 7h.01" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
            <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.09em", color: accentColor, textTransform: "uppercase" as const }}>summary</span>
          </div>
          <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.6, color: t.textPrimary, fontFamily: MONO }}>
            {notif.summary}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Filter chip ──────────────────────────────────────────────────────────────
function FilterChip({ label, active, onClick, t }: { label: string; active: boolean; onClick: () => void; t: Theme }) {
  const [h, setH] = useState(false);
  return (
    <button onClick={onClick} onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{
        background: active ? "#38BDF8" : h ? (t === THEMES.dark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)") : "transparent",
        border: `1.5px solid ${active ? "#38BDF8" : h ? t.textDim : t.border}`,
        color: active ? "#0F172A" : h ? t.textPrimary : t.textMuted,
        fontFamily: MONO, fontSize: 11, letterSpacing: "0.04em",
        padding: "3px 10px", borderRadius: 4, cursor: "pointer",
        transition: "all 0.12s", outline: "none", whiteSpace: "nowrap",
        fontWeight: active ? 700 : 400,
      }}
    >
      {label}
    </button>
  );
}

// ── Stat cell with count-up ─────────────────────────────────────────────────
function StatCell({ target, liveValue, label, accent, bordered, animDelay, t }: {
  target: number; liveValue: number; label: string; accent?: boolean; bordered?: boolean; animDelay?: number; t: Theme;
}) {
  const counted = useCountUp(target, 900, animDelay ?? 0);
  const prevLive = useRef(liveValue);
  const [displayed, setDisplayed] = useState(0);
  const [animDir, setAnimDir] = useState<"up" | null>(null);

  // On-load count-up drives the display
  useEffect(() => { setDisplayed(counted); }, [counted]);

  // Live updates after load — no guard on counted
  useEffect(() => {
    if (liveValue === prevLive.current) return;
    setAnimDir("up");
    const from = prevLive.current;
    prevLive.current = liveValue;
    const to = liveValue;
    const start = performance.now();
    function step(now: number) {
      const pct = Math.min((now - start) / 600, 1);
      const e = 1 - Math.pow(1 - pct, 3);
      setDisplayed(Math.round(from + (to - from) * e));
      if (pct < 1) requestAnimationFrame(step);
      else setAnimDir(null);
    }
    requestAnimationFrame(step);
  }, [liveValue]);

  return (
    <div style={{ padding: "18px 20px 16px", borderLeft: bordered ? `1.5px solid ${t.border}` : "none" }}>
      <div style={{
        fontSize: 32, fontWeight: 700, lineHeight: 1, letterSpacing: "-0.03em",
        color: accent ? "#38BDF8" : t.textPrimary,
        transform: animDir === "up" ? "translateY(-2px)" : "none",
        transition: "transform 0.15s",
      }}>
        {displayed}
      </div>
      <div style={{ fontSize: 10.5, color: t.textDim, letterSpacing: "0.04em", marginTop: 5 }}>{label}</div>
    </div>
  );
}

// ── Group divider ────────────────────────────────────────────────────────────
function GroupDivider({ label, t }: { label: string; t: Theme }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 14px", backgroundColor: t.bg, borderBottom: `1.5px solid ${t.dividerRow}` }}>
      <span style={{ color: t.textFaint, fontSize: 10, letterSpacing: "0.08em", fontWeight: 600, whiteSpace: "nowrap" }}>{label}</span>
      <div style={{ flex: 1, height: 1, backgroundColor: t.border }} />
    </div>
  );
}

// ── Toast ────────────────────────────────────────────────────────────────────
function Toast({ visible }: { visible: boolean }) {
  return (
    <div style={{
      position: "fixed", top: 0, left: 0, right: 0, zIndex: 50,
      display: "flex", justifyContent: "center", pointerEvents: "none",
      transform: visible ? "translateY(0)" : "translateY(-110%)",
      transition: "transform 0.32s cubic-bezier(0.4, 0, 0.2, 1)",
    }}>
      <div style={{ marginTop: 16, display: "inline-flex", alignItems: "center", gap: 8, backgroundColor: "#0F2233", border: "1.5px solid #38BDF8", padding: "9px 18px", borderRadius: 4 }}>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <circle cx="6" cy="6" r="5" stroke="#38BDF8" strokeWidth="1.4" />
          <path d="M3.5 6l2 2 3-3" stroke="#38BDF8" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span style={{ color: "#38BDF8", fontSize: 12, fontWeight: 600, letterSpacing: "0.04em", fontFamily: MONO }}>queue released</span>
      </div>
    </div>
  );
}

// ── Connect tools dropdown ───────────────────────────────────────────────────
type ToolId = "gmail" | "github" | "calendar";
interface Tool { id: ToolId; label: string; icon: React.ReactNode; }

function IconGmail() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d="M2 6.5A2.5 2.5 0 0 1 4.5 4h15A2.5 2.5 0 0 1 22 6.5v11A2.5 2.5 0 0 1 19.5 20h-15A2.5 2.5 0 0 1 2 17.5v-11Z" fill="#F2F2F2"/>
      <path d="M2 7l10 7 10-7V6.5A2.5 2.5 0 0 0 19.5 4h-15A2.5 2.5 0 0 0 2 6.5V7Z" fill="#EA4335"/>
      <path d="M2 7v10.5A2.5 2.5 0 0 0 4.5 20H8V12l4 3 4-3v8h3.5A2.5 2.5 0 0 0 22 17.5V7L12 14 2 7Z" fill="#D93025"/>
      <path d="M8 12v8H4.5A2.5 2.5 0 0 1 2 17.5V7l6 5Z" fill="#C5221F"/>
      <path d="M16 12v8h3.5A2.5 2.5 0 0 0 22 17.5V7l-6 5Z" fill="#C5221F"/>
    </svg>
  );
}
function IconGithubColor() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24">
      <rect width="24" height="24" rx="4" fill="#24292E"/>
      <path d="M12 2C6.477 2 2 6.477 2 12c0 4.418 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.008-.866-.013-1.7-2.782.604-3.369-1.341-3.369-1.341-.454-1.155-1.11-1.463-1.11-1.463-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.092-.646.35-1.086.636-1.336-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0 1 12 6.836c.85.004 1.705.114 2.504.336 1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.578.688.48C19.138 20.163 22 16.418 22 12c0-5.523-4.477-10-10-10z" fill="white"/>
    </svg>
  );
}
function IconGoogleCalendar() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <rect x="3" y="4" width="18" height="17" rx="2" fill="#fff"/>
      <rect x="3" y="4" width="18" height="6" rx="2" fill="#1A73E8"/>
      <rect x="3" y="8" width="18" height="2" fill="#1A73E8"/>
      <path d="M8 4V2M16 4V2" stroke="#1A73E8" strokeWidth="1.8" strokeLinecap="round"/>
      <text x="12" y="19" textAnchor="middle" fontSize="7" fontWeight="700" fill="#1A73E8" fontFamily="Arial,sans-serif">31</text>
    </svg>
  );
}
function IconPlug() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
      <path d="M9.5 4.5L4.5 9.5M5 2L2 5l2.5 2.5M9 12l3-3-2.5-2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M8.5 5.5l-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}

const TOOLS: Tool[] = [
  { id: "gmail",    label: "Gmail",    icon: <IconGmail /> },
  { id: "github",   label: "GitHub",   icon: <IconGithubColor /> },
  { id: "calendar", label: "Calendar", icon: <IconGoogleCalendar /> },
];

function ConnectDropdown({ t }: { t: Theme }) {
  const [open, setOpen] = useState(false);
  const [hBtn, setHBtn] = useState(false);
  const [connected, setConnected] = useState<Record<ToolId, boolean>>({ gmail: false, github: true, calendar: false });
  const [rowHover, setRowHover] = useState<ToolId | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", flexShrink: 0 }}>
      {/* Trigger button */}
      <button
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setHBtn(true)}
        onMouseLeave={() => setHBtn(false)}
        style={{
          background: open || hBtn ? t.rowHover : "transparent",
          border: `1.5px solid ${open ? "#38BDF8" : hBtn ? t.textMuted : t.border}`,
          color: open ? "#38BDF8" : hBtn ? t.textPrimary : t.textMuted,
          width: 28, height: 28, display: "inline-flex", alignItems: "center", justifyContent: "center",
          cursor: "pointer", borderRadius: 4, transition: "all 0.15s", outline: "none",
        }}
        title="Connect tools"
      >
        <IconPlug />
      </button>

      {/* Dropdown panel */}
      <div style={{
        position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 200,
        width: 300,
        backgroundColor: t.card,
        border: `1.5px solid ${t.border}`,
        borderRadius: 8,
        overflow: "hidden",
        opacity: open ? 1 : 0,
        transform: open ? "scale(1) translateY(0)" : "scale(0.97) translateY(-4px)",
        pointerEvents: open ? "auto" : "none",
        transition: "opacity 0.15s ease, transform 0.15s ease",
        transformOrigin: "top right",
      }}>
        {/* Header */}
        <div style={{ padding: "10px 16px 9px", borderBottom: `1.5px solid ${t.border}` }}>
          <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.08em", color: t.textMuted, textTransform: "uppercase" as const }}>
            connect your tools
          </span>
        </div>

        {/* Tool rows */}
        {TOOLS.map((tool, i) => {
          const isConnected = connected[tool.id];
          const isRowHovered = rowHover === tool.id;
          return (
            <div
              key={tool.id}
              onMouseEnter={() => setRowHover(tool.id)}
              onMouseLeave={() => setRowHover(null)}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "11px 16px",
                borderBottom: i < TOOLS.length - 1 ? `1.5px solid ${t.border}` : "none",
                backgroundColor: isRowHovered ? t.rowHover : "transparent",
                transition: "background-color 0.1s",
              }}
            >
              {/* Brand icon */}
              <span style={{ display: "inline-flex", flexShrink: 0, width: 20, height: 20, alignItems: "center", justifyContent: "center" }}>
                {tool.icon}
              </span>

              {/* Label */}
              <span style={{ fontSize: 12, fontWeight: 600, color: t.textPrimary, flex: 1, fontFamily: MONO }}>
                {tool.label}
              </span>

              {/* Status pill */}
              <span style={{
                display: "inline-flex", alignItems: "center", gap: 5,
                padding: "2px 8px", borderRadius: 6,
                border: `1.5px solid ${isConnected ? "rgba(56,189,248,0.3)" : "rgba(100,110,130,0.25)"}`,
                backgroundColor: isConnected ? "rgba(56,189,248,0.08)" : "transparent",
              }}>
                <span style={{ width: 5, height: 5, borderRadius: "50%", backgroundColor: isConnected ? "#38BDF8" : "#4A505C", flexShrink: 0, display: "inline-block" }} />
                <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.05em", color: isConnected ? "#38BDF8" : t.textMuted, whiteSpace: "nowrap" as const }}>
                  {isConnected ? "connected" : "not connected"}
                </span>
              </span>

              {/* Toggle switch */}
              <button
                onClick={() => setConnected((c) => ({ ...c, [tool.id]: !c[tool.id] }))}
                style={{
                  flexShrink: 0, cursor: "pointer", outline: "none", border: "none", padding: 0,
                  width: 36, height: 20, borderRadius: 10,
                  backgroundColor: isConnected ? "#38BDF8" : t.border,
                  transition: "background-color 0.2s",
                  position: "relative" as const,
                }}
                title={isConnected ? "Disconnect" : "Connect"}
              >
                <span style={{
                  position: "absolute" as const, top: 3, left: isConnected ? 19 : 3,
                  width: 14, height: 14, borderRadius: "50%",
                  backgroundColor: isConnected ? "#0F172A" : t.textDim,
                  transition: "left 0.2s, background-color 0.2s",
                }} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Theme toggle button ──────────────────────────────────────────────────────
function ThemeToggle({ theme, onToggle, t }: { theme: ThemeKey; onToggle: () => void; t: Theme }) {
  const [h, setH] = useState(false);
  const isDark = theme === "dark";
  return (
    <button onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)} onClick={onToggle}
      style={{
        background: h ? (isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)") : "transparent",
        border: `1.5px solid ${h ? t.textMuted : t.border}`,
        color: h ? t.textPrimary : t.textMuted, width: 28, height: 28,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", borderRadius: 4, transition: "all 0.18s", flexShrink: 0, outline: "none",
      }}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? (
        /* sun */
        <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="2.8" stroke="currentColor" strokeWidth="1.4" />
          <path d="M7 1v1.4M7 11.6V13M1 7h1.4M11.6 7H13M2.9 2.9l1 1M10.1 10.1l1 1M2.9 11.1l1-1M10.1 3.9l1-1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      ) : (
        /* moon */
        <svg width="12" height="12" viewBox="0 0 13 13" fill="none">
          <path d="M11.5 8.5A5.5 5.5 0 0 1 4.5 1.5a5.5 5.5 0 1 0 7 7z" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </button>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────
// ── Chaos vs calm comparison ─────────────────────────────────────────────────
function ChaosCalmComparison({ t }: { t: Theme }) {
  return (
    <div style={{ padding: "16px 0 20px", borderBottom: `1.5px solid ${t.border}` }}>
      <div className="chaos-calm-grid" style={{ gap: 14 }}>

        {/* Without — chaotic, constant popups */}
        <div>
          <div style={{ color: "#E5484D", fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", marginBottom: 8 }}>
            WITHOUT CONTEXT INTERRUPTER
          </div>
          <div style={{ position: "relative", height: 220, border: "1.5px solid #E5484D33", borderRadius: 4, overflow: "hidden", background: t.bg }}>
            {CHAOS_ITEMS.map((item, i) => (
              <div key={i} className="chaos-toast" style={{ position: "absolute", top: item.top, left: item.left, animationDelay: `${item.delay}s` }}>
                <div style={{ transform: `rotate(${item.rot}deg)`, background: t.card, border: "1px solid #E5484D55", borderRadius: 4, padding: "5px 8px", fontSize: 10, whiteSpace: "nowrap", boxShadow: "0 4px 14px rgba(0,0,0,0.35)" }}>
                  <span style={{ color: "#E5484D", fontWeight: 600 }}>{item.label}</span>{" "}
                  <span style={{ color: t.textPrimary }}>{item.text}</span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ color: t.textFaint, fontSize: 10, marginTop: 6 }}>every ping breaks focus, one at a time</div>
        </div>

        {/* With — calm, held, released together */}
        <div>
          <div style={{ color: "#38BDF8", fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", marginBottom: 8 }}>
            WITH CONTEXT INTERRUPTER
          </div>
          <div style={{ position: "relative", height: 220, border: "1.5px solid #38BDF833", borderRadius: 4, overflow: "hidden", background: t.card, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#38BDF8", boxShadow: "0 0 0 4px rgba(56,189,248,0.15)" }} />
            <div style={{ color: t.textPrimary, fontSize: 12, fontWeight: 600 }}>{CHAOS_ITEMS.length} notifications held quietly</div>
            <div style={{ color: t.textFaint, fontSize: 10, textAlign: "center", padding: "0 20px" }}>released together at your next natural pause</div>
          </div>
          <div style={{ color: t.textFaint, fontSize: 10, marginTop: 6 }}>same information, delivered once, on your terms</div>
        </div>

      </div>
    </div>
  );
}

// ── Project window ────────────────────────────────────────────────────────────
function ProjectWindow({
  project, queue, onDismiss, onDefer, onOpen, onRelease, onProfile, t,
}: {
  project: Project;
  queue: Notification[];
  onDismiss: (id: string) => void;
  onDefer: (id: string) => void;
  onOpen: (notif: Notification) => void;
  onRelease: () => void;
  onProfile: (profile: string) => void;
  t: Theme;
}) {
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | Source>("all");
  const [urgencyFilter, setUrgencyFilter] = useState<"all" | Urgency>("all");
  const [releasePressed, setReleasePressed] = useState(false);
  const [toastVisible, setToastVisible] = useState(false);

  function handleRelease() {
    if (queue.length === 0) return;
    onRelease();
    setToastVisible(true);
    setTimeout(() => setToastVisible(false), 2200);
  }

  const sorted = [...queue].sort((a, b) => ({ urgent: 0, normal: 1, low: 2 }[a.urgency] - { urgent: 0, normal: 1, low: 2 }[b.urgency]));
  const filtered = sorted.filter((n) => {
    if (sourceFilter !== "all" && n.source !== sourceFilter) return false;
    if (urgencyFilter !== "all" && n.urgency !== urgencyFilter) return false;
    if (search && !n.title.toLowerCase().includes(search.toLowerCase()) && !n.subtitle.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const todayCutoff = Date.now() - 2 * 3600 * 1000;
  const todayItems = filtered.filter((n) => n.timestamp >= todayCutoff);
  const earlierItems = filtered.filter((n) => n.timestamp < todayCutoff);
  const showGroups = queue.length > 3;

  const counts = {
    urgent: queue.filter((n) => n.urgency === "urgent").length,
    normal: queue.filter((n) => n.urgency === "normal").length,
    low:    queue.filter((n) => n.urgency === "low").length,
    github: queue.filter((n) => n.source === "github").length,
    slack:  queue.filter((n) => n.source === "slack").length,
  };

  return (
    <div style={{
      display: "flex", flexDirection: "column",
      border: `1.5px solid ${t.border}`,
      borderRadius: 8, overflow: "hidden",
      backgroundColor: t.card,
      minWidth: 0,
    }}>
      {/* Window header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "10px 14px",
        borderBottom: `1.5px solid ${t.border}`,
        background: `linear-gradient(90deg, ${project.color}12 0%, transparent 100%)`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: project.color, flexShrink: 0, display: "inline-block", boxShadow: `0 0 0 3px ${project.color}22` }} />
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", color: project.color, fontFamily: MONO }}>
            {project.name}
          </span>
          <select
            value={project.urgency_profile || "shipping"}
            onChange={(e) => onProfile(e.target.value)}
            title={
              {
                shipping: "Ship mode — prod/failures jump the line; bots stay low.",
                oncall: "On-call — Slack pings and incidents are urgent.",
                review: "Review — PRs and issues are the work; chat stays quiet.",
                quiet: "Quiet — only explicit incidents.",
              }[project.urgency_profile || "shipping"] || "Priority rules for this workflow — Python, not the model"
            }
            style={{
              background: t.bg, border: `1px solid ${t.border}`, color: t.textMuted,
              fontFamily: MONO, fontSize: 10, borderRadius: 4, padding: "2px 6px",
            }}
          >
            <option value="shipping">shipping</option>
            <option value="oncall">oncall</option>
            <option value="review">review</option>
            <option value="quiet">quiet</option>
          </select>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 600, letterSpacing: "0.05em",
          color: queue.length > 0 ? project.color : t.textFaint,
          backgroundColor: queue.length > 0 ? `${project.color}18` : "transparent",
          border: `1px solid ${queue.length > 0 ? `${project.color}40` : t.border}`,
          padding: "1px 7px", borderRadius: 10,
        }}>
          {queue.length} notification{queue.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Toast */}
      {toastVisible && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          margin: "8px 10px 0",
          padding: "8px 12px",
          borderRadius: 6,
          backgroundColor: `${project.color}10`,
          border: `1.5px solid ${project.color}40`,
          animation: "slideIn 0.3s ease both",
        }}>
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ color: project.color, flexShrink: 0 }}>
            <circle cx="5" cy="5" r="4" stroke="currentColor" strokeWidth="1.3" />
            <path d="M3 5l1.5 1.5 2.5-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span style={{ color: project.color, fontSize: 11, fontWeight: 600, fontFamily: MONO }}>queue released</span>
        </div>
      )}

      {/* Filter / search */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 10px", borderBottom: `1.5px solid ${t.border}`, flexWrap: "wrap" as const }}>
        <div style={{ position: "relative" as const, flexShrink: 0 }}>
          <svg width="10" height="10" viewBox="0 0 11 11" fill="none" style={{ position: "absolute" as const, left: 7, top: "50%", transform: "translateY(-50%)", color: t.textFaint, pointerEvents: "none" }}>
            <circle cx="4.5" cy="4.5" r="3.5" stroke="currentColor" strokeWidth="1.3" />
            <line x1="7.5" y1="7.5" x2="10.5" y2="10.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          <input
            type="text" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="search…"
            style={{ background: t.bg, border: `1.5px solid ${t.border}`, borderRadius: 4, color: t.textPrimary, fontFamily: MONO, fontSize: 10, padding: "3px 8px 3px 22px", outline: "none", width: 110 }}
          />
        </div>
        <FilterChip label={`gh·${counts.github}`} active={sourceFilter === "github"} onClick={() => setSourceFilter(sourceFilter === "github" ? "all" : "github")} t={t} />
        <FilterChip label={`sl·${counts.slack}`}  active={sourceFilter === "slack"}  onClick={() => setSourceFilter(sourceFilter === "slack"  ? "all" : "slack")}  t={t} />
        <span style={{ width: 1, height: 14, backgroundColor: t.border, flexShrink: 0 }} />
        <FilterChip label="🔴" active={urgencyFilter === "urgent"} onClick={() => setUrgencyFilter(urgencyFilter === "urgent" ? "all" : "urgent")} t={t} />
        <FilterChip label="🟡" active={urgencyFilter === "normal"} onClick={() => setUrgencyFilter(urgencyFilter === "normal" ? "all" : "normal")} t={t} />
        <FilterChip label="⚪" active={urgencyFilter === "low"}    onClick={() => setUrgencyFilter(urgencyFilter === "low"    ? "all" : "low")}    t={t} />
      </div>

      {/* Queue body */}
      <div style={{ flex: 1, overflowY: "auto" as const, maxHeight: 480 }}>
        {filtered.length === 0 ? (
          <div style={{ padding: "32px 16px", textAlign: "center" as const, color: t.textFaint, fontSize: 12 }}>
            {queue.length === 0
              ? project.id === "slack"
                ? "waiting for a live Slack mention"
                : project.id === "github"
                  ? "waiting for a live GitHub event"
                  : "waiting for a matching live event"
              : "no matches"}
          </div>
        ) : showGroups ? (
          <>
            {todayItems.length > 0 && (
              <>
                <GroupDivider label="TODAY" t={t} />
                {todayItems.map((n, i) => <QueueRow key={n.id} notif={n} isLast={i === todayItems.length - 1 && earlierItems.length === 0} onDismiss={onDismiss} onDefer={onDefer} onOpen={onOpen} t={t} />)}
              </>
            )}
            {earlierItems.length > 0 && (
              <>
                <GroupDivider label="EARLIER" t={t} />
                {earlierItems.map((n, i) => <QueueRow key={n.id} notif={n} isLast={i === earlierItems.length - 1} onDismiss={onDismiss} onDefer={onDefer} onOpen={onOpen} t={t} />)}
              </>
            )}
          </>
        ) : (
          filtered.map((n, i) => <QueueRow key={n.id} notif={n} isLast={i === filtered.length - 1} onDismiss={onDismiss} onDefer={onDefer} onOpen={onOpen} t={t} />)
        )}
      </div>

      {/* Release button */}
      <div style={{ padding: "10px 12px", borderTop: `1.5px solid ${t.border}` }}>
        <button
          onMouseDown={() => setReleasePressed(true)}
          onMouseUp={() => { setReleasePressed(false); handleRelease(); }}
          onMouseLeave={() => setReleasePressed(false)}
          disabled={queue.length === 0}
          style={{
            width: "100%",
            backgroundColor: releasePressed ? `${project.color}20` : "transparent",
            border: `1.5px solid ${queue.length === 0 ? t.border : releasePressed ? project.color : t.border}`,
            color: queue.length === 0 ? t.textFaint : releasePressed ? project.color : t.textPrimary,
            fontFamily: MONO, fontSize: 11, padding: "6px 0",
            cursor: queue.length === 0 ? "not-allowed" : "pointer",
            letterSpacing: "0.04em", transition: "all 0.1s", outline: "none", borderRadius: 4,
          }}
        >
          trigger release ↵
        </button>
      </div>
    </div>
  );
}

function asQueueItem(item: LiveNotification): Notification {
  return {
    id: item.id,
    urgency: item.urgency,
    title: item.title,
    subtitle: item.subtitle,
    summary: item.summary,
    source: item.source,
    timestamp: item.timestamp,
    projectId: item.source,
    workflowId: item.workflowId,
    url: item.url,
    isNew: item.isNew,
  };
}

export default function App() {
  const [themeKey, setThemeKey] = useState<ThemeKey>("dark");
  const t = THEMES[themeKey];

  const [queue, setQueue] = useState<Notification[]>([]);
  const [stats, setStats] = useState<Stats>({
    interruptions_caught_today: 0,
    releases_today: 0,
    focus_minutes_protected_today: 0,
  });
  const [status, setStatus] = useState<Status | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [tick, setTick] = useState(0);
  const [showComparison, setShowComparison] = useState(false);
  const seen = useRef<Set<string>>(new Set());

  async function refresh() {
    try {
      const [q, s] = await Promise.all([fetchQueue(), fetchStatus()]);
      const first = seen.current.size === 0;
      const incoming = q.notifications.map((item) => {
        const mapped = asQueueItem(item);
        mapped.isNew = !first && Boolean(mapped.id) && !seen.current.has(mapped.id);
        return mapped;
      });
      seen.current = new Set(incoming.map((n) => n.id));
      setQueue(incoming);
      setStats(q.stats);
      setStatus(s);
      if (q.workflows) setWorkflows(q.workflows);
    } catch {
      /* API down — keep last snapshot */
    }
  }

  useEffect(() => {
    refresh();
    const tickId = setInterval(() => {
      setTick((n) => n + 1);
      refresh();
    }, 5000);
    return () => clearInterval(tickId);
  }, []);

  async function handleDismiss(id: string) {
    try {
      await dismissNotification(id);
    } finally {
      setQueue((q) => q.filter((n) => n.id !== id));
      refresh();
    }
  }

  async function handleDefer(id: string) {
    try {
      await deferNotification(id);
    } finally {
      setQueue((q) => q.filter((n) => n.id !== id));
      refresh();
    }
  }

  function handleOpen(notif: Notification) {
    if (notif.url) window.open(notif.url, "_blank", "noopener");
  }

  async function handleRelease(_projectId: string) {
    await releaseQueue();
    refresh();
  }

  return (
    <>
      <div className="bg-grid" style={{ backgroundImage: `radial-gradient(circle, ${t.gridDot} 1px, transparent 1px)` }} />

      <div style={{ minHeight: "100vh", backgroundColor: t.bg, padding: "24px 20px 48px", position: "relative", zIndex: 1, transition: "background-color 0.3s" }}>

        {/* ── Top bar ── */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 14, borderBottom: `1.5px solid ${t.border}`, maxWidth: 1200, margin: "0 auto 0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <LogoMark />
            <TypingTitle t={t} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <PulsingDot />
            <span style={{ color: t.textMuted, fontSize: 11 }}>
              {status?.webhook_listening ? "live" : "waiting for backend"}
              {status?.repo ? ` · ${status.repo}` : ""}
            </span>
            <button onClick={() => setShowComparison((v) => !v)} style={{ background: showComparison ? "#38BDF822" : "transparent", border: `1.5px solid ${showComparison ? "#38BDF8" : t.border}`, color: showComparison ? "#38BDF8" : t.textMuted, fontFamily: MONO, fontSize: 10, borderRadius: 4, padding: "4px 8px", cursor: "pointer" }}>
              see the difference
            </button>
            <ConnectPanel t={t} status={status} onSaved={refresh} />
            <ThemeToggle theme={themeKey} onToggle={() => setThemeKey((k) => k === "dark" ? "light" : "dark")} t={t} />
          </div>
        </div>

        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          {showComparison && <ChaosCalmComparison t={t} />}

          {/* ── Global stats strip ── */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", backgroundColor: t.card, borderLeft: `1.5px solid ${t.border}`, borderRight: `1.5px solid ${t.border}`, borderBottom: `1.5px solid ${t.border}`, transition: "background-color 0.3s" }} className="stats-strip">
            <StatCell target={Math.round(stats.focus_minutes_protected_today || 0)} liveValue={Math.round(stats.focus_minutes_protected_today || 0)} label="min protected today" accent animDelay={200} t={t} />
            <StatCell target={stats.interruptions_caught_today || 0} liveValue={stats.interruptions_caught_today || 0} label="interruptions caught" bordered animDelay={350} t={t} />
            <StatCell target={stats.releases_today || 0} liveValue={stats.releases_today || 0} label="releases today" bordered animDelay={500} t={t} />
          </div>

          {/* ── GitHub + Slack (the product) ── */}
          <div style={{ color: t.textDim, fontSize: 11, marginTop: 16, letterSpacing: "0.03em" }}>
            GitHub and Slack stay as their own queues. The dropdown only changes Python priority rules — the model still only writes the summary.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 10 }} className="project-grid">
            {withProfiles(SOURCE_PANES, workflows).map((project) => (
              <ProjectWindow
                key={project.id}
                project={project}
                queue={queue.filter((n) => n.source === project.id)}
                onDismiss={handleDismiss}
                onDefer={handleDefer}
                onOpen={handleOpen}
                onRelease={() => handleRelease(project.id)}
                onProfile={async (profile) => {
                  await patchWorkflow(project.id, { urgency_profile: profile });
                  refresh();
                }}
                t={t}
              />
            ))}
          </div>
          {extraWorkflowPanes(workflows).length > 0 ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16, marginTop: 16 }}>
              {withProfiles(extraWorkflowPanes(workflows), workflows).map((project) => (
                <ProjectWindow
                  key={project.id}
                  project={project}
                  queue={queue.filter((n) => n.workflowId === project.id)}
                  onDismiss={handleDismiss}
                  onDefer={handleDefer}
                  onOpen={handleOpen}
                  onRelease={() => handleRelease(project.id)}
                  onProfile={async (profile) => {
                    await patchWorkflow(project.id, { urgency_profile: profile });
                    refresh();
                  }}
                  t={t}
                />
              ))}
            </div>
          ) : null}

          {/* ── Footer ── */}
          <div style={{ display: "flex", justifyContent: "flex-start", alignItems: "center", marginTop: 14 }}>
            <span style={{ color: t.textFaint, fontSize: 11 }}>
              auto-refreshing every 5s
              <span style={{ margin: "0 6px", color: t.border }}>·</span>
              tick {tick}
            </span>
          </div>
        </div>
      </div>

      <style>{`
        input::placeholder { color: #3A404D; }
        input:focus { border-color: #38BDF8 !important; outline: none; }
        @media (max-width: 700px) {
          .project-grid { grid-template-columns: 1fr !important; }
          .stats-strip { grid-template-columns: 1fr !important; }
          .timestamp-col { display: none !important; }
        }
      `}</style>
    </>
  );
}
