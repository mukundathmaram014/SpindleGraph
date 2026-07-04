import { useState } from 'react'
import { api, type Proposal } from '../api'

type DiffLine = { type: 'same' | 'add' | 'del'; text: string }

// minimal LCS line diff so the reviewer sees exactly what the agent changed.
function lineDiff(a: string, b: string): DiffLine[] {
  const A = a.split('\n'), B = b.split('\n')
  const n = A.length, m = B.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: DiffLine[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push({ type: 'same', text: A[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ type: 'del', text: A[i] }); i++ }
    else { out.push({ type: 'add', text: B[j] }); j++ }
  }
  while (i < n) out.push({ type: 'del', text: A[i++] })
  while (j < m) out.push({ type: 'add', text: B[j++] })
  return out
}

export default function Reconcile({ proposals, onClose, onResolved }: {
  proposals: Proposal[]
  onClose: () => void
  onResolved: () => Promise<void>
}) {
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState('')

  const act = async (id: number, kind: 'accept' | 'reject') => {
    setBusy(id)
    setError('')
    try {
      await (kind === 'accept' ? api.acceptProposal(id) : api.rejectProposal(id))
      await onResolved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" style={{ width: 'min(780px, 94vw)' }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>Reconcile · {proposals.length} pending</h2>
          <div className="grow" />
          <button onClick={onClose}>Close</button>
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 13, margin: 0 }}>
          A build changed files these specs planned to touch, so their plans may be
          stale. Review each proposed rewrite and accept (writes the file) or reject
          (keeps the file, clears the flag).
        </p>
        {error && <div className="error-banner">{error}</div>}
        {proposals.length === 0 && <div className="empty">Nothing to reconcile.</div>}
        {proposals.map((p) => (
          <section key={p.id} className="panel pad" style={{ display: 'grid', gap: 8 }}>
            <div className="row">
              <span className="mono" style={{ color: 'var(--muted)' }}>
                #{String(p.spec_number ?? 0).padStart(4, '0')} · {p.spec_slug}
              </span>
              <span className="pill stale">stale</span>
              <div className="grow" />
              {p.trigger_number != null && (
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                  after #{String(p.trigger_number).padStart(4, '0')} {p.trigger_title}
                </span>
              )}
            </div>
            <h3 style={{ margin: 0 }}>{p.spec_title}</h3>
            {p.no_change ? (
              <div className="conflict-note" style={{ color: 'var(--muted)' }}>
                Agent reported no changes needed.
              </div>
            ) : (
              <pre className="diff">
                {lineDiff(p.current_body ?? '', p.proposed_body ?? '').map((l, i) => (
                  <div key={i} className={`diff-${l.type}`}>
                    {l.type === 'add' ? '+ ' : l.type === 'del' ? '- ' : '  '}{l.text}
                  </div>
                ))}
              </pre>
            )}
            <div className="row">
              <button className="primary" disabled={busy === p.id}
                onClick={() => act(p.id, 'accept')}>
                {p.no_change ? 'Dismiss' : 'Accept'}
              </button>
              {!p.no_change && (
                <button disabled={busy === p.id} onClick={() => act(p.id, 'reject')}>
                  Reject
                </button>
              )}
            </div>
          </section>
        ))}
      </aside>
    </>
  )
}
