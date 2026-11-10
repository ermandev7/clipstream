import type { Clip } from '../lib/api'
import { timecode } from '../lib/api'

/** Histograma de actividad con los clips detectados marcados como rangos. */
export function ActivityBar({ values, duration, offset = 0, clips, onPick }: {
  values: number[] | null | undefined
  duration: number
  offset?: number
  clips: Clip[]
  onPick?: (clip: Clip) => void
}) {
  const W = 1000
  const H = 56
  const span = Math.max(1, duration - offset)
  const n = values?.length ?? 0
  const bw = n ? W / n : 0
  return (
    <div className="activity">
      <div className="section-head" style={{ padding: '0 0 6px' }}>
        <span className="label">Actividad</span>
        <span className="dim" style={{ fontSize: 11 }}>{clips.length} clips marcados</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Actividad del vídeo">
        <rect x="0" y="0" width={W} height={H} fill="#1f1f22" />
        {clips.map((c) => {
          const x = ((c.start - offset) / span) * W
          const w = Math.max(2, ((c.end - c.start) / span) * W)
          return (
            <rect key={c.id} x={x} y={0} width={w} height={H} fill="#2a3a57" style={{ cursor: onPick ? 'pointer' : 'default' }}
              onClick={() => onPick?.(c)}>
              <title>{`${c.title} · ${timecode(c.start)}`}</title>
            </rect>
          )
        })}
        {values?.map((v, i) => {
          const h = Math.max(1, v * (H - 4))
          return <rect key={i} x={i * bw + bw * 0.15} y={H - h} width={Math.max(0.5, bw * 0.7)} height={h} fill="#6e6e74" pointerEvents="none" />
        })}
        {clips.map((c) => {
          const x = ((c.start - offset) / span) * W
          const w = Math.max(2, ((c.end - c.start) / span) * W)
          return <rect key={`l${c.id}`} x={x} y={0} width={w} height={2} fill="#4c8dff" pointerEvents="none" />
        })}
      </svg>
      <div className="activity-axis mono">
        <span>{timecode(offset)}</span>
        <span>{timecode(offset + span / 2)}</span>
        <span>{timecode(duration)}</span>
      </div>
    </div>
  )
}

/** Mini histograma para el panel de señales. */
export function MiniHist({ label, values, unit, format, na }: {
  label: string
  values: number[] | null | undefined
  unit?: string
  format?: (v: number) => string
  na?: string
}) {
  const fmt = format ?? ((v: number) => v.toFixed(1))
  if (!values || values.length === 0) {
    return (
      <div className="mini">
        <div className="mini-head"><span>{label}</span></div>
        <div className="na">{na ?? 'No disponible'}</div>
      </div>
    )
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const W = 300
  const H = 34
  const bw = W / values.length
  const last = values[values.length - 1]
  const avg = values.reduce((a, b) => a + b, 0) / values.length
  return (
    <div className="mini">
      <div className="mini-head">
        <span>{label}</span>
        <span className="mono muted" style={{ fontSize: 11 }}>
          media {fmt(avg)}{unit ? ` ${unit}` : ''} · máx {fmt(max)}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {values.map((v, i) => {
          const h = Math.max(1, ((v - min) / range) * (H - 4))
          return <rect key={i} x={i * bw} y={H - h} width={Math.max(0.6, bw - 0.4)} height={h} fill={v === last ? '#4c8dff' : '#56565c'} />
        })}
      </svg>
    </div>
  )
}
