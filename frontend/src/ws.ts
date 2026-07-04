import { useEffect, useRef } from 'react'

/** Subscribe to the project-scoped event bus. Reconnects on drop. */
export function useProjectEvents(
  projectId: number | null,
  onEvent: (e: Record<string, any>) => void,
) {
  const handler = useRef(onEvent)
  handler.current = onEvent
  useEffect(() => {
    if (projectId == null) return
    let ws: WebSocket | null = null
    let closed = false
    let timer: number | undefined
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/projects/${projectId}`)
      ws.onmessage = (ev) => {
        try { handler.current(JSON.parse(ev.data)) } catch { /* ignore */ }
      }
      ws.onclose = () => {
        if (!closed) timer = window.setTimeout(connect, 1500)
      }
    }
    connect()
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      ws?.close()
    }
  }, [projectId])
}
