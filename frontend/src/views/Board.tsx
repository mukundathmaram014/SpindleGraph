import { useState } from 'react'
import { api, type Executor, type Project, type Spec } from '../api'
import RiskChip from './RiskChip'
import SpecDrawer from './SpecDrawer'

const COLUMNS = ['draft', 'decided', 'building', 'built'] as const

export default function Board({ project, specs, executors, refresh }: {
  project: Project
  specs: Spec[]
  executors: Executor[]
  refresh: () => Promise<void>
}) {
  const [openId, setOpenId] = useState<number | null>(null)
  const open = specs.find((s) => s.id === openId) ?? null
  const visible = specs.filter((s) => s.status !== 'archived')
  const stale = visible.filter((s) => s.status === 'stale')

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 className="section" style={{ margin: 0 }}>
          Spec board · {visible.length} specs
        </h2>
        <div className="grow" />
        <button onClick={() => api.reimport(project.id).then(refresh)}>↻ Refresh from repo</button>
      </div>
      <div className="board">
        {COLUMNS.map((col) => {
          const items = visible.filter((s) =>
            col === 'draft' ? s.status === 'draft' || s.status === 'stale' : s.status === col)
          return (
            <div className="col" key={col}>
              <h3>{col}{col === 'draft' && stale.length ? ` (+${stale.length} stale)` : ''} · {items.length}</h3>
              {items.map((s) => (
                <div className="card" key={s.id} onClick={() => setOpenId(s.id)}>
                  <div className="num mono">#{String(s.number).padStart(4, '0')}
                    {' '}<span className={`pill ${s.status}`}>{s.status}</span>
                  </div>
                  <div className="title">{s.title}</div>
                  <div className="meta">
                    <span>{s.files_planned.length} files</span>
                    <RiskChip risk={s.risk} />
                    {s.decisions.some((d) => !d.resolved) && (
                      <span className="badge-warn">
                        ⚠ {s.decisions.filter((d) => !d.resolved).length} decision(s)
                      </span>
                    )}
                    {s.provenance?.pr_url && (
                      <a href={s.provenance.pr_url} target="_blank" rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}>PR ↗</a>
                    )}
                  </div>
                </div>
              ))}
              {!items.length && <div className="empty" style={{ padding: '16px 0' }}>—</div>}
            </div>
          )
        })}
      </div>
      {open && (
        <SpecDrawer spec={open} project={project} executors={executors} specs={specs}
          onClose={() => setOpenId(null)} refresh={refresh} />
      )}
    </>
  )
}
