/** Wave-lane layout: the canvas *is* the recommended build order.
 *
 * Columns left→right: ✓ Built (already landed), then Wave 1, Wave 2, …
 * (the backend's suggested ordering; riskiest first within a wave). A red
 * conflict edge reaching into a later column is the visual "why" for that
 * spec waiting. Deterministic grid — nodes can never overlap. */

export interface Pt { x: number; y: number }
export interface Lane { label: string; x: number; kind: 'built' | 'wave'; index: number }

export const LANE_W = 285
export const ROW_H = 106
export const X0 = 48
export const Y0 = 92
export const LABEL_Y = 34

export function waveLayout(
  waves: number[][],
  builtIds: number[],
): { positions: Map<number, Pt>; lanes: Lane[] } {
  const laneSets: { label: string; kind: Lane['kind']; index: number; ids: number[] }[] = []
  if (builtIds.length) {
    laneSets.push({ label: '✓ Built', kind: 'built', index: -1, ids: builtIds })
  }
  waves.forEach((w, i) => {
    if (w.length) laneSets.push({ label: `Wave ${i + 1}`, kind: 'wave', index: i, ids: w })
  })

  const positions = new Map<number, Pt>()
  const lanes: Lane[] = []
  laneSets.forEach((lane, li) => {
    const x = X0 + li * LANE_W
    lanes.push({ label: lane.label, x, kind: lane.kind, index: lane.index })
    lane.ids.forEach((id, row) => positions.set(id, { x, y: Y0 + row * ROW_H }))
  })
  return { positions, lanes }
}
