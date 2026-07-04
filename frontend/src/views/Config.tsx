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
          <button className="danger" onClick={async () => {
            if (!window.confirm(
              `Remove "${project.name}" from SpindleGraph?\n\nOnly SpindleGraph's` +
              ' records are deleted — the repo, its specs, and branches are untouched.')) return
            await api.deleteProject(project.id)
            await refreshProjects()
          }}>Remove project…</button>
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
              <th>Name</th><th>Backend</th><th>Model / Command</th><th>Prior P</th>
              <th>$ in/MTok</th><th>$ out/MTok</th><th>Record</th><th>Live P</th>
              <th>Avg $/build</th><th>On</th>
            </tr>
          </thead>
          <tbody>
            {executors.map((e) => (
              <ExecutorRow key={e.id} e={e} refresh={refreshExecutors} />
            ))}
          </tbody>
        </table>
        <AddExecutor refresh={refreshExecutors} />
        <p style={{ color: 'var(--muted)', fontSize: 12.5 }}>
          Live P = Beta-mean of prior + recorded build outcomes (success = checks pass +
          PR opens). Backends: <span className="mono">claude_code</span> (claude CLI),{' '}
          <span className="mono">claude_sdk</span> (Claude Agent SDK, needs{' '}
          <span className="mono">pip install claude-agent-sdk</span>), and{' '}
          <span className="mono">local_cli</span> — any local coding agent runnable as
          a command; its template runs with <span className="mono">{'{prompt}'}</span>{' '}
          substituted, exit 0 = success.
        </p>
      </section>
    </div>
  )
}

const BACKENDS = ['claude_code', 'claude_sdk', 'local_cli'] as const

function ExecutorRow({ e, refresh }: { e: Executor; refresh: () => Promise<void> }) {
  const [draft, setDraft] = useState({
    model: e.model ?? '', command_template: e.command_template ?? '',
    prior_success: e.prior_success,
    input_price_per_mtok: e.input_price_per_mtok,
    output_price_per_mtok: e.output_price_per_mtok,
  })
  useEffect(() => {
    setDraft({
      model: e.model ?? '', command_template: e.command_template ?? '',
      prior_success: e.prior_success,
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
        <select value={e.backend} onChange={(ev) => save({ backend: ev.target.value })}>
          {BACKENDS.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      </td>
      <td>
        {e.backend === 'local_cli' ? (
          <input className="wide mono" placeholder='myagent --msg "{prompt}"'
            value={draft.command_template}
            onChange={(ev) => setDraft({ ...draft, command_template: ev.target.value })}
            onBlur={() => save({ command_template: draft.command_template })} />
        ) : (
          <input className="wide mono" value={draft.model}
            onChange={(ev) => setDraft({ ...draft, model: ev.target.value })}
            onBlur={() => save({ model: draft.model || null })} />
        )}
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

function AddExecutor({ refresh }: { refresh: () => Promise<void> }) {
  const [name, setName] = useState('')
  const [backend, setBackend] = useState<string>('claude_code')
  const [template, setTemplate] = useState('')
  const [error, setError] = useState('')
  return (
    <div className="row" style={{ marginTop: 10 }}>
      <input placeholder="New executor name" value={name}
        onChange={(e) => setName(e.target.value)} />
      <select value={backend} onChange={(e) => setBackend(e.target.value)}>
        {BACKENDS.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>
      {backend === 'local_cli' && (
        <input className="grow mono" placeholder='command template, e.g. aider --yes -m "{prompt}"'
          value={template} onChange={(e) => setTemplate(e.target.value)} />
      )}
      <button disabled={!name.trim()} onClick={async () => {
        setError('')
        try {
          await api.addExecutor({
            name: name.trim(), backend,
            command_template: backend === 'local_cli' ? template : undefined,
          })
          setName(''); setTemplate('')
          await refresh()
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e))
        }
      }}>+ Add executor</button>
      {error && <span style={{ color: 'var(--bad)', fontSize: 12.5 }}>{error}</span>}
    </div>
  )
}
