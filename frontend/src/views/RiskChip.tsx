import type { Risk } from '../api'

const cap = (s?: string) => (s ? s[0].toUpperCase() + s.slice(1) : '')
const SHORT: Record<string, string> = {
  minimal: 'Min', moderate: 'Mod', involved: 'Inv',
  low: 'Low', medium: 'Med', high: 'High',
}

/** Two-axis risk badge (Involvement · Review attention); tinted by review
 * attention. Tooltip carries the rationales. */
export default function RiskChip({ risk, compact }: { risk?: Risk; compact?: boolean }) {
  if (!risk || (!risk.involvement && !risk.review)) return null
  const title = [
    risk.involvement && `Involvement: ${cap(risk.involvement)}`
      + (risk.involvement_note ? ` — ${risk.involvement_note}` : ''),
    risk.review && `Review attention: ${cap(risk.review)}`
      + (risk.review_note ? ` — ${risk.review_note}` : ''),
  ].filter(Boolean).join('\n')
  const label = compact
    ? [SHORT[risk.involvement ?? ''], SHORT[risk.review ?? '']].filter(Boolean).join('·')
    : [cap(risk.involvement), risk.review ? `${cap(risk.review)} review` : '']
        .filter(Boolean).join(' · ')
  return (
    <span className={`riskchip ${risk.review ?? 'none'}`} title={title}>⚑ {label}</span>
  )
}
