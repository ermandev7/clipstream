import { useEffect, useState } from 'react'
import type { CleanMode, StorageReport } from '../lib/api'
import { api, fmtBytes, getCleanMode, setCleanMode } from '../lib/api'

const KIND: Record<string, string> = { live: 'Directo', vod: 'Vídeo publicado', local: 'Archivo local' }

/** Al empezar un vídeo nuevo: borrar los anteriores (con sus clips) o conservarlos. */
export function StartCleanDialog({ report, onChoose, onCancel }: {
  report: StorageReport
  onChoose: (clean: boolean) => void
  onCancel: () => void
}) {
  const [remember, setRemember] = useState(false)
  const old = report.sources.filter((s) => !s.busy)
  const clips = old.reduce((a, s) => a + s.clips, 0)
  const bytes = old.reduce((a, s) => a + s.total_bytes, 0)
  const choose = (clean: boolean) => {
    if (remember) setCleanMode(clean ? 'clean' : 'keep')
    onChoose(clean)
  }
  return (
    <div className="modal-back" onClick={onCancel}>
      <div className="modal" style={{ width: 'min(520px, 100%)' }} role="dialog" aria-modal="true" aria-label="Empezar limpio" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><strong>¿Empezar limpio?</strong></div>
        <div className="dialog-body">
          <p>Tienes <b>{old.length === 1 ? '1 vídeo anterior' : `${old.length} vídeos anteriores`}</b> con <b>{clips} clips</b> que
            ocupan <b>{fmtBytes(bytes)}</b>.</p>
          <p className="muted">Si los borras desaparecen de ClipStream con sus clips. Los archivos de tu PC que abriste
            con «Abrir ubicación…» y los clips que ya guardaste en una carpeta <b>no se tocan</b>.</p>
          {report.sources.some((s) => s.busy) && <p className="dim">Los vídeos que aún se están procesando se conservan.</p>}
          <label className="check"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Recordar mi elección (se puede cambiar en «Espacio»)</label>
          <div className="dialog-actions">
            <button className="btn" onClick={onCancel}>Cancelar</button>
            <button className="btn" onClick={() => choose(false)}>Conservar los anteriores</button>
            <button className="btn danger-solid" onClick={() => choose(true)}>Borrar y empezar</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Cuánto ocupa cada vídeo y borrarlos (todo: original, audio y clips). */
export function StorageDialog({ currentId, onChanged, onClose }: {
  currentId: number | null
  onChanged: () => void
  onClose: () => void
}) {
  const [report, setReport] = useState<StorageReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [mode, setMode] = useState<CleanMode>(getCleanMode())
  const load = () => api.storage().then(setReport).catch((e) => setMsg((e as Error).message))
  useEffect(() => { load() }, [])

  const run = async (body: { ids?: number[]; keep?: number[] }, what: string) => {
    if (!window.confirm(`${what}\n\nSe borrarán también sus clips. Los archivos de tu PC y los clips que ya guardaste en una carpeta no se tocan. No se puede deshacer.`)) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.cleanup(body)
      setMsg(`${r.deleted === 1 ? '1 vídeo borrado' : `${r.deleted} vídeos borrados`} · ${fmtBytes(r.freed_bytes)} liberados` +
        (r.skipped ? ` · ${r.skipped} en uso, sin borrar` : ''))
      await load()
      onChanged()
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }
  const others = (report?.sources ?? []).filter((s) => s.id !== currentId && !s.busy)
  return (
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal" style={{ width: 'min(760px, 100%)' }} role="dialog" aria-modal="true" aria-label="Espacio en disco" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>Espacio en disco</strong>
          {report && <span className="mono muted">{fmtBytes(report.total_bytes)} en total</span>}
          <button className="btn small" style={{ marginLeft: 'auto' }} disabled={busy} onClick={onClose}>Cerrar</button>
        </div>
        <div className="dialog-body">
          {!report && <div className="muted">Calculando…</div>}
          {report && report.sources.length === 0 && <div className="muted">No hay vídeos guardados.</div>}
          {report && report.sources.length > 0 && (
            <div className="table-wrap storage-table">
              <table className="grid">
                <thead><tr><th>Vídeo</th><th className="num">Clips</th><th className="num">Original y audio</th><th className="num">Clips</th><th className="num">Total</th><th /></tr></thead>
                <tbody>
                  {report.sources.map((s) => (
                    <tr key={s.id} className={s.id === currentId ? 'selected' : ''}>
                      <td><div className="clip-title" title={s.title}>#{s.id} · {s.title || KIND[s.kind]}</div>
                        <div className="dim" style={{ fontSize: 12 }}>{KIND[s.kind]}{s.id === currentId ? ' · abierto ahora' : ''}{s.busy ? ' · en proceso' : ''}</div></td>
                      <td className="num mono">{s.clips}</td>
                      <td className="num mono">{fmtBytes(s.original_bytes)}</td>
                      <td className="num mono">{fmtBytes(s.clips_bytes)}</td>
                      <td className="num mono"><b>{fmtBytes(s.total_bytes)}</b></td>
                      <td className="num"><button className="btn small danger" disabled={busy || s.busy}
                        title={s.busy ? 'Se está procesando: espera a que termine' : 'Borrar este vídeo y sus clips'}
                        onClick={() => run({ ids: [s.id] }, `¿Borrar «${s.title || `#${s.id}`}» (${fmtBytes(s.total_bytes)})?`)}>Borrar</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {msg && <div className="notice ok-notice">{msg}</div>}
          <div className="dialog-actions" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <label className="inline-field"><span className="dim">Al empezar un vídeo nuevo</span>
              <select className="select" value={mode} onChange={(e) => { const m = e.target.value as CleanMode; setMode(m); setCleanMode(m) }}>
                <option value="ask">Preguntar</option>
                <option value="clean">Borrar los anteriores</option>
                <option value="keep">Conservarlos</option>
              </select></label>
            <button className="btn danger-solid" disabled={busy || others.length === 0}
              onClick={() => run({ keep: currentId != null ? [currentId] : [] },
                `¿Borrar ${others.length === 1 ? 'el vídeo anterior' : `los ${others.length} vídeos anteriores`} (${fmtBytes(others.reduce((a, s) => a + s.total_bytes, 0))})?`)}>
              Borrar todos menos el actual{others.length ? ` (${fmtBytes(others.reduce((a, s) => a + s.total_bytes, 0))})` : ''}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
