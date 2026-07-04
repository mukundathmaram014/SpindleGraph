import { useState } from 'react'
import { api, type Project } from '../api'

export default function AddProject({ onDone, cancellable }: {
  onDone: (p: Project | null) => void
  cancellable: boolean
}) {
  const [repoPath, setRepoPath] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const p = await api.addProject({
        repo_path: repoPath.trim(),
        notes_doc_path: notes.trim() || undefined,
      })
      onDone(p)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
      <div className="panel pad" style={{ width: 520, display: 'grid', gap: 12 }}>
        <div className="logo" style={{ fontWeight: 700, fontSize: 18 }}>
          Spindle<span style={{ color: 'var(--accent)' }}>Graph</span>
        </div>
        <p style={{ margin: 0, color: 'var(--muted)' }}>
          Point at a local git repository. A <span className="mono">specs/</span> directory
          is created if missing, and the workflow commands are copied into{' '}
          <span className="mono">.claude/commands/</span>.
        </p>
        {error && <div className="error-banner">{error}</div>}
        <label className="field">
          Repository path (absolute)
          <input value={repoPath} onChange={(e) => setRepoPath(e.target.value)}
            placeholder="C:\Users\me\Projects\my-app" autoFocus />
        </label>
        <label className="field">
          Notes / ideas document for /triage (optional)
          <input value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="C:\Users\me\notes\ideas.md" />
        </label>
        <div className="row">
          <button className="primary" onClick={submit} disabled={busy || !repoPath.trim()}>
            {busy ? 'Adding…' : 'Add project'}
          </button>
          {cancellable && <button onClick={() => onDone(null)}>Cancel</button>}
        </div>
      </div>
    </div>
  )
}
