import { useEffect, useMemo, useRef, useState } from 'react'
import type { SourceDetail } from '../lib/api'
import { clock } from '../lib/api'

type Filter = 'all' | 'ia' | 'warn'

interface Parsed { at?: string; score?: number; tag?: string; text: string; ia: boolean }

/** Mensajes del análisis -> partes legibles: minuto del vídeo, puntuación y motivo. */
export function parseLog(message: string): Parsed {
  let m = message.match(/^IA (\d+:\d{2}(?::\d{2})?) → (\d+)\/100 · (.*)$/s)
  if (m) return { at: m[1], score: Number(m[2]), text: m[3], ia: true }
  m = message.match(/^(Bloque [^:]+): mejor momento (\d+)\/100 · (.*)$/s)
  if (m) return { tag: m[1], score: Number(m[2]), text: m[3], ia: true }
  m = message.match(/^(Bloque [^:]+): (sin .*)$/s)
  if (m) return { tag: m[1], text: m[2], ia: true }
  m = message.match(/^Descartado (\d+) s: (\d+)\/100 ?(.*)$/s)
  if (m) {
    const s = Number(m[1])
    return { at: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`, score: Number(m[2]), tag: 'Descartado', text: m[3], ia: true }
  }
  m = message.match(/^Momento detectado \((\d+)\/100\): (.*)$/s)
  if (m) return { score: Number(m[1]), tag: 'Momento', text: m[2], ia: true }
  return { text: message, ia: false }
}

function scoreClass(s: number): string {
  return s >= 70 ? 'good' : s >= 50 ? 'mid' : 'low'
}

export function LogPanel({ detail }: { detail: SourceDetail | null }) {
  const ref = useRef<HTMLDivElement>(null)
  const stick = useRef(true) // seguir abajo mientras el usuario no suba a leer
  const [filter, setFilter] = useState<Filter>('all')
  const events = detail?.events ?? []
  const rows = useMemo(() => events.map((e) => ({ e, p: parseLog(e.message) })).filter(({ e, p }) =>
    filter === 'all' || (filter === 'ia' ? p.ia : e.level === 'warn' || e.level === 'error')), [events, filter])
  const warnCount = events.filter((e) => e.level === 'warn' || e.level === 'error').length

  useEffect(() => {
    const el = ref.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [rows.length])

  return (
    <div className="section log-section">
      <div className="section-head">
        <span className="label">Registro <span className="dim mono" style={{ fontWeight: 400 }}>{events.length}</span></span>
        <div className="log-filter" role="group" aria-label="Filtrar registro">
          {([['all', 'Todo'], ['ia', 'IA'], ['warn', `Avisos${warnCount ? ` ${warnCount}` : ''}`]] as [Filter, string][]).map(([id, label]) => (
            <button key={id} className={filter === id ? 'on' : ''} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="log" ref={ref}
        onScroll={(ev) => { const el = ev.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40 }}>
        {rows.length === 0 && <div className="dim log-empty">{events.length ? 'Nada con este filtro.' : 'Sin eventos.'}</div>}
        {rows.map(({ e, p }, i) => (
          <div key={e.id ?? `w${i}`} className={`log-row ${e.level}`}>
            <span className="t">{clock(e.ts)}</span>
            <div className="m">
              {(p.at || p.tag || p.score != null) && (
                <div className="log-meta">
                  {p.score != null && <span className={`log-score ${scoreClass(p.score)}`}>{p.score}</span>}
                  {p.at && <span className="log-at mono">{p.at}</span>}
                  {p.tag && <span className="log-tag">{p.tag}</span>}
                </div>
              )}
              <div className="log-text">{p.text}</div>
            </div>
          </div>
        ))}
      </div>
      {detail?.error && <div className="notice error" style={{ margin: '8px 14px 12px' }}>{detail.error}</div>}
    </div>
  )
}
