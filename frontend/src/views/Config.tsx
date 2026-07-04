import { useEffect, useState } from 'react'
import { api, fmt$, type Executor, type Project } from '../api'

export default function Config({ project, executors, refreshExecutors, refreshProjects }: {
  project: Project
  executors: Executor[]
  refreshExecutors: () => Promise<void>
  refreshProjects: () => Promise<void>
}) {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null)
  const [notes, setNotes] = useState(project.notes_doc_path ?? '')
  const [branch, setBranch] = useState(project.default_branch)
  const [escalate, setEscalate] = useState(!!project.settings?.permission_escalation)
  const [saved, setSaved] = useState('')

  useEffect(() => { api.config().then(setCfg) }, [])
  useEffect(() => {
    setNotes(project.notes_doc_path ?? '')
    setBranch(project.default_branch)
    setEscalate(!!project.settings?.permission_escalation)
  }, [project.id, project.notes_doc_path, project.default_branch, project.settings])

  const flash = (msg: string) => { setSaved(msg); setTimeout(() => setSaved(''), 2000) }

  return (
    <div className="config-grid">
      {saved && <div className="panel pad" style={{ color: 'var(--good)' }}>{saved}</div>}

      <section className="panel pad">
        <h2 className="section">Global</h2>
        {cfg && (
          <div className="row">
            <label className="field">claude binary
              <input value={cfg.claude_bin}
                onChange={(e) => setCfg({ ...cfg, claude_bin: e.target.value })} />
            </label>
            <label className="field">max parallel builds
              <input type="number" min={1} max={8} value={cfg.max_parallel}
                onChange={(e) => setCfg({ ...cfg, max_parallel: Number(e.target.value) })} />
            </label>
            <label className="field">job timeout (min)
              <input type="number" min={1} value={cfg.job_timeout_min}
                onChange={(e) => setCfg({ ...cfg, job_timeout_min: Number(e.target.value) })} />
            </label>
            <button className="primary" style={{ alignSelf: 'end' }}
              onClick={async () => { await api.patchConfig(cfg); flash('Global config saved') }}>
              Save
            </button>
          </div>
        )}
      </section>

      <section className="panel pad">
        <h2 className="section">Project · {project.name}</h2>
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="mono" style={{ color: 'var(--muted)' }}>{project.repo_path}</span>
        </div>
        <div className="row">
          <label className="field grow">notes doc (for /triage)
            <input value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <label className="field">default branch
            <input value={branch} onChange={(e) => setBranch(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={escalate}
              onChange={(e) => setEscalate(e.target.checked)} />
            <span>
              Run agents with <span className="mono">--dangerously-skip-permissions</span>
              <span style={{ color: 'var(--bad)' }}> — off by default; only for fully
              isolated worktree builds you trust</span>
            </span>
          </label>
          <div className="grow" />
          <button className="primary" onClick={async () => {
            await api.patchProject(project.id, {
              notes_doc_path: notes, default_branch: branch,
              settings: { permission_escalation: escalate },
            })
            await refreshProjects()
            flash('Project settings saved')
          }}>Save</button>
        </div>
      </section>

      <section className="panel pad" style={{ overflowX: 'auto' }}>
        <h2 className="section">Executor roster</h2>
        <table className="executors">
          <thead>
            <tr>
              <th>Name</th><th>Model</th><th>Prior P</th><th>$ in/MTok</th>
              <th>$ out/MTok</th><th>Record</th><th>Live P</th><th>Avg $/build</th><th>On</th>
            </tr>
          </thead>
          <tbody>
            {executors.map((e) => (
              <ExecutorRow key={e.id} e={e} refresh={refreshExecutors} />
            ))}
          </tbody>
        </table>
        <p style={{ color: 'var(--muted)', fontSize: 12.5 }}>
          Live P = Beta-mean of prior + recorded build outcomes (success = checks pass +
          PR opens). Prices are editable — they change. v0 backend is Claude Code only;
          Codex/local executors arrive with the pluggable backend interface.
        </p>
      </section>
    </div>
  )
}

function ExecutorRow({ e, refresh }: { e: Executor; refresh: () => Promise<void> }) {
  const [draft, setDraft] = useState({
    model: e.model ?? '', prior_success: e.prior_success,
    input_price_per_mtok: e.input_price_per_mtok,
    output_price_per_mtok: e.output_price_per_mtok,
  })
  useEffect(() => {
    setDraft({
      model: e.model ?? '', prior_success: e.prior_success,
      input_price_per_mtok: e.input_price_per_mtok,
      output_price_per_mtok: e.output_price_per_mtok,
    })
  }, [e])

  const save = async (patch: Record<string, unknown>) => {
    await api.patchExecutor(e.id, patch)
    await refresh()
  }

  return (
    <tr style={{ opacity: e.enabled ? 1 : 0.5 }}>
      <td>{e.name}</td>
      <td>
        <input className="wide mono" value={draft.model}
          onChange={(ev) => setDraft({ ...draft, model: ev.target.value })}
          onBlur={() => save({ model: draft.model || null })} />
      </td>
      <td>
        <input type="number" step={0.05} min={0} max={1} value={draft.prior_success}
          onChange={(ev) => setDraft({ ...draft, prior_success: Number(ev.target.value) })}
          onBlur={() => save({ prior_success: draft.prior_success })} />
      </td>
      <td>
        <input type="number" step={0.5} value={draft.input_price_per_mtok ?? ''}
          onChange={(ev) => setDraft({ ...draft, input_price_per_mtok: ev.target.value === '' ? null : Number(ev.target.value) })}
          onBlur={() => save({ input_price_per_mtok: draft.input_price_per_mtok })} />
      </td>
      <td>
        <input type="number" step={0.5} value={draft.output_price_per_mtok ?? ''}
          onChange={(ev) => setDraft({ ...draft, output_price_per_mtok: ev.target.value === '' ? null : Number(ev.target.value) })}
          onBlur={() => save({ output_price_per_mtok: draft.output_price_per_mtok })} />
      </td>
      <td className="mono">{e.successes}W / {e.failures}L</td>
      <td className="mono">{e.estimated_success.toFixed(2)}</td>
      <td className="mono">{fmt$(e.avg_build_cost_usd)}</td>
      <td>
        <input type="checkbox" checked={!!e.enabled}
          onChange={(ev) => save({ enabled: ev.target.checked })} />
      </td>
    </tr>
  )
}
