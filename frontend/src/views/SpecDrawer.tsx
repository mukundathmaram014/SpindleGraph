import { useEffect, useState } from 'react'
import { api, type Executor, type Project, type Spec } from '../api'
import RiskChip from './RiskChip'

export default function SpecDrawer({ spec, project, executors, onClose, refresh }: {
  spec: Spec
  project: Project
  executors: Executor[]
  onClose: () => void
  refresh: () => Promise<void>
}) {
  const [body, setBody] = useState(spec.body_md)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setBody(spec.body_md); setEditing(false) }, [spec.id, spec.body_md])

  const patch = async (payload: Record<string, unknown>) => {
    setBusy(true)
    setError('')
    try {
      await api.patchSpec(spec.id, payload)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const resolveDecision = async (text: string) => {
    const answer = window.prompt(`Answer for:\n${text}`)
    if (answer == null) return
    const updated = spec.body_md.replace(
      new RegExp(`^(\\s*[-*+]\\s+)\\[ \\](\\s+${escapeRe(text)})\\s*$`, 'm'),
      `$1[x]$2 → ${answer}`,
    )
    if (updated === spec.body_md) {
      setError('Could not locate that decision line in the markdown — edit it manually.')
      return
    }
    await patch({ body_md: updated })
  }

  const build = async () => {
    setBusy(true)
    setError('')
    try {
      await api.createJob({ project_id: project.id, kind: 'build', spec_ids: [spec.id] })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const unresolved = spec.decisions.filter((d) => !d.resolved)

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer">
        <div className="row">
          <span className="mono" style={{ color: 'var(--muted)' }}>
            #{String(spec.number).padStart(4, '0')} · {spec.file_path}
          </span>
          <span className={`pill ${spec.status}`}>{spec.status}</span>
          <RiskChip risk={spec.risk} />
          <div className="grow" />
          <button onClick={onClose}>Close</button>
        </div>
        <h2 style={{ margin: '2px 0' }}>{spec.title}</h2>
        {error && <div className="error-banner">{error}</div>}

        <div className="row">
          <label className="field">
            Executor
            <select
              value={spec.executor_id ?? ''}
              onChange={(e) => patch({ executor_id: e.target.value ? Number(e.target.value) : 0 })}
            >
              <option value="">project default</option>
              {executors.filter((x) => x.enabled).map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name} · P {x.estimated_success.toFixed(2)}
                </option>
              ))}
            </select>
          </label>
          <div className="grow" />
          <button className="primary" onClick={build}
            disabled={busy || unresolved.length > 0 || spec.status === 'building'}
            title={unresolved.length ? 'Resolve decisions first' : ''}>
            ▶ Build
          </button>
          {spec.provenance?.pr_url && (
            <a href={spec.provenance.pr_url} target="_blank" rel="noreferrer">Open PR ↗</a>
          )}
        </div>

        {spec.decisions.length > 0 && (
          <section>
            <h2 className="section">Decisions</h2>
            {spec.decisions.map((d, i) => (
              <div className="decision" key={i}>
                <span>{d.resolved ? '✅' : '⬜'}</span>
                <span className="grow">
                  {d.text}
                  {d.answer && <span style={{ color: 'var(--good)' }}> → {d.answer}</span>}
                </span>
                {!d.resolved && (
                  <button onClick={() => resolveDecision(d.text)} disabled={busy}>Resolve</button>
                )}
              </div>
            ))}
          </section>
        )}

        <section>
          <h2 className="section">
            Affected files ({spec.status === 'built' && spec.files_actual.length ? 'actual' : 'planned'})
          </h2>
          <ul className="filelist">
            {(spec.status === 'built' && spec.files_actual.length
              ? spec.files_actual
              : spec.files_planned
            ).map((f, i) => (
              <li key={i}>
                <code>{f.path}</code>
                {'rationale' in f && (f as { rationale?: string }).rationale
                  ? ` — ${(f as { rationale?: string }).rationale}` : ''}
                {'planned_new' in f && (f as { planned_new?: boolean }).planned_new ? ' (new)' : ''}
              </li>
            ))}
          </ul>
        </section>

        <section style={{ display: 'grid', gap: 8 }}>
          <div className="row">
            <h2 className="section" style={{ margin: 0 }}>Markdown</h2>
            <div className="grow" />
            {editing ? (
              <>
                <button onClick={() => { setBody(spec.body_md); setEditing(false) }}>Discard</button>
                <button className="primary" disabled={busy}
                  onClick={async () => { await patch({ body_md: body }); setEditing(false) }}>
                  Save to file
                </button>
              </>
            ) : (
              <button onClick={() => setEditing(true)}>Edit</button>
            )}
          </div>
          {editing ? (
            <textarea value={body} onChange={(e) => setBody(e.target.value)} />
          ) : (
            <pre className="mono" style={{
              whiteSpace: 'pre-wrap', background: 'var(--panel-2)',
              border: '1px solid var(--line)', borderRadius: 8, padding: 12,
              fontSize: 12.5, margin: 0,
            }}>{spec.body_md}</pre>
          )}
        </section>
      </aside>
    </>
  )
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
