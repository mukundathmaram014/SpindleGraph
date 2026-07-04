import { BaseEdge, useStore, type EdgeProps } from 'reactflow'

/** Directed dependency edge: an arrow from the dependent spec to the spec it
 * must build *after*. The line stops just outside the target node's box so the
 * arrowhead stays visible instead of hiding under the opaque card. */
export default function DependencyEdge({ id, source, target, style, markerEnd }: EdgeProps) {
  const sourceNode = useStore((s) => s.nodeInternals.get(source))
  const targetNode = useStore((s) => s.nodeInternals.get(target))
  if (!sourceNode || !targetNode) return null

  const box = (n: typeof sourceNode) => {
    const w = n.width ?? 170, h = n.height ?? 64
    return {
      x: (n.positionAbsolute?.x ?? n.position.x) + w / 2,
      y: (n.positionAbsolute?.y ?? n.position.y) + h / 2,
      w, h,
    }
  }
  const s = box(sourceNode)
  const t = box(targetNode)

  // clip the source→target segment to the target's (slightly padded) rectangle
  const dx = s.x - t.x, dy = s.y - t.y
  const hw = t.w / 2 + 7, hh = t.h / 2 + 7
  const scale = Math.min(
    dx === 0 ? Infinity : hw / Math.abs(dx),
    dy === 0 ? Infinity : hh / Math.abs(dy),
  )
  const tx = t.x + dx * (Number.isFinite(scale) ? scale : 0)
  const ty = t.y + dy * (Number.isFinite(scale) ? scale : 0)

  return (
    <BaseEdge id={id} path={`M ${s.x},${s.y} L ${tx},${ty}`} style={style} markerEnd={markerEnd} />
  )
}
