/** Tiny force layout for the conflict graph (dependency-free, deterministic).
 * Conflicting specs pull together into clusters; unlinked specs repel to the
 * periphery — so "what can build in parallel" is visible as distance. */

export interface Pt { x: number; y: number }

export function forceLayout(
  ids: number[],
  links: { a: number; b: number; weight: number }[],
): Map<number, Pt> {
  const n = ids.length
  const pos = new Map<number, Pt>()
  if (n === 0) return pos
  // deterministic seed: circle by index
  const R = 90 + n * 26
  ids.forEach((id, i) => {
    const t = (2 * Math.PI * i) / n
    pos.set(id, { x: Math.cos(t) * R, y: Math.sin(t) * R * 0.72 })
  })
  const idx = new Map(ids.map((id, i) => [id, i]))
  const valid = links.filter((l) => idx.has(l.a) && idx.has(l.b))

  for (let iter = 0; iter < 320; iter++) {
    const heat = 1 - iter / 320
    const disp = ids.map(() => ({ x: 0, y: 0 }))
    // pairwise repulsion
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const p = pos.get(ids[i])!, q = pos.get(ids[j])!
        let dx = p.x - q.x, dy = (p.y - q.y) * 1.6 // extra vertical spread (wide nodes)
        let d2 = dx * dx + dy * dy
        if (d2 < 1) { dx = (i - j) * 0.1 || 0.1; dy = 0.1; d2 = 0.02 }
        const f = 90000 / d2
        const d = Math.sqrt(d2)
        disp[i].x += (dx / d) * f; disp[i].y += (dy / d) * f
        disp[j].x -= (dx / d) * f; disp[j].y -= (dy / d) * f
      }
    }
    // springs on conflict edges — heavier overlap pulls tighter; rest length
    // sits well below the unlinked repulsion equilibrium so clusters read
    for (const l of valid) {
      const i = idx.get(l.a)!, j = idx.get(l.b)!
      const p = pos.get(l.a)!, q = pos.get(l.b)!
      const dx = p.x - q.x, dy = p.y - q.y
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 0.1)
      const rest = 165 - 45 * Math.min(l.weight, 1)
      const f = (d - rest) * 0.12
      disp[i].x -= (dx / d) * f; disp[i].y -= (dy / d) * f
      disp[j].x += (dx / d) * f; disp[j].y += (dy / d) * f
    }
    // weak centering + apply capped displacement
    const cap = 22 * heat + 2
    ids.forEach((id, i) => {
      const p = pos.get(id)!
      disp[i].x -= p.x * 0.006
      disp[i].y -= p.y * 0.006
      const len = Math.max(Math.sqrt(disp[i].x ** 2 + disp[i].y ** 2), 0.001)
      const s = Math.min(len, cap) / len
      p.x += disp[i].x * s
      p.y += disp[i].y * s
    })
  }
  // normalize to positive canvas coords
  let minX = Infinity, minY = Infinity
  for (const p of pos.values()) { minX = Math.min(minX, p.x); minY = Math.min(minY, p.y) }
  for (const p of pos.values()) { p.x = Math.round(p.x - minX + 40); p.y = Math.round(p.y - minY + 40) }
  return pos
}
