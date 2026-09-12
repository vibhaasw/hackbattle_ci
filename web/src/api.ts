export type Urgency = "urgent" | "normal" | "low";
export type Source = "github" | "slack";

export interface Notification {
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

export interface Stats {
  interruptions_caught_today: number;
  releases_today: number;
  focus_minutes_protected_today: number;
}

export interface Status {
  repo: string;
  webhook_url: string;
  ngrok_url: string;
  github_token_set: boolean;
  slack_bot_set: boolean;
  slack_app_set: boolean;
  webhook_listening: boolean;
  stats: Stats;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error((body as { error?: string }).error || `HTTP ${resp.status}`);
  }
  return body as T;
}

export interface Workflow {
  id: string;
  name: string;
  color: string;
  urgency_profile: string;
  github_repos: string[];
  slack_channels: string[];
  keywords: string[];
  catch_all?: boolean;
}

export interface UrgencyProfile {
  id: string;
  blurb: string;
}

export function fetchQueue() {
  return json<{ notifications: Notification[]; stats: Stats; workflows?: Workflow[] }>("/api/queue");
}

export function fetchWorkflows() {
  return json<{ workflows: Workflow[]; profiles: UrgencyProfile[] }>("/api/workflows");
}

export function patchWorkflow(id: string, body: Partial<Workflow>) {
  return json<{ ok: boolean; workflows: Workflow[]; profiles: UrgencyProfile[] }>(
    `/api/workflows/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function putWorkflows(workflows: Workflow[]) {
  return json<{ ok: boolean; workflows: Workflow[]; profiles: UrgencyProfile[] }>("/api/workflows", {
    method: "PUT",
    body: JSON.stringify({ workflows }),
  });
}

export function fetchStatus() {
  return json<Status>("/api/status");
}

export function releaseQueue() {
  return json<{ released: boolean; held: boolean; stats: Stats }>("/api/release", {
    method: "POST",
    body: "{}",
  });
}

export function dismissNotification(id: string) {
  return json<{ ok: boolean }>(`/api/notifications/${encodeURIComponent(id)}/dismiss`, {
    method: "POST",
    body: "{}",
  });
}

export function deferNotification(id: string) {
  return json<{ ok: boolean }>(`/api/notifications/${encodeURIComponent(id)}/defer`, {
    method: "POST",
    body: "{}",
  });
}

export function primeDemo() {
  return json<{ ok: boolean; dismissed: number; stats: Stats }>("/api/prime", {
    method: "POST",
    body: "{}",
  });
}

export function saveSetup(payload: {
  github_repo?: string;
  github_token?: string;
  slack_bot_token?: string;
  slack_app_token?: string;
}) {
  return json<{
    ok: boolean;
    error?: string;
    repo: string;
    webhook_id: number | null;
    webhook_url: string;
    webhook_action: string;
    slack_workspace: string;
  }>("/api/setup", { method: "POST", body: JSON.stringify(payload) });
}
