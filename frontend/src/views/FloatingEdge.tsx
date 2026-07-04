import { BaseEdge, EdgeLabelRenderer, useStore, type EdgeProps } from 'reactflow'

/** Undirected conflict edge: a straight line between node *centers* (drawn
 * beneath the opaque node cards), instead of the default top/bottom handle
 * connectors that wrongly suggest direction. */
export default function FloatingEdge({ id, source, target, style, data }: EdgeProps) {
  const sourceNode = useStore((s) => s.nodeInternals.get(source))
  const targetNode = useStore((s) => s.nodeInternals.get(target))
  if (!sourceNode || !targetNode) return null

  const center = (n: typeof sourceNode) => ({
    x: (n.positionAbsolute?.x ?? n.position.x) + (n.width ?? 170) / 2,
    y: (n.positionAbsolute?.y ?? n.position.y) + (n.height ?? 64) / 2,
  })
  const s = center(sourceNode)
  const t = center(targetNode)
  const path = `M ${s.x},${s.y} L ${t.x},${t.y}`
  const mx = (s.x + t.x) / 2
  const my = (s.y + t.y) / 2

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className="edgelabel"
            title={data.tooltip}
            style={{ transform: `translate(-50%, -50%) translate(${mx}px, ${my}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
