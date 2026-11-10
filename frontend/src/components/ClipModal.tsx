import { useEffect, useState } from 'react'
import type { Clip } from '../lib/api'
import { api, fileUrl, saveClipsToPc, shortDuration, timecode } from '../lib/api'
import { ClipStatus, RightsNotice } from './RightsBadge'

const FILES: { kind: 'mp4' | 'png' | 'txt' | 'srt'; label: string }[] = [
  { kind: 'mp4', label: 'Descargar MP4' }, { kind: 'png', label: 'Portada PNG' },
  { kind: 'txt', label: 'Título .txt' }, { kind: 'srt', label: 'Subtítulos .srt' },
]

export function ClipModal({ clip, onClose }: { clip: Clip; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const ready = clip.status === 'ready'

  // En la ventana de escritorio los enlaces de descarga no funcionan: se guarda con el diálogo de carpeta.
  const save = async (kind: 'mp4' | 'png' | 'txt' | 'srt') => {
    setBusy(true)
    setError(null)
    setSaved(null)
    try {
      const r = await saveClipsToPc([clip.id], { mp4: kind === 'mp4', png: kind === 'png', txt: kind === 'txt', srt: kind === 'srt' })
      if (r && r !== 'browser') setSaved(r.dest)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={clip.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong className="monitor-title">{clip.title}</strong>
          <span className="mono muted hide-sm" style={{ fontSize: 12 }}>
            {timecode(clip.start)} → {timecode(clip.end)} · {shortDuration(clip.end - clip.start)} · 1080×1920
          </span>
          <button className="btn small" style={{ marginLeft: 'auto' }} onClick={onClose}>Cerrar</button>
        </div>
        <div className="modal-body">
          <div className="modal-video">
            {ready
              ? <video src={fileUrl(clip.id, 'mp4')} poster={fileUrl(clip.id, 'png')} controls autoPlay playsInline />
              : <div className="monitor-empty" style={{ position: 'static', height: 400, width: 225 }}><ClipStatus clip={clip} /></div>}
          </div>
          <div className="modal-info">
            <div className="kv">
              <span>Puntuación</span><span className="mono">{clip.score.toFixed(0)} <span className="dim">(señales {clip.signal_score.toFixed(0)} · IA {clip.llm_score ?? '—'})</span></span>
              <span>Señales</span><span>{clip.signals_label}</span>
              <span>Estado</span><span><ClipStatus clip={clip} /></span>
              {clip.hook && <><span>Gancho</span><span>{clip.hook}</span></>}
              {clip.reason && <><span>Motivo IA</span><span className="muted">{clip.reason}</span></>}
            </div>
            {clip.hashtags?.length > 0 && <div className="tags">{clip.hashtags.map((t) => `#${t}`).join(' ')}</div>}
            <RightsNotice clip={clip} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {FILES.map((f, i) => (
                <button key={f.kind} className={`btn ${i === 0 ? 'primary' : ''}`} disabled={!ready || busy || !clip.files?.[f.kind]}
                  onClick={() => save(f.kind)}>{f.label}</button>
              ))}
            </div>
            {saved && (
              <div className="notice ok-notice">
                <span>Guardado en {saved}</span>
                <button className="btn small" onClick={() => { api.openFolder(saved).catch(() => {}) }}>Abrir carpeta</button>
              </div>
            )}
            {error && <div className="notice error">{error}</div>}
            {clip.error && <div className="notice error">{clip.error}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
