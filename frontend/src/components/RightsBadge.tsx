import type { Clip } from '../lib/api'

/** Estado del clip: listo (verde), aviso de derechos (ámbar, nunca bloquea), renderizando (gris). */
export function ClipStatus({ clip, queuePos }: { clip: Clip; queuePos?: number }) {
  if (clip.status === 'error') {
    return <span className="status st-live" title={clip.error ?? ''}><span className="dot" />Error</span>
  }
  if (clip.status === 'rendering') {
    return <span className="status st-muted"><span className="dot" />Generando {Math.round(clip.progress * 100)} %</span>
  }
  if (clip.status === 'pending') {
    return <span className="status st-muted"><span className="dot" />{queuePos ? `En cola · ${queuePos}º` : 'En cola'}</span>
  }
  if (clip.warnings?.length) {
    const short = clip.warnings[0].kind === 'music' ? 'música' : 'permiso'
    return (
      <span className="status st-warn" title={clip.warnings.map((w) => w.message).join('\n')}>
        <span className="dot" />Aviso · {short}
      </span>
    )
  }
  return <span className="status st-ok"><span className="dot" />Listo</span>
}

export function RightsNotice({ clip }: { clip: Clip }) {
  if (!clip.warnings?.length) return null
  return (
    <div className="notice">
      <div className="label" style={{ color: 'var(--warn-text)', marginBottom: 4 }}>Aviso de derechos</div>
      {clip.warnings.map((w, i) => <div key={i}>{w.message}</div>)}
      <div className="dim" style={{ marginTop: 4 }}>El clip se genera igualmente.</div>
    </div>
  )
}
