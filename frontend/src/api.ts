export interface Project {
  id: number; slug: string; name: string; repo_path: string;
  notes_doc_path: string | null; default_branch: string;
  settings?: { permission_escalation?: boolean; default_executor_id?: number };
}
export interface FileRef { path: string; rationale: string; planned_new: boolean; from_glob: string | null }
export interface Decision { text: string; resolved: boolean; answer: string }
export interface Risk {
  involvement?: string; involvement_note?: string;
  review?: string; review_note?: string;
}
export interface Spec {
  id: number; project_id: number; number: number; slug: string; title: string;
  status: string; file_path: string; body_md: string;
  files_planned: FileRef[]; files_actual: { path: string }[];
  decisions: Decision[]; risk: Risk; depends_on: number[]; executor_id: number | null;
  provenance: { branch?: string; pr_url?: string; built_at?: string };
}
export interface Executor {
  id: number; name: string; backend: string; model: string | null;
  prior_success: number; prior_strength: number; successes: number; failures: number;
  command_template: string | null;
  input_price_per_mtok: number | null; output_price_per_mtok: number | null;
  avg_build_cost_usd: number | null; enabled: number; estimated_success: number;
}
export interface Job {
  id: number; project_id: number; kind: string; spec_ids: number[];
  parent_job_id: number | null; status: string; executor_id: number | null;
  outcome: string | null; usage: Record<string, number>; cost_usd: number | null;
  command: string; worktree_path: string | null; branch: string | null;
  pr_url: string | null; exit_code: number | null; error: string | null;
  created_at: string; started_at: string | null; finished_at: string | null;
  limit_hit?: 'rate_limited' | 'spend_capped' | null;
  log_events?: LogEvent[];
}
export type LogEvent = Record<string, any>
export interface TriageCandidate {
  title: string; size: 'S' | 'M' | 'L' | null; grounding: string;
  flag: 'needs_clarification' | 'already_exists' | null;
}
export interface SpecChatMessage {
  id: number; role: 'user' | 'agent'; text: string; job_id: number | null; created_at: string;
}
export interface SpecChat {
  id: number; project_id: number; spec_id: number | null; session_id: string | null;
  topic: string; status: 'active' | 'done'; executor_id: number | null; created_at: string;
  messages: SpecChatMessage[]; turn_running: boolean;
}
export interface GraphNode {
  id: number; number: number; slug: string; title: string; status: string;
  executor_id: number | null; file_count: number; unknown_footprint: boolean;
  unresolved_decisions: number; pr_url: string | null; risk: Risk;
}
export interface GraphEdge {
  spec_a: number; spec_b: number; shared_files: string[]; weight: number; overridden: number;
}
export interface DepEdge { source: number; target: number }
export interface CheckResult {
  safe: boolean;
  conflicts: { spec_a: number; spec_b: number; shared_files: string[]; weight: number }[];
  unknown_footprint: number[];
  waves: number[][];
}
export interface Health { claude_path: string | null; claude_version: string | null; gh: boolean }
export interface Proposal {
  id: number; project_id: number; spec_id: number; trigger_spec_id: number | null;
  job_id: number | null; prior_status: string; proposed_body: string;
  no_change: number; status: string; created_at: string;
  spec_number?: number; spec_slug?: string; spec_title?: string; spec_status?: string;
  current_body?: string; trigger_number?: number; trigger_title?: string;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail ?? detail } catch { /* not json */ }
    throw new Error(detail)
  }
  return r.json()
}

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  config: () => req<Record<string, any>>('GET', '/api/config'),
  patchConfig: (c: Record<string, any>) => req('PATCH', '/api/config', c),
  projects: () => req<Project[]>('GET', '/api/projects'),
  addProject: (body: { repo_path: string; notes_doc_path?: string }) =>
    req<Project>('POST', '/api/projects', body),
  patchProject: (id: number, body: Record<string, any>) =>
    req<Project>('PATCH', `/api/projects/${id}`, body),
  reimport: (id: number, ifChanged = false) =>
    req<{ changed: boolean }>('POST', `/api/projects/${id}/import${ifChanged ? '?if_changed=true' : ''}`),
  openPr: (specId: number) => req<{ pr_url: string; note: string }>('POST', `/api/specs/${specId}/open-pr`),
  dismissStale: (specId: number) => req<Spec>('POST', `/api/specs/${specId}/dismiss-stale`),
  dismissAllStale: (pid: number) => req<{ dismissed: number }>('POST', `/api/projects/${pid}/dismiss-stale`),
  specs: (pid: number) => req<Spec[]>('GET', `/api/projects/${pid}/specs`),
  patchSpec: (id: number, body: Record<string, any>) =>
    req<Spec>('PATCH', `/api/specs/${id}`, body),
  graph: (pid: number) =>
    req<{ nodes: GraphNode[]; edges: GraphEdge[]; deps: DepEdge[]; suggested_waves: number[][] }>('GET', `/api/projects/${pid}/graph`),
  check: (pid: number, spec_ids: number[]) =>
    req<CheckResult>('POST', `/api/projects/${pid}/graph/check`, { spec_ids }),
  executors: () => req<Executor[]>('GET', '/api/executors'),
  addExecutor: (body: Record<string, any>) => req<Executor>('POST', '/api/executors', body),
  patchExecutor: (id: number, body: Record<string, any>) =>
    req<Executor>('PATCH', `/api/executors/${id}`, body),
  jobs: (pid: number) => req<Job[]>('GET', `/api/jobs?project_id=${pid}`),
  job: (id: number) => req<Job>('GET', `/api/jobs/${id}`),
  triageCandidates: (jobId: number) =>
    req<{ candidates: TriageCandidate[] }>('GET', `/api/jobs/${jobId}/triage-candidates`),
  createJob: (body: Record<string, any>) => req<Job>('POST', '/api/jobs', body),
  cancelJob: (id: number) => req('POST', `/api/jobs/${id}/cancel`),
  createSpecChat: (body: { project_id: number; topic: string; executor_id?: number }) =>
    req<SpecChat>('POST', '/api/spec-chats', body),
  specChat: (id: number) => req<SpecChat>('GET', `/api/spec-chats/${id}`),
  specChats: (pid: number) => req<SpecChat[]>('GET', `/api/projects/${pid}/spec-chats`),
  sendSpecChat: (id: number, text: string) =>
    req<SpecChat>('POST', `/api/spec-chats/${id}/messages`, { text }),
  closeSpecChat: (id: number) => req<SpecChat>('POST', `/api/spec-chats/${id}/done`),
  deleteProject: (id: number) => req('DELETE', `/api/projects/${id}`),
  proposals: (pid: number) =>
    req<Proposal[]>('GET', `/api/projects/${pid}/proposals`),
  acceptProposal: (id: number) => req<Spec>('POST', `/api/proposals/${id}/accept`),
  rejectProposal: (id: number) => req('POST', `/api/proposals/${id}/reject`),
}

export function estCost(e: Executor | undefined): number | null {
  if (!e) return null
  return e.avg_build_cost_usd
}
export function expectedCost(e: Executor | undefined): number | null {
  const c = estCost(e)
  if (c == null || !e || e.estimated_success <= 0) return null
  return c / e.estimated_success
}
export const fmt$ = (v: number | null | undefined) =>
  v == null ? '—' : `$${v.toFixed(2)}`
