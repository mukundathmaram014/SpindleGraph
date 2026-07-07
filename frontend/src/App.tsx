import { useCallback, useEffect, useState } from 'react'
import { api, type Executor, type Health, type Project, type Proposal, type Spec } from './api'
import { useProjectEvents } from './ws'
import Board from './views/Board'
import GraphView from './views/GraphView'
import Runner from './views/Runner'
import Config from './views/Config'
import AddProject from './views/AddProject'
import Reconcile from './views/Reconcile'

const TABS = ['Board', 'Graph', 'Runner', 'Config'] as const
type Tab = (typeof TABS)[number]

export default function App() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [projectId, setProjectId] = useState<number | null>(() => {
    const v = localStorage.getItem('sg.project')
    return v ? Number(v) : null
  })
  const [tab, setTab] = useState<Tab>('Board')
  const [specs, setSpecs] = useState<Spec[]>([])
  const [executors, setExecutors] = useState<Executor[]>([])
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [reviewing, setReviewing] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [adding, setAdding] = useState(false)
  const [graphTick, setGraphTick] = useState(0)
  const [loadError, setLoadError] = useState('')

  const project = projects?.find((p) => p.id === projectId) ?? null

  const loadProjects = useCallback(async () => {
    try {
      const ps = await api.projects()
      setProjects(ps)
      setLoadError('')
      if (ps.length && !ps.some((p) => p.id === projectId)) setProjectId(ps[0].id)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [projectId])

  const loadSpecs = useCallback(async () => {
    if (projectId != null) setSpecs(await api.specs(projectId))
  }, [projectId])

  const loadExecutors = useCallback(async () => {
    setExecutors(await api.executors())
  }, [])

  const loadProposals = useCallback(async () => {
    if (projectId != null) setProposals(await api.proposals(projectId))
  }, [projectId])

  useEffect(() => { void loadProjects() }, [loadProjects])
  useEffect(() => { void loadSpecs() }, [loadSpecs])
  useEffect(() => { void loadExecutors() }, [loadExecutors])
  useEffect(() => { void loadProposals() }, [loadProposals])
  useEffect(() => { api.health().then(setHealth).catch(() => setHealth(null)) }, [])
  useEffect(() => {
    if (projectId != null) localStorage.setItem('sg.project', String(projectId))
  }, [projectId])

  // Auto-refresh: the DB is a projection of the repo's spec files, which change
  // outside the app (merging PRs, pulling to main moves specs into
  // implemented/). Re-import on tab focus and a light interval; the backend
  // only re-scans + fires events when a spec file actually changed, so this is
  // cheap and quiet when nothing moved.
  useEffect(() => {
    if (projectId == null) return
    const sync = () => {
      if (document.visibilityState === 'visible') void api.reimport(projectId, true)
    }
    sync()
    const onVisible = () => { if (document.visibilityState === 'visible') sync() }
    window.addEventListener('focus', sync)
    document.addEventListener('visibilitychange', onVisible)
    const timer = window.setInterval(sync, 20000)
    return () => {
      window.removeEventListener('focus', sync)
      document.removeEventListener('visibilitychange', onVisible)
      clearInterval(timer)
    }
  }, [projectId])

  useProjectEvents(projectId, (e) => {
    if (e.type === 'specs.updated') { void loadSpecs(); void loadProposals() }
    if (e.type === 'proposals.updated') void loadProposals()
    if (e.type === 'graph.updated') setGraphTick((t) => t + 1)
    if (e.type === 'job.updated') void loadExecutors()
  })

  if (projects === null) {
    return (
      <div className="empty" style={{ display: 'grid', gap: 10, placeContent: 'center', height: '100%' }}>
        {loadError ? (
          <>
            <div className="error-banner">Can't reach the SpindleGraph backend: {loadError}</div>
            <button onClick={loadProjects}>Retry</button>
          </>
        ) : 'Loading…'}
      </div>
    )
  }

  if (!projects.length || adding) {
    return (
      <AddProject
        onDone={async (p) => {
          await loadProjects()
          if (p) setProjectId(p.id)
          setAdding(false)
        }}
        cancellable={projects.length > 0}
      />
    )
  }

  return (
    <div className="shell">
      <header className="appbar">
        <div className="logo">Spindle<span>Graph</span></div>
        <select
          value={projectId ?? ''}
          onChange={(e) => setProjectId(Number(e.target.value))}
          aria-label="Project"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button onClick={() => setAdding(true)}>+ Add project</button>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
        <div className="right">
          {proposals.length > 0 && (
            <button className="reconcile-badge" onClick={() => setReviewing(true)}
              title="Specs went stale after a build — review proposed updates">
              ⟳ Reconcile {proposals.length}
            </button>
          )}
          {health && !health.claude_path && (
            <span style={{ color: 'var(--bad)' }}>claude CLI not found</span>
          )}
          {health?.claude_version && <span className="mono">{health.claude_version}</span>}
        </div>
      </header>
      <main className="main">
        {project && tab === 'Board' && (
          <Board project={project} specs={specs} executors={executors} refresh={loadSpecs} />
        )}
        {project && tab === 'Graph' && (
          <GraphView project={project} executors={executors} specs={specs}
            graphTick={graphTick} refreshSpecs={loadSpecs} />
        )}
        {project && tab === 'Runner' && (
          <Runner project={project} specs={specs} executors={executors} />
        )}
        {project && tab === 'Config' && (
          <Config project={project} executors={executors}
            refreshExecutors={loadExecutors} refreshProjects={loadProjects} />
        )}
      </main>
      {reviewing && (
        <Reconcile
          proposals={proposals}
          onClose={() => setReviewing(false)}
          onResolved={async () => {
            await loadProposals()
            await loadSpecs()
            setGraphTick((t) => t + 1)
          }}
        />
      )}
    </div>
  )
}
