import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactFlow, { Background, Controls, type Edge, type Node } from 'reactflow'
import {
  api, expectedCost, fmt$, type CheckResult, type Executor, type GraphEdge,
  type GraphNode, type Project, type Spec,
} from '../api'

export default function GraphView({ project, executors, specs, graphTick, refreshSpecs }: {
  project: Project
  executors: Executor[]
  specs: Spec[]
  graphTick: number
  refreshSpecs: () => Promise<void>
}) {
  const [gnodes, setGnodes] = useState<GraphNode[]>([])
  const [gedges, setGedges] = useState<GraphEdge[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [error, setError] = useState('')
  const [launching, setLaunching] = useState(false)

  useEffect(() => {
    api.graph(project.id).then((g) => {
      setGnodes(g.nodes)
      setGedges(g.edges)
      setSelected((sel) => sel.filter((id) => g.nodes.some((n) => n.id === id)))
    }).catch((e) => setError(String(e)))
  }, [project.id, graphTick])

  useEffect(() => {
    if (selected.length < 1) { setCheck(null); return }
    let stale = false
    api.check(project.id, selected).then((c) => { if (!stale) setCheck(c) })
    return () => { stale = true }
  }, [project.id, selected, graphTick])

  const execOf = useCallback((executorId: number | null) =>
    executors.find((e) => e.id === executorId), [executors])

  const nodes: Node[] = useMemo(() => gnodes.map((n, i) => {
    const ex = execOf(n.executor_id)
    const isSel = selected.includes(n.id)
    const cols = Math.max(2, Math.ceil(Math.sqrt(gnodes.length)))
    return {
      id: String(n.id),
      position: { x: (i % cols) * 230 + (Math.floor(i / cols) % 2) * 60, y: Math.floor(i / cols) * 130 },
      data: {
        label: (
          <div className={`sgnode ${isSel ? 'sel' : ''} ${n.status}`}>
            <div className="t">#{String(n.number).padStart(4, '0')} {n.slug}</div>
            <div className="sub">
              <span className={`pill ${n.status}`}>{n.status}</span>{' '}
              {n.unknown_footprint ? '⚠ unknown footprint' : `${n.file_count} files`}
              {n.unresolved_decisions > 0 && ` · ${n.unresolved_decisions}❓`}
            </div>
            <div className="sub">
              {ex ? `${ex.name} · P ${ex.estimated_success.toFixed(2)} · ${fmt$(ex.avg_build_cost_usd)}` : 'default executor'}
            </div>
          </div>
        ),
      },
      style: { background: 'transparent', border: 'none', padding: 0, width: 'auto' },
    }
  }), [gnodes, selected, execOf])

  const edges: Edge[] = useMemo(() => gedges.map((e) => ({
    id: `${e.spec_a}-${e.spec_b}`,
    source: String(e.spec_a),
    target: String(e.spec_b),
    label: `${e.shared_files.length} shared · w ${e.weight.toFixed(2)}${e.overridden ? ' (pinned)' : ''}`,
    style: { stroke: 'var(--bad)', strokeWidth: 1.5 + e.weight * 4 },
    labelStyle: { fill: 'var(--muted)', fontSize: 10 },
    labelBgStyle: { fill: 'var(--panel)' },
    type: 'straight',
  })), [gedges])

  const toggle = useCallback((id: number) => {
    setSelected((sel) => sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id])
  }, [])

  const launch = async () => {
    if (!check) return
    setLaunching(true)
    setError('')
    try {
      await api.createJob({
        project_id: project.id, kind: 'build_batch',
        spec_ids: selected, waves: check.waves,
      })
      setSelected([])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLaunching(false)
    }
  }

  const specById = (id: number) => specs.find((s) => s.id === id)
  const byId = (id: number) => gnodes.find((n) => n.id === id)
  const blockers = selected
    .map((id) => specById(id))
    .filter((s): s is Spec => !!s && s.decisions.some((d) => !d.resolved))

  return (
    <div className="graphwrap">
      <div className="panel" style={{ overflow: 'hidden' }}>
        {gnodes.length === 0 ? (
          <div className="empty">No specs yet — run /spec from the Runner tab, or add
            <span className="mono"> specs/NNNN-slug.md</span> files and refresh.</div>
        ) : (
          <ReactFlow
            nodes={nodes} edges={edges} fitView
            onNodeClick={(_, node) => toggle(Number(node.id))}
            nodesConnectable={false} proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        )}
      </div>

      <div className="panel pad composer">
        <h2 className="section">Batch composer</h2>
        {error && <div className="error-banner">{error}</div>}
        {selected.length === 0 && (
          <p style={{ color: 'var(--muted)' }}>
            Click nodes to select specs. Conflicting picks are flagged and split into waves.
          </p>
        )}
        {check && (
          <>
            {check.conflicts.map((c, i) => (
              <div className="conflict-note" key={i}>
                ⚡ #{byId(c.spec_a)?.number} × #{byId(c.spec_b)?.number} share{' '}
                {c.shared_files.slice(0, 2).join(', ')}
                {c.shared_files.length > 2 ? ` +${c.shared_files.length - 2}` : ''}
              </div>
            ))}
            {check.unknown_footprint.length > 0 && (
              <div className="conflict-note">
                ⚠ unknown footprint (no parsed files): {check.unknown_footprint
                  .map((id) => `#${byId(id)?.number}`).join(', ')} — treated as
                conflicting with everything
              </div>
            )}
            {blockers.length > 0 && (
              <div className="conflict-note">
                ❓ unresolved decisions block: {blockers.map((s) => `#${s.number}`).join(', ')}
              </div>
            )}
            <table>
              <thead>
                <tr><th>Spec</th><th>Executor</th><th>P</th><th>est $</th><th>E[$]</th></tr>
              </thead>
              <tbody>
                {check.waves.map((wave, wi) => (
                  <WaveRows key={wi} wave={wave} wi={wi} byId={byId}
                    specById={specById} executors={executors}
                    refreshSpecs={refreshSpecs} />
                ))}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" disabled={launching || blockers.length > 0}
                onClick={launch}>
                Launch batch · {check.waves.length} wave{check.waves.length > 1 ? 's' : ''}
              </button>
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                {selected.length} spec(s) · one worktree + PR each
              </span>
            </div>
            {check.waves.length > 1 && (
              <p style={{ color: 'var(--muted)', fontSize: 12 }}>
                Waves run sequentially; all builds branch from{' '}
                <span className="mono">{project.default_branch}</span>. Merge earlier
                waves' PRs before later waves for conflict-free merges.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function WaveRows({ wave, wi, byId, specById, executors, refreshSpecs }: {
  wave: number[]
  wi: number
  byId: (id: number) => { number: number; slug: string } | undefined
  specById: (id: number) => Spec | undefined
  executors: Executor[]
  refreshSpecs: () => Promise<void>
}) {
  return (
    <>
      <tr><td colSpan={5} className="wavehdr">Wave {wi + 1}</td></tr>
      {wave.map((id) => {
        const n = byId(id)
        const s = specById(id)
        const ex = executors.find((e) => e.id === s?.executor_id)
        return (
          <tr key={id}>
            <td className="mono">#{n ? String(n.number).padStart(4, '0') : id}</td>
            <td>
              <select value={s?.executor_id ?? ''} style={{ maxWidth: 130 }}
                onChange={async (e) => {
                  if (!s) return
                  await api.patchSpec(s.id, {
                    executor_id: e.target.value ? Number(e.target.value) : 0,
                  })
                  await refreshSpecs()
                }}>
                <option value="">default</option>
                {executors.filter((x) => x.enabled).map((x) => (
                  <option key={x.id} value={x.id}>{x.name}</option>
                ))}
              </select>
            </td>
            <td className="num mono">{ex ? ex.estimated_success.toFixed(2) : '—'}</td>
            <td className="num mono">{fmt$(ex?.avg_build_cost_usd)}</td>
            <td className="num mono">{fmt$(expectedCost(ex))}</td>
          </tr>
        )
      })}
    </>
  )
}
