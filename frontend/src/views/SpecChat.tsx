import { useEffect, useRef, useState } from 'react'
import { api, type Project, type SpecChat as Chat } from '../api'
import { useProjectEvents } from '../ws'

// A turn-by-turn conversation that develops one spec. Each reply resumes the
// same claude session server-side, so context carries without re-sending it.
export default function SpecChat({ project, chatId, onClose, refresh }: {
  project: Project
  chatId: number
  onClose: () => void
  refresh: () => Promise<void>
}) {
  const [chat, setChat] = useState<Chat | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const load = () => api.specChat(chatId).then(setChat).catch(() => {})
  useEffect(() => { void load() }, [chatId])

  // the turn completes server-side; refetch when it announces itself
  useProjectEvents(project.id, (e) => {
    if (e.type === 'spec_chat.updated' && e.chat_id === chatId) void loadAndRefresh()
  })
  const loadAndRefresh = async () => { await load(); await refresh() }

  // fallback poll while a turn is in flight (in case the socket is asleep)
  useEffect(() => {
    if (!chat?.turn_running) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [chat?.turn_running])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [chat?.messages.length, chat?.turn_running])

  const send = async () => {
    const text = input.trim()
    if (!text || !chat) return
    setError(''); setInput('')
    try {
      setChat(await api.sendSpecChat(chat.id, text))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setInput(text)
    }
  }

  const markDone = async () => {
    if (!chat) return
    try { setChat(await api.closeSpecChat(chat.id)); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }

  const running = !!chat?.turn_running
  const closed = chat?.status === 'done'

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer chat-drawer">
        <div className="row">
          <span className="mono" style={{ color: 'var(--muted)' }}>spec chat</span>
          {chat?.spec_id != null && <span className="pill decided">spec linked</span>}
          {closed && <span className="pill built">done</span>}
          <div className="grow" />
          {!closed && chat && (
            <button onClick={markDone} title="Close this conversation">Done</button>
          )}
          <button onClick={onClose}>Close</button>
        </div>
        <h2 style={{ margin: '2px 0 8px' }}>{chat?.topic ?? '…'}</h2>
        {error && <div className="error-banner">{error}</div>}

        <div className="chatlog" ref={scrollRef}>
          {chat?.messages.map((m) => (
            <div key={m.id} className={`chatmsg ${m.role}`}>
              <div className="chatrole">{m.role === 'user' ? 'you' : 'agent'}</div>
              <div className="chatbubble">{m.text}</div>
            </div>
          ))}
          {running && (
            <div className="chatmsg agent">
              <div className="chatrole">agent</div>
              <div className="chatbubble thinking">thinking…</div>
            </div>
          )}
          {!chat && <div className="empty">Loading…</div>}
        </div>

        {closed ? (
          <div className="empty" style={{ padding: '10px 0' }}>
            This conversation is closed.
            {chat?.spec_id != null && ' The spec is on the board.'}
          </div>
        ) : (
          <div className="chatinput">
            <textarea rows={2} value={input} disabled={running}
              placeholder={running ? 'Agent is working…' : 'Reply — answer a question, push back, or say "looks good"'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
              }} />
            <button className="primary" disabled={running || !input.trim()} onClick={send}>
              Send
            </button>
          </div>
        )}
      </aside>
    </>
  )
}
