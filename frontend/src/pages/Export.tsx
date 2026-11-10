import { useCallback, useEffect, useMemo, useState } from 'react'
import { ClipModal } from '../components/ClipModal'
import { PublishDialog } from '../components/PublishDialog'
import { SideResizer } from '../components/Resizers'
import { ClipStatus } from '../components/RightsBadge'
import { TikTokPanel } from '../components/TikTokPanel'
import type { Clip } from '../lib/api'
import { api, fileUrl, saveClipsToPc, shortDuration } from '../lib/api'
import { useWs } from '../lib/live'
import type { Post, TikTokStatus } from '../lib/publish'
import { POST_LABEL, publishApi } from '../lib/publish'

const KIND: Record<string, string> = { live: 'Directo', vod: 'Vídeo publicado', local: 'Archivo local' }
const INTERVALS = [15, 30, 60, 120]

export function ExportPage({ onOpenClip, focusClipId, onFocused }: { onOpenClip: (id: number) => void; focusClipId?: number | null; onFocused?: () => void }) {
  const [clips, setClips] = useState<Clip[]>([])
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [include, setInclude] = useState({ mp4: true, png: true, txt: true, srt: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<Clip | null>(null)
  const [tiktok, setTiktok] = useState<TikTokStatus | null>(null)
  const [posts, setPosts] = useState<Post[]>([])
  const [interval, setInterval_] = useState(30)
  // Cola de publicación: una ventana por clip (TikTok exige revisar y aceptar cada publicación).
  const [queue, setQueue] = useState<{ ids: number[]; i: number; next: number | null } | null>(null)
  const [showHidden, setShowHidden] = useState(false)
  // Aviso tras guardar o quitar, con su acción (abrir carpeta / deshacer).
  const [notice, setNotice] = useState<{ text: string; action?: { label: string; run: () => void } } | null>(null)

  useEffect(() => { api.clips().then(setClips).catch((e) => setError(e.message)) }, [])
  // Viene del Editor con «Exportar»: ese clip queda seleccionado (y visible aunque estuviera quitado).
  useEffect(() => {
    if (focusClipId == null || !clips.length) return
    const c = clips.find((x) => x.id === focusClipId)
    if (c) {
      setShowHidden(!!c.hidden)
      setSel(new Set([c.id]))
      window.setTimeout(() => document.getElementById(`exp-${c.id}`)?.scrollIntoView({ block: 'center' }), 50)
    }
    onFocused?.()
  }, [focusClipId, clips, onFocused])
  const loadPosts = useCallback(() => { publishApi.posts().then(setPosts).catch(() => {}) }, [])
  useEffect(() => { loadPosts() }, [loadPosts])
  useWs((m) => {
    const msg = m as unknown as { type: string; clip?: Clip; post?: Post }
    if (msg.type === 'clip' && msg.clip) {
      const c = msg.clip
      setClips((l) => (l.some((x) => x.id === c.id) ? l.map((x) => (x.id === c.id ? { ...x, ...c } : x)) : [c, ...l]))
    } else if (msg.type === 'post' && msg.post) {
      const p = msg.post
      setPosts((l) => (l.some((x) => x.id === p.id) ? l.map((x) => (x.id === p.id ? { ...x, ...p } : x)) : [p, ...l]))
    }
  })

  const hiddenCount = useMemo(() => clips.filter((c) => c.hidden).length, [clips])
  const visible = useMemo(() => clips.filter((c) => (showHidden ? c.hidden : !c.hidden)), [clips, showHidden])
  const ready = useMemo(() => visible.filter((c) => c.status === 'ready'), [visible])
  const allOn = visible.length > 0 && visible.every((c) => sel.has(c.id))
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const toggleAll = () => setSel(allOn ? new Set() : new Set(visible.map((c) => c.id)))
  const lastPost = useMemo(() => {
    const m = new Map<number, Post>()
    for (const p of posts) if (p.clip_id != null && (!m.has(p.clip_id) || m.get(p.clip_id)!.id < p.id)) m.set(p.clip_id, p)
    return m
  }, [posts])

  const download = async (ids: number[]) => {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      const r = await saveClipsToPc(ids, include)
      if (r && r !== 'browser') {
        setNotice({ text: `${r.clips === 1 ? '1 clip guardado' : `${r.clips} clips guardados`} en ${r.dest}`,
          action: { label: 'Abrir carpeta', run: () => { api.openFolder(r.dest).catch(() => {}) } } })
      }
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const setHidden = async (ids: number[], hidden: boolean) => {
    setError(null)
    try {
      await api.hideClips(ids, hidden)
      setClips((l) => l.map((c) => (ids.includes(c.id) ? { ...c, hidden: hidden ? 1 : 0 } : c)))
      setSel((s) => { const n = new Set(s); ids.forEach((id) => n.delete(id)); return n })
      setNotice({
        text: hidden ? `${ids.length === 1 ? '1 clip quitado' : `${ids.length} clips quitados`} de la lista (no se ha borrado nada)`
          : `${ids.length === 1 ? '1 clip restaurado' : `${ids.length} clips restaurados`}`,
        action: { label: 'Deshacer', run: () => { setHidden(ids, !hidden) } },
      })
    } catch (e) { setError((e as Error).message) }
  }
  const startPublish = (ids: number[]) => setQueue({ ids, i: 0, next: null })
  const advance = (scheduledAt: number | null | undefined) => {
    setQueue((q) => {
      if (!q) return null
      if (q.i + 1 >= q.ids.length) return null
      // El siguiente se propone «intervalo» minutos después del anterior (o de ahora).
      const base = scheduledAt ?? (q.next ?? Date.now() / 1000)
      return { ...q, i: q.i + 1, next: base + interval * 60 }
    })
    loadPosts()
  }
  const current = queue ? clips.find((c) => c.id === queue.ids[queue.i]) : undefined

  const selectedReady = [...sel].filter((id) => ready.some((c) => c.id === id))
  const selectedAny = [...sel].filter((id) => visible.some((c) => c.id === id))
  const desktop = api.isDesktop()
  return (
    <div className="page">
      <div className="bar">
        <strong>{showHidden ? 'Clips quitados de la lista' : 'Cola de exportación'}</strong>
        <span className="muted">{selectedAny.length} seleccionados</span>
        {(hiddenCount > 0 || showHidden) && (
          <button className="btn link" onClick={() => { setShowHidden((v) => !v); setSel(new Set()) }}>
            {showHidden ? '← Volver a la lista' : `Ver quitados (${hiddenCount})`}
          </button>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {selectedAny.length > 0 && (showHidden
            ? <button className="btn" onClick={() => setHidden(selectedAny, false)}>Restaurar ({selectedAny.length})</button>
            : <button className="btn" onClick={() => setHidden(selectedAny, true)} title="Desaparecen de esta lista; los archivos no se borran">Quitar de la lista ({selectedAny.length})</button>)}
          <button className="btn" disabled={!selectedReady.length || busy} onClick={() => download(selectedReady)}
            title={desktop ? 'Elige una carpeta del PC donde guardarlos' : 'Descarga un ZIP'}>
            {busy ? 'Guardando…' : desktop ? `Guardar en carpeta${selectedReady.length > 1 ? ` (${selectedReady.length})` : ''}` : 'Descargar ZIP'}
          </button>
          {selectedReady.length > 1 && (
            <label className="inline-field"><span className="dim">Uno cada</span>
              <select className="select" value={interval} onChange={(e) => setInterval_(Number(e.target.value))}>
                {INTERVALS.map((m) => <option key={m} value={m}>{m < 60 ? `${m} min` : `${m / 60} h`}</option>)}
              </select></label>
          )}
          <button className="btn primary" disabled={!selectedReady.length || !tiktok?.connected}
            title={!tiktok?.connected ? 'Conecta tu cuenta de TikTok en el panel derecho' : 'Revisarás cada clip antes de publicarlo'}
            onClick={() => startPublish(selectedReady)}>Publicar en TikTok{selectedReady.length > 1 ? ` (${selectedReady.length})` : ''}</button>
        </span>
      </div>
      {error && <div style={{ padding: '8px 16px 0' }}><div className="notice error">{error}</div></div>}
      {notice && (
        <div style={{ padding: '8px 16px 0' }}>
          <div className="notice ok-notice">
            <span>{notice.text}</span>
            {notice.action && <button className="btn small" onClick={() => { notice.action!.run(); if (notice.action!.label === 'Abrir carpeta') return; setNotice(null) }}>{notice.action.label}</button>}
            <button className="btn link" style={{ marginLeft: 'auto' }} onClick={() => setNotice(null)} aria-label="Cerrar aviso">Cerrar</button>
          </div>
        </div>
      )}
      <div className="workspace">
        <div className="main-col">
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th style={{ width: 32 }}><label className="check"><input type="checkbox" checked={allOn} onChange={toggleAll} aria-label="Seleccionar todos" /></label></th>
                  <th>Vídeo</th>
                  <th className="hide-sm col-opt">Idioma</th>
                  <th>TikTok</th>
                  <th>Estado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.length === 0 && <tr className="empty-row"><td colSpan={6}>{showHidden ? 'No hay clips quitados.' : 'Aún no hay clips. Analiza una fuente en la pestaña Fuentes.'}</td></tr>}
                {visible.map((c) => {
                  const p = lastPost.get(c.id)
                  return (
                    <tr key={c.id} id={`exp-${c.id}`} className={sel.has(c.id) ? 'selected' : ''}>
                      <td><label className="check"><input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} aria-label={`Seleccionar ${c.title}`} /></label></td>
                      <td>
                        <div style={{ display: 'flex', gap: 10, alignItems: 'center', cursor: 'pointer' }} onClick={() => onOpenClip(c.id)} title="Abrir en el editor">
                          {c.status === 'ready' && c.files?.thumb
                            ? <img className="thumb-v" src={fileUrl(c.id, 'thumb')} alt="" loading="lazy" />
                            : <div className="thumb-v empty">9:16</div>}
                          <div style={{ minWidth: 0 }}>
                            <div className="clip-title">{c.title}</div>
                            <div className="dim" style={{ fontSize: 12 }}>
                              Clip · {KIND[c.source_kind ?? ''] ?? ''} · <span className="mono">{shortDuration(c.end - c.start)}</span>
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="mono hide-sm col-opt">{({ es: 'ES', en: 'EN', both: 'ES+EN' } as Record<string, string>)[(c as unknown as { settings?: { sub_lang?: string } }).settings?.sub_lang ?? ''] ?? 'Original'}</td>
                      <td style={{ whiteSpace: 'nowrap', fontSize: 12 }}>
                        {p ? <span className={`post-st ${p.status}`} title={p.error ?? ''}>{POST_LABEL[p.status]}
                          {p.status === 'scheduled' && p.scheduled_at ? ` · ${new Date(p.scheduled_at * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' })}` : ''}
                          {p.status === 'uploading' ? ` ${Math.round((p.progress || 0) * 100)} %` : ''}</span>
                          : <span className="dim">—</span>}
                      </td>
                      <td><ClipStatus clip={c} /></td>
                      <td className="num row-actions">
                        <button className="btn small" disabled={c.status !== 'ready' || busy} onClick={() => download([c.id])}
                          title={desktop ? 'Guardar este clip en una carpeta del PC' : 'Descargar este clip'}>Descargar</button>
                        {!showHidden && <button className="btn small" disabled={c.status !== 'ready' || !tiktok?.connected} onClick={() => startPublish([c.id])}
                          title={!tiktok?.connected ? 'Conecta tu cuenta de TikTok' : 'Publicar este clip en TikTok'}>TikTok</button>}
                        {showHidden
                          ? <button className="btn small" onClick={() => setHidden([c.id], false)}>Restaurar</button>
                          : <button className="btn small icon-btn" onClick={() => setHidden([c.id], true)} title="Quitar de la lista (no borra el archivo)" aria-label={`Quitar ${c.title} de la lista`}>✕</button>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
        <SideResizer />
        <aside className="side">
          <TikTokPanel onStatus={setTiktok} />
          <div className="section">
            <div className="section-head"><span className="label">{desktop ? 'Qué guardar de cada clip' : 'Incluir en la descarga'}</span></div>
            <div className="section-body" style={{ display: 'grid', gap: 8 }}>
              {([['mp4', 'Vídeo MP4'], ['png', 'Portada PNG'], ['txt', 'Título, descripción, hashtags y avisos (.txt)'], ['srt', 'Subtítulos .srt']] as const).map(([k, label]) => (
                <label key={k} className="check">
                  <input type="checkbox" checked={include[k]} onChange={(e) => setInclude((s) => ({ ...s, [k]: e.target.checked }))} />{label}
                </label>
              ))}
            </div>
          </div>
          <div className="section">
            <div className="section-head"><span className="label">Formato</span></div>
            <div className="section-body kv mono" style={{ fontSize: 12 }}>
              <span style={{ fontFamily: 'var(--sans)' }}>Resolución</span><span>1080×1920</span>
              <span style={{ fontFamily: 'var(--sans)' }}>Vídeo</span><span>H.264 NVENC · 12 Mbps</span>
              <span style={{ fontFamily: 'var(--sans)' }}>Audio</span><span>AAC 192k · −14 LUFS</span>
            </div>
          </div>
          <div className="section" style={{ borderBottom: 0 }}>
            <div className="section-head"><span className="label">Otras redes</span></div>
            <div className="section-body muted" style={{ fontSize: 12 }}>YouTube Shorts, Instagram Reels y Facebook: próximamente.</div>
          </div>
        </aside>
      </div>
      {open && <ClipModal clip={open} onClose={() => setOpen(null)} />}
      {queue && current && tiktok && (
        <PublishDialog key={current.id} clip={current} status={tiktok} index={queue.i} total={queue.ids.length}
          suggestedAt={queue.next} onDone={(at) => advance(at)} onClose={() => advance(undefined)} />
      )}
    </div>
  )
}
