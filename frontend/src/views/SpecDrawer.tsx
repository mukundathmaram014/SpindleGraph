import { useEffect, useState } from 'react'
import { api, type Executor, type Project, type Spec } from '../api'
import RiskChip from './RiskChip'
import SpecChat from './SpecChat'

export default function SpecDrawer({ spec, project, executors, specs, onClose, refresh }: {
  spec: Spec
  project: Project
  executors: Executor[]
  specs: Spec[]
  onClose: () => void
  refresh: () => Promise<void>
}) {
  const [body, setBody] = useState(spec.body_md)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [chatId, setChatId] = useState<number | null>(null)
  useEffect(() => { setBody(spec.body_md); setEditing(false); setFeedback('') },
    [spec.id, spec.body_md])

  const refineWithAgent = async () => {
    setBusy(true); setError('')
    try {
      const chat = await api.createSpecChat({
        project_id: project.id,
        topic: `Refine the existing spec at ${spec.file_path} ("${spec.title}")`,
      })
      setChatId(chat.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const otherSpecs = specs.filter((o) => o.id !== spec.id && o.status !== 'archived')

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

  const sendFeedback = async () => {
    setBusy(true)
    setError('')
    try {
      await api.createJob({ project_id: project.id, kind: 'feedback',
                            spec_ids: [spec.id], idea: feedback.trim() })
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
          {spec.status !== 'built' && spec.status !== 'building' && (
            <button onClick={refineWithAgent} disabled={busy}
              title="Develop this spec conversationally with an agent — it asks questions and revises the file">
              💬 Refine with agent
            </button>
          )}
          <button className="primary" onClick={build}
            disabled={busy || unresolved.length > 0 || spec.status === 'building'}
            title={unresolved.length ? 'Resolve decisions first' : ''}>
            ▶ Build
          </button>
          {spec.provenance?.pr_url && (
            <a href={spec.provenance.pr_url} target="_blank" rel="noreferrer">Open PR ↗</a>
          )}
          {spec.status === 'stale' && (
            <button disabled={busy}
              title="This spec's reconcile pass failed or never ran. Clear the stale flag."
              onClick={async () => {
                setBusy(true); setError('')
                try { await api.dismissStale(spec.id); await refresh() }
                catch (e) { setError(e instanceof Error ? e.message : String(e)) }
                finally { setBusy(false) }
              }}>Dismiss stale</button>
          )}
          {spec.status === 'built' && !spec.provenance?.pr_url && spec.provenance?.branch && (
            <button disabled={busy} onClick={async () => {
              setBusy(true); setError('')
              try {
                const { pr_url } = await api.openPr(spec.id)
                window.open(pr_url, '_blank')
                await refresh()
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e))
              } finally { setBusy(false) }
            }} title={`Push ${spec.provenance.branch} and open its PR`}>
              ⇪ Open PR for branch
            </button>
          )}
        </div>

        {spec.status === 'built' && (
          <section className="feedback-box">
            <h2 className="section" style={{ margin: '0 0 6px' }}>Feedback / revise</h2>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12.5 }}>
              Report a bug or gap in what was built. An agent revises it on the
              existing branch <span className="mono">{spec.provenance?.branch}</span>,
              so the fix rides the open PR.
            </p>
            <textarea rows={3} value={feedback} disabled={busy}
              placeholder="e.g. the repeat-days selection isn't saved — reopening the edit dialog shows it reset"
              onChange={(e) => setFeedback(e.target.value)} />
            <div className="row" style={{ marginTop: 6 }}>
              <div className="grow" />
              <button className="primary" disabled={busy || !feedback.trim()}
                onClick={sendFeedback}>Send feedback → revise</button>
            </div>
          </section>
        )}

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

        <section style={{ display: 'grid', gap: 6 }}>
          <h2 className="section" style={{ margin: 0 }}>Depends on · builds after</h2>
          {otherSpecs.length === 0 ? (
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>No other specs to depend on.</span>
          ) : (
            <div className="depschooser">
              {otherSpecs.map((o) => (
                <label key={o.id} className="depsrow">
                  <input type="checkbox" checked={spec.depends_on.includes(o.id)} disabled={busy}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...spec.depends_on, o.id]
                        : spec.depends_on.filter((x) => x !== o.id)
                      void patch({ depends_on: next })
                    }} />
                  <span className="mono">#{String(o.number).padStart(4, '0')}</span>
                  <span className="grow">{o.title}</span>
                  <span className={`pill ${o.status}`}>{o.status}</span>
                </label>
              ))}
            </div>
          )}
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
      {chatId != null && (
        <SpecChat project={project} chatId={chatId}
          onClose={() => setChatId(null)} refresh={refresh} />
      )}
    </>
  )
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
