import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Executor, type Job, type LogEvent, type Project, type Spec } from '../api'
import { useProjectEvents } from '../ws'

export default function Runner({ project, specs, executors }: {
  project: Project
  specs: Spec[]
  executors: Executor[]
}) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [openId, setOpenId] = useState<number | null>(null)
  const [events, setEvents] = useState<LogEvent[]>([])
  const [idea, setIdea] = useState('')
  const [buildSpec, setBuildSpec] = useState<number | ''>('')
  const [error, setError] = useState('')
  const logRef = useRef<HTMLDivElement>(null)

  const loadJobs = useCallback(async () => {
    setJobs(await api.jobs(project.id))
  }, [project.id])
  useEffect(() => { void loadJobs() }, [loadJobs])

  useEffect(() => {
    if (openId == null) { setEvents([]); return }
    api.job(openId).then((j) => setEvents(j.log_events ?? []))
  }, [openId])

  useProjectEvents(project.id, (e) => {
    if (e.type === 'job.updated') {
      setJobs((js) => {
        const j = e.job as Job
        const i = js.findIndex((x) => x.id === j.id)
        if (i >= 0) { const c = [...js]; c[i] = j; return c }
        return [j, ...js]
      })
    }
    if (e.type === 'job.log' && e.job_id === openId) {
      setEvents((evs) => [...evs, e.event])
    }
  })

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [events])

  const run = async (body: Record<string, unknown>) => {
    setError('')
    try {
      const j = await api.createJob({ project_id: project.id, ...body })
      setOpenId(j.id)
      await loadJobs()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const open = jobs.find((j) => j.id === openId)
  const buildable = specs.filter((s) =>
    !['archived', 'building', 'built'].includes(s.status))

  return (
    <div className="runner">
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="pad" style={{ display: 'grid', gap: 8, borderBottom: '1px solid var(--line)' }}>
          <h2 className="section" style={{ margin: 0 }}>Run</h2>
          {error && <div className="error-banner">{error}</div>}
          <div className="row">
            <input className="grow" placeholder='New spec idea, e.g. "add rate limiting"'
              value={idea} onChange={(e) => setIdea(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && idea.trim()) { void run({ kind: 'spec', idea }); setIdea('') } }} />
            <button className="primary" disabled={!idea.trim()}
              onClick={() => { void run({ kind: 'spec', idea }); setIdea('') }}>
              /spec
            </button>
          </div>
          <div className="row">
            <select className="grow" value={buildSpec}
              onChange={(e) => setBuildSpec(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Pick a spec to build…</option>
              {buildable.map((s) => (
                <option key={s.id} value={s.id}>
                  #{String(s.number).padStart(4, '0')} {s.title}
                  {s.decisions.some((d) => !d.resolved) ? ' (❓ unresolved)' : ''}
                </option>
              ))}
            </select>
            <button disabled={buildSpec === ''}
              onClick={() => run({ kind: 'build', spec_ids: [buildSpec] })}>
              /build
            </button>
          </div>
          <div className="row">
            <button disabled={!project.notes_doc_path}
              title={project.notes_doc_path ?? 'Set a notes doc in Config first'}
              onClick={() => run({ kind: 'triage' })}>
              /triage notes doc
            </button>
            <button onClick={() => run({ kind: 'scaffold' })}
              title="Draft CLAUDE.md via an init pass">
              /init scaffold
            </button>
          </div>
        </div>
        <div className="joblist grow">
          {jobs.map((j) => (
            <div key={j.id} className={`jobrow ${j.id === openId ? 'active' : ''}`}
              onClick={() => setOpenId(j.id)}>
              <span className="mono" style={{ color: 'var(--muted)' }}>#{j.id}</span>
              <span>{j.kind}</span>
              {j.spec_ids.length > 0 && (
                <span className="mono" style={{ color: 'var(--muted)' }}>
                  {j.spec_ids.map((id) => `#${specs.find((s) => s.id === id)?.number ?? id}`).join(' ')}
                </span>
              )}
              <span className="grow" />
              {j.cost_usd != null && <span className="mono">${j.cost_usd.toFixed(2)}</span>}
              {j.limit_hit ? (
                <span className={`pill limit-${j.limit_hit}`}
                  title={j.limit_hit === 'spend_capped'
                    ? 'Hit the monthly spend cap — raise it at claude.ai/settings/usage, or switch executor'
                    : 'Hit a rolling rate limit — retry after the window resets, or switch executor'}>
                  {j.limit_hit === 'spend_capped' ? '$ spend cap' : '⏳ rate limit'}
                </span>
              ) : (
                <span className={`pill ${j.status}`}>{j.status}</span>
              )}
            </div>
          ))}
          {!jobs.length && <div className="empty">No jobs yet</div>}
        </div>
      </div>

      <div className="panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {open ? (
          <>
            <div className="pad row" style={{ borderBottom: '1px solid var(--line)' }}>
              <span className="mono">job #{open.id} · {open.kind}</span>
              <span className={`pill ${open.status}`}>{open.status}</span>
              {open.limit_hit && (
                <span className={`pill limit-${open.limit_hit}`}>
                  {open.limit_hit === 'spend_capped' ? '$ spend cap' : '⏳ rate limit'}
                </span>
              )}
              {open.branch && <span className="mono" style={{ color: 'var(--muted)' }}>{open.branch}</span>}
              {open.pr_url && <a href={open.pr_url} target="_blank" rel="noreferrer">PR ↗</a>}
              <div className="grow" />
              {executorName(executors, open.executor_id)}
              {(open.status === 'running' || open.status === 'queued') && (
                <button className="danger" onClick={() => api.cancelJob(open.id).then(loadJobs)}>
                  Cancel
                </button>
              )}
            </div>
            {open.error && <div className="error-banner" style={{ margin: 10 }}>{open.error}</div>}
            <div className="logpane grow" ref={logRef}>
              {events.map((e, i) => <EventLine key={i} e={e} />)}
              {!events.length && <div className="empty">Waiting for output…</div>}
            </div>
          </>
        ) : (
          <div className="empty" style={{ margin: 'auto' }}>Select a job to stream its log</div>
        )}
      </div>
    </div>
  )
}

function executorName(executors: Executor[], id: number | null) {
  const e = executors.find((x) => x.id === id)
  return e ? <span style={{ color: 'var(--muted)', fontSize: 12 }}>{e.name}</span> : null
}

function EventLine({ e }: { e: LogEvent }) {
  if (e.type === 'system') {
    return <div className="ev sys">◆ session started{e.model ? ` · ${e.model}` : ''}</div>
  }
  if (e.type === 'assistant') {
    const items = (e.message?.content ?? []) as { type: string; text?: string; name?: string }[]
    return (
      <>
        {items.map((c, i) =>
          c.type === 'text'
            ? <div className="ev" key={i}>{c.text}</div>
            : c.type === 'tool_use'
              ? <div className="ev tool" key={i}>→ {c.name}</div>
              : null,
        )}
      </>
    )
  }
  if (e.type === 'user') return null // tool results — collapsed
  if (e.type === 'result') {
    return (
      <div className={`ev ${e.is_error ? 'error' : 'result'}`}>
        ■ {e.is_error ? 'failed' : 'done'}: {String(e.result ?? '')}
      </div>
    )
  }
  if (e.type === 'stderr') return <div className="ev stderr">{e.text}</div>
  if (e.type === 'raw') return <div className="ev">{e.text}</div>
  return <div className="ev sys">· {e.type}</div>
}
