import { useEffect, useRef, useState } from 'react'
import type { ClipDetail } from '../lib/api'
import { timecode } from '../lib/api'

const FX_LABEL: Record<string, string> = { punch: 'ZOOM', shake: 'TEMBLOR', flash: 'DESTELLO', pulse: 'PULSO' }

interface Props {
  detail: ClipDetail
  hookSeconds: number
  playhead: number // tiempo absoluto de la fuente
  minDur: number
  maxDur: number
  disabled?: boolean
  onSeek: (abs: number) => void
  onTrim: (start: number, end: number) => void
}

const TRACKS = [
  { id: 'V1', label: 'Vídeo' },
  { id: 'T1', label: 'Gancho' },
  { id: 'T2', label: 'Subtítulos' },
  { id: 'A1', label: 'Voz' },
  { id: 'A2', label: 'Efectos' },
  { id: 'A3', label: 'Música' },
]

function tickStep(span: number): number {
  if (span <= 30) return 2
  if (span <= 60) return 5
  if (span <= 150) return 10
  return 30
}

export function Timeline({ detail, hookSeconds, playhead, minDur, maxDur, disabled, onSeek, onTrim }: Props) {
  const c = detail.clip
  const [draft, setDraft] = useState<{ start: number; end: number } | null>(null)
  const area = useRef<HTMLDivElement>(null)
  useEffect(() => setDraft(null), [c.start, c.end])

  const start = draft?.start ?? c.start
  const end = draft?.end ?? c.end
  const pad = Math.max(5, (c.end - c.start) * 0.3)
  const a = Math.max(detail.limits.min, Math.min(c.start, start) - pad)
  const b = Math.min(detail.limits.max, Math.max(c.end, end) + pad)
  const span = Math.max(0.1, b - a)
  const pct = (t: number) => `${((t - a) / span) * 100}%`
  const width = (t0: number, t1: number) => `${(Math.max(0, t1 - t0) / span) * 100}%`

  const timeAt = (clientX: number) => {
    const r = area.current!.getBoundingClientRect()
    return a + Math.min(1, Math.max(0, (clientX - r.left) / r.width)) * span
  }

  const startDrag = (edge: 'start' | 'end' | 'move') => (e: React.PointerEvent) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    const t0 = timeAt(e.clientX)
    const orig = { start: c.start, end: c.end }
    let cur = orig
    const move = (ev: PointerEvent) => {
      const t = Math.round(timeAt(ev.clientX) * 10) / 10
      if (edge === 'start') {
        const s = Math.min(Math.max(t, detail.limits.min, orig.end - maxDur), orig.end - minDur)
        cur = { start: s, end: orig.end }
      } else if (edge === 'end') {
        const en = Math.max(Math.min(t, detail.limits.max, orig.start + maxDur), orig.start + minDur)
        cur = { start: orig.start, end: en }
      } else {
        const dur = orig.end - orig.start
        const s = Math.min(Math.max(orig.start + (t - t0), detail.limits.min), detail.limits.max - dur)
        cur = { start: Math.round(s * 10) / 10, end: Math.round((s + dur) * 10) / 10 }
      }
      setDraft(cur)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      if (cur.start !== orig.start || cur.end !== orig.end) onTrim(cur.start, cur.end)
      else setDraft(null)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const step = tickStep(span)
  const ticks: number[] = []
  for (let t = Math.ceil(a / step) * step; t <= b; t += step) ticks.push(t)

  const rendered = c.rendered as { start?: number; end?: number }
  const rStart = rendered.start ?? c.start
  const rEnd = rendered.end ?? c.end
  const wave = detail.wave

  return (
    <div className="timeline">
      <div className="tl-head">
        <span className="label">Timeline</span>
        <span className="mono dim" style={{ fontSize: 11 }}>
          {draft ? `${timecode(start)} → ${timecode(end)} · ${(end - start).toFixed(1)} s` : 'Arrastra los bordes del vídeo para recortar · clic en la regla para ir'}
        </span>
      </div>
      <div className="tl-body">
        <div className="tl-labels">
          <div className="tl-ruler-label" />
          {TRACKS.map((t) => <div key={t.id} className="tl-label"><span className="mono">{t.id}</span> <span className="tl-name">{t.label}</span></div>)}
        </div>
        <div className="tl-area" ref={area}>
          <div className="tl-ruler" onPointerDown={(e) => onSeek(timeAt(e.clientX))}>
            {ticks.map((t) => (
              <div key={t} className="tl-tick" style={{ left: pct(t) }}><span className="mono">{timecode(t).slice(3)}</span></div>
            ))}
          </div>
          {/* V1 */}
          <div className="tl-track">
            <div className={`tl-block video ${disabled ? 'off' : ''}`} style={{ left: pct(start), width: width(start, end) }} onPointerDown={startDrag('move')}>
              <div className="tl-handle l" onPointerDown={startDrag('start')} />
              <span className="tl-block-label">{c.title}</span>
              <div className="tl-handle r" onPointerDown={startDrag('end')} />
            </div>
          </div>
          {/* T1 gancho */}
          <div className="tl-track">
            {c.hook && <div className="tl-block hook" style={{ left: pct(start), width: width(start, Math.min(end, start + hookSeconds)) }}>
              <span className="tl-block-label">{c.hook}</span></div>}
          </div>
          {/* T2 subtítulos */}
          <div className="tl-track">
            {detail.words.filter((w) => w.e > a && w.t < b).map((w, i) => (
              <div key={i} className={`tl-word ${w.t >= start && w.e <= end ? '' : 'out'}`} style={{ left: pct(w.t), width: width(w.t, w.e) }} title={w.w} />
            ))}
          </div>
          {/* A1 voz (forma de onda del último render) */}
          <div className="tl-track">
            {wave.length > 0 && (
              <svg className="tl-wave" style={{ left: pct(rStart), width: width(rStart, rEnd) }} viewBox={`0 0 ${wave.length} 20`} preserveAspectRatio="none">
                {wave.map((v, i) => <rect key={i} x={i} y={10 - v * 9} width={0.8} height={Math.max(0.4, v * 18)} fill="#56565c" />)}
              </svg>
            )}
          </div>
          {/* A2 efectos (y el zoom en el pico) del último render */}
          <div className="tl-track">
            {(detail.audio?.fx?.events ?? (detail.audio?.zoom_at != null ? [{ t: detail.audio.zoom_at, kind: 'punch' as const, reason: 'momento cumbre' }] : [])).map((e, i) => (
              <div key={`fx${i}`} className="tl-marker zoom" style={{ left: pct(rStart + e.t) }}
                title={`${FX_LABEL[e.kind] ?? e.kind}${e.reason ? ` · ${e.reason}` : ''}`}><span>{FX_LABEL[e.kind] ?? e.kind}</span></div>
            ))}
            {(detail.audio?.sfx ?? []).map((e, i) => (
              <div key={i} className={`tl-marker ${i % 2 ? "low" : ""}`} style={{ left: pct(rStart + e.t) }} title={`${e.sound} · ${e.gain_db} dB${e.reason ? ` · ${e.reason}` : ''}`}><span>{e.sound}</span></div>
            ))}
            {!detail.audio?.sfx?.length && !detail.audio?.fx?.events?.length && detail.audio?.zoom_at == null && <span className="tl-empty">Sin efectos</span>}
          </div>
          {/* A3 música */}
          <div className="tl-track">
            {detail.audio?.music
              ? <div className="tl-block music" style={{ left: pct(rStart), width: width(rStart, rEnd) }}>
                  <span className="tl-block-label">{detail.audio.music.id} · {detail.audio.music.gain_db} dB</span></div>
              : <span className="tl-empty">Sin música</span>}
          </div>
          {playhead >= a && playhead <= b && <div className="tl-playhead" style={{ left: pct(playhead) }} />}
        </div>
      </div>
    </div>
  )
}
