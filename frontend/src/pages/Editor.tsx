import { useCallback, useEffect, useRef, useState } from 'react'
import { MusicImport } from '../components/MusicImport'
import { RightsNotice, ClipStatus } from '../components/RightsBadge'
import { Segmented } from '../components/Segmented'
import { Timeline } from '../components/Timeline'
import type { ClipDetail, ClipPatchBody, EditorOptions, Layout } from '../lib/api'
import { api, editorApi, fileUrl, saveClipsToPc, shortDuration, timecode } from '../lib/api'
import { useWs } from '../lib/live'
import { SideResizer } from '../components/Resizers'
import type { FxMode, SfxMode } from '../lib/api'

const LAYOUT_SHORT: Record<string, string> = {
  follow: 'Seguir cara', facecam: 'Facecam + juego', blur: 'Desenfocado',
  split2: 'Dividido ×2', split3: 'Dividido ×3', speaker: 'Quien habla', react: 'Streamer + vídeo',
}

const PLATFORM: Record<string, string> = { tiktok: 'TikTok', instagram: 'Reels', facebook: 'Facebook', youtube: 'Shorts' }

interface Props { clipId: number | null; onNavigate: (tab: 'sources' | 'crop' | 'export', clipId?: number) => void }

export function EditorPage({ clipId, onNavigate }: Props) {
  const [detail, setDetail] = useState<ClipDetail | null>(null)
  const [options, setOptions] = useState<EditorOptions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [undo, setUndo] = useState<ClipPatchBody[]>([])
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [importing, setImporting] = useState(false)
  const [regenMeta, setRegenMeta] = useState(false)
  const [coverBusy, setCoverBusy] = useState(false)
  const [saveBusy, setSaveBusy] = useState(false)
  const [saved, setSaved] = useState<{ text: string; dest: string } | null>(null)
  const video = useRef<HTMLVideoElement>(null)

  const load = useCallback(async (id: number) => {
    try {
      setDetail(await editorApi.detail(id))
      setError(null)
    } catch (e) { setError((e as Error).message) }
  }, [])

  useEffect(() => { editorApi.options().then(setOptions).catch(() => {}) }, [])
  useEffect(() => {
    setUndo([])
    setDetail(null)
    if (clipId != null) load(clipId)
  }, [clipId, load])

  useWs((m) => {
    if (m.type !== 'clip' || !m.clip || m.clip.id !== clipId) return
    setDetail((d) => {
      if (!d) return d
      const wasBusy = d.clip.status === 'rendering' || d.clip.status === 'pending'
      if (wasBusy && m.clip.status === 'ready') load(m.clip.id) // vídeo, onda y estado nuevos
      setRegenMeta(false)
      return { ...d, clip: { ...d.clip, ...m.clip } as ClipDetail['clip'] }
    })
  })

  const apply = useCallback(async (patch: ClipPatchBody, reverse: ClipPatchBody) => {
    if (!detail) return
    setSaving(true)
    setError(null)
    try {
      const d = await editorApi.patch(detail.clip.id, patch)
      setDetail(d)
      setUndo((u) => [...u.slice(-49), reverse])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [detail])

  const doUndo = async () => {
    if (!detail || !undo.length) return
    const last = undo[undo.length - 1]
    setSaving(true)
    try {
      setDetail(await editorApi.patch(detail.clip.id, last))
      setUndo((u) => u.slice(0, -1))
    } catch (e) { setError((e as Error).message) } finally { setSaving(false) }
  }

  const renderNow = async () => {
    if (!detail) return
    setError(null)
    try { await editorApi.render(detail.clip.id); load(detail.clip.id) } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault()
        doUndo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (clipId == null) {
    return (
      <div className="page"><div className="placeholder"><div className="box">
        <div className="label" style={{ marginBottom: 8 }}>Editor de clip</div>
        <div className="muted">Abre un clip desde la tabla de Fuentes o desde Exportar.</div>
        <button className="btn" style={{ marginTop: 14 }} onClick={() => onNavigate('sources')}>Ir a Fuentes</button>
      </div></div></div>
    )
  }
  if (!detail) {
    return <div className="page"><div className="placeholder">{error ? <div className="notice error">{error}</div> : <span className="muted">Cargando clip…</span>}</div></div>
  }

  const c = detail.clip
  const busy = c.status === 'pending' || c.status === 'rendering'
  const rendered = c.rendered as { start?: number; end?: number; at?: number }
  const rStart = rendered.start ?? c.start
  const crop = detail.crop
  const activeLayout = crop.layout || null
  const shownLayout = activeLayout ?? (c.layout || 'blur')
  const hookSeconds = c.settings?.hook_seconds ?? 2
  const sfxMode: SfxMode = c.settings?.sfx ?? 'auto'
  const fxMode: FxMode = c.settings?.fx ?? (c.settings?.zoom === false ? 'none' : 'dynamic')
  const musicId = c.settings?.music ?? ''
  const musicGain = c.settings?.music_gain ?? -20
  const meta = c.meta ?? {}
  const subLang = c.settings?.sub_lang ?? 'orig'
  const subLangShown = (subLang === 'orig' ? detail.language : subLang) as 'es' | 'en' | 'both'
  const coverAt = (rendered as { cover_at?: number }).cover_at ?? rendered.at ?? 0
  const coverT = c.settings?.cover_t ?? meta.cover_auto_t

  const regenerateMeta = async () => {
    setRegenMeta(true)
    try { await editorApi.metadata(c.id) } catch (e) { setError((e as Error).message); setRegenMeta(false) }
  }

  // Cambios de portada: se guarda el ajuste y se rehace solo la portada (≈1 s), sin re-renderizar el vídeo.
  const updateCover = async (patch: ClipPatchBody, reverse: ClipPatchBody) => {
    setCoverBusy(true)
    try {
      await apply(patch, reverse)
      setDetail(await editorApi.cover(c.id))
    } catch (e) { setError((e as Error).message) } finally { setCoverBusy(false) }
  }

  const setLayout = (id: string) => {
    apply({ settings: { crop: { mode: 'auto', layout: id as Layout, tracking: crop.tracking ?? {} } } },
      c.settings?.crop ? { settings: { crop: c.settings.crop } } : { settings: { clear_crop: true } })
  }

  const togglePlay = () => {
    const v = video.current
    if (!v) return
    if (v.paused) v.play(); else v.pause()
  }

  const saveToPc = async (what: { mp4?: boolean; png?: boolean }) => {
    setError(null)
    setSaved(null)
    setSaveBusy(true)
    try {
      const r = await saveClipsToPc([c.id], { mp4: !!what.mp4, png: !!what.png, txt: false, srt: false })
      if (r && r !== 'browser') setSaved({ text: `${what.png ? 'Portada guardada' : 'Clip guardado'} en ${r.dest}`, dest: r.dest })
    } catch (e) { setError((e as Error).message) } finally { setSaveBusy(false) }
  }

  return (
    <div className="page">
      <div className="bar">
        <strong className="monitor-title" style={{ maxWidth: 360 }}>{c.title}</strong>
        <span className="mono muted hide-sm" style={{ fontSize: 12 }}>
          {timecode(c.start)} → {timecode(c.end)} · {shortDuration(c.end - c.start)} · 1080×1920
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {saving && <span className="dim" style={{ fontSize: 12 }}>Guardando…</span>}
          <button className="btn" disabled={!undo.length || saving || busy} onClick={doUndo} title="Deshacer (Ctrl+Z)">Deshacer</button>
          {detail.dirty || busy
            ? <button className="btn primary" disabled={busy} onClick={renderNow}>{busy ? `Renderizando ${Math.round(c.progress * 100)} %` : 'Renderizar cambios'}</button>
            : <>
                <button className="btn" disabled={c.status !== 'ready' || saveBusy} onClick={() => saveToPc({ mp4: true })}
                  title={api.isDesktop() ? 'Guardar el vídeo en una carpeta del PC' : 'Descargar el vídeo'}>{saveBusy ? 'Guardando…' : 'Descargar'}</button>
                <button className="btn primary" disabled={c.status !== 'ready'} onClick={() => onNavigate('export', c.id)}
                  title="Ir a Exportar con este clip: guardar con portada y textos, o publicar en TikTok">Exportar</button>
              </>}
        </span>
      </div>
      {error && <div style={{ padding: '8px 16px 0' }}><div className="notice error">{error}</div></div>}
      {saved && (
        <div style={{ padding: '8px 16px 0' }}>
          <div className="notice ok-notice">
            <span>{saved.text}</span>
            <button className="btn small" onClick={() => { api.openFolder(saved.dest).catch(() => {}) }}>Abrir carpeta</button>
            <button className="btn link" style={{ marginLeft: 'auto' }} onClick={() => setSaved(null)}>Cerrar</button>
          </div>
        </div>
      )}

      <div className="editor-grid">
        <div className="viewer">
          <div className="viewer-frame">
            <div className="viewer-stage">
              {c.files?.mp4
                ? <video ref={video} key={String(rendered.at ?? c.id)} src={`${fileUrl(c.id, 'mp4')}?v=${rendered.at ?? 0}`}
                    onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
                    onClick={togglePlay} playsInline preload="auto" />
                : <div className="monitor-empty">Sin render todavía</div>}
              <div className="safe-zone" title="Zona segura de TikTok / Reels" />
              {busy && <div className="viewer-busy"><ClipStatus clip={c} /></div>}
            </div>
          </div>
          <div className="viewer-controls">
            <button className="btn small" onClick={togglePlay} disabled={!c.files?.mp4}>{playing ? 'Pausa' : 'Reproducir'}</button>
            <span className="mono">{timecode(time, true)}</span>
            <span className="mono dim">/ {timecode(c.end - c.start, true)}</span>
            {detail.dirty && !busy && <span className="st-warn status" style={{ marginLeft: 'auto' }}><span className="dot" />Cambios sin renderizar</span>}
          </div>
        </div>

        <SideResizer />
        <aside className="side">
          <div className="section">
            <div className="section-head"><span className="label">Encuadre</span>
              <span className="dim" style={{ fontSize: 11 }}>{crop.mode === 'manual' ? 'Manual' : activeLayout ? 'Elegido' : 'Automático'}</span>
            </div>
            <div className="section-body">
              <div className="layout-grid">
                {['follow', 'facecam', 'blur', 'split2', 'split3', 'speaker', 'react'].map((id) => (
                  <button key={id} className={`layout-btn ${shownLayout === id ? 'on' : ''}`} disabled={busy}
                    aria-label={LAYOUT_SHORT[id]} aria-pressed={shownLayout === id}
                    title={id === 'speaker' ? 'Sigue a quien habla (labios + voz); corta o se desplaza según «Cambio de plano»'
                      : id === 'react' ? 'Reacciones: el vídeo que se ve dentro del directo arriba y el streamer abajo' : undefined}
                    onClick={() => setLayout(id)}>
                    {LAYOUT_SHORT[id]}
                  </button>
                ))}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 12 }}>
                <button className="btn link" onClick={() => onNavigate('crop', c.id)}>Editar recorte manual</button>
                {c.settings?.crop && <button className="btn link" disabled={busy}
                  onClick={() => apply({ settings: { clear_crop: true } }, { settings: { crop: c.settings.crop } })}>Volver a automático</button>}
              </div>
              {crop.mode !== 'manual' && !activeLayout && c.layout && <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>Detectado: {LAYOUT_SHORT[c.layout] ?? c.layout}</div>}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span className="label">Subtítulos</span></div>
            <div className="section-body kv">
              <span>Estilo</span>
              <select className="select" value={c.settings?.style ?? 'clasico'} disabled={busy}
                onChange={(e) => apply({ settings: { style: e.target.value } }, { settings: { style: c.settings?.style ?? 'clasico' } })}>
                {options?.styles.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
              <span>Idioma</span>
              <Segmented<'es' | 'en' | 'both'> value={subLangShown} onChange={(v) => apply(
                { settings: { sub_lang: v === detail.language ? 'orig' : v } }, { settings: { sub_lang: subLang } })}
                options={[
                  { value: 'es', label: 'ES', title: detail.language === 'es' ? 'Original' : 'Traducido con IA' },
                  { value: 'en', label: 'EN', title: detail.language === 'en' ? 'Original' : 'Traducido con IA' },
                  { value: 'both', label: 'ES + EN', title: 'Dos líneas: español arriba, inglés debajo' },
                ]} />
              <span>Gancho</span>
              <TextField value={c.hook} placeholder="Texto en los primeros segundos" disabled={busy}
                onCommit={(v) => apply({ hook: v }, { hook: c.hook })} />
              <span>Duración gancho</span>
              <select className="select" value={hookSeconds} disabled={busy}
                onChange={(e) => apply({ settings: { hook_seconds: Number(e.target.value) } }, { settings: { hook_seconds: hookSeconds } })}>
                {[1.5, 2, 2.5, 3, 4].map((s) => <option key={s} value={s}>{s} s</option>)}
              </select>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span className="label">Audio</span></div>
            <div className={`section-body kv ${busy ? 'disabled-block' : ''}`}>
              <span>Efectos visuales</span>
              <Segmented<FxMode> value={fxMode} onChange={(v) => apply({ settings: { fx: v } }, { settings: { fx: fxMode } })}
                options={[{ value: 'none', label: 'Ninguno' }, { value: 'subtle', label: 'Sutil', title: 'Acercamiento lento y zoom en el momento cumbre' },
                  { value: 'dynamic', label: 'Dinámico', title: 'Además temblor en gritos, destellos en aplausos y pulsos en risas' }]} />
              <span>Sonidos</span>
              <Segmented<SfxMode> value={sfxMode} onChange={(v) => apply({ settings: { sfx: v } }, { settings: { sfx: sfxMode } })}
                options={[{ value: 'none', label: 'Ninguno' }, { value: 'auto', label: 'Auto', title: 'Solo cuando el vídeo lo pide' },
                  { value: 'subtle', label: 'Sutil' }, { value: 'meme', label: 'Meme' }]} />
              <span />
              <span className="dim" style={{ fontSize: 12 }}>Se ponen solo donde el vídeo lo pide: gritos, risas, aplausos, donaciones, frases de sorpresa o de fallo y silencios. Pasa el ratón por las marcas de la línea de tiempo para ver el motivo.</span>
              <span>Música</span>
              <span style={{ display: 'flex', gap: 6, minWidth: 0 }}>
                <select className="select" style={{ flex: 1, minWidth: 0 }} value={musicId}
                  onChange={(e) => apply({ settings: { music: e.target.value } }, { settings: { music: musicId } })}>
                  <option value="">Sin música</option>
                  {options?.music.map((t) => <option key={t.id} value={t.id}>{t.title} · {t.license}</option>)}
                </select>
                <button className="btn small" style={{ height: 28 }} onClick={() => setImporting(true)} title="Importar una pista con licencia">Importar…</button>
              </span>
              {musicId && <>
                <span>Volumen música</span>
                <select className="select" value={musicGain}
                  onChange={(e) => apply({ settings: { music_gain: Number(e.target.value) } }, { settings: { music_gain: musicGain } })}>
                  {[-28, -24, -20, -16, -12].map((g) => <option key={g} value={g}>{g} dB {g === -20 ? '(recomendado)' : ''}</option>)}
                </select>
              </>}
              <span>Normalización</span>
              <span className="mono">−14 LUFS</span>
            </div>
            {!options?.music.length && <div className="section-body dim" style={{ fontSize: 12, paddingTop: 0 }}>La biblioteca de música está vacía: importa pistas CC0, CC-BY, Pixabay o de la Biblioteca de audio de YouTube.</div>}
          </div>

          <div className="section">
            <div className="section-head"><span className="label">Metadatos</span></div>
            <div className="section-body kv">
              <span>Título ES</span>
              <TextField value={c.title} disabled={busy} onCommit={(v) => v.trim() && apply({ title: v }, { title: c.title })} />
              <span>Título EN</span>
              <TextField value={meta.title_en ?? ''} placeholder="Sin generar" disabled={busy}
                onCommit={(v) => apply({ title_en: v }, { title_en: meta.title_en ?? '' })} />
              <span>Descripción ES</span>
              <TextArea value={meta.description_es ?? ''} disabled={busy}
                onCommit={(v) => apply({ description_es: v }, { description_es: meta.description_es ?? '' })} />
              <span>Descripción EN</span>
              <TextArea value={meta.description_en ?? ''} disabled={busy}
                onCommit={(v) => apply({ description_en: v }, { description_en: meta.description_en ?? '' })} />
              <span>Hashtags</span>
              <TextField value={(c.hashtags || []).map((t) => `#${t}`).join(' ')} disabled={busy}
                onCommit={(v) => apply({ hashtags: v.split(/[\s,]+/).filter(Boolean) }, { hashtags: c.hashtags })} />
            </div>
            <div className="section-body platform-tags">
              {Object.entries(meta.platforms ?? {}).map(([p, tags]) => (
                <div key={p}><span className="dim">{PLATFORM[p] ?? p}</span> <span className="tags">{tags.map((t) => `#${t}`).join(' ')}</span></div>
              ))}
              <button className="btn small" style={{ marginTop: 6 }} disabled={busy || regenMeta} onClick={regenerateMeta}>
                {regenMeta ? 'Generando…' : 'Regenerar con IA'}</button>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span className="label">Portada</span>
              <span className="dim mono" style={{ fontSize: 11 }}>{coverT != null ? `${timecode(coverT)} · ${c.settings?.cover_t != null ? 'elegido' : 'auto'}` : ''}</span></div>
            <div className="section-body cover-row">
              {c.files?.png
                ? <button className="cover-btn" onClick={() => saveToPc({ png: true })} title="Guardar la portada en el PC"><img className="cover-img" src={`${fileUrl(c.id, 'png')}?v=${coverAt}`} alt="Portada" /></button>
                : <div className="cover-img empty">Sin portada</div>}
              <div style={{ display: 'grid', gap: 6, alignContent: 'start', minWidth: 0 }}>
                <span className="dim" style={{ fontSize: 12 }}>Texto</span>
                <TextField value={c.settings?.cover_text ?? c.title} disabled={busy || coverBusy}
                  onCommit={(v) => updateCover({ settings: { cover_text: v } }, { settings: { cover_text: c.settings?.cover_text ?? c.title } })} />
                <button className="btn small" disabled={busy || coverBusy || !c.files?.mp4}
                  aria-label="Usar fotograma actual" title={`Fotograma ${timecode(time)} del visor`}
                  onClick={() => updateCover({ settings: { cover_t: Math.round(time * 10) / 10 } }, c.settings?.cover_t != null ? { settings: { cover_t: c.settings.cover_t } } : { settings: { cover_auto: true } })}>
                  Usar fotograma actual</button>
                {c.settings?.cover_t != null && <button className="btn small" disabled={busy || coverBusy}
                  onClick={() => updateCover({ settings: { cover_auto: true } }, { settings: { cover_t: c.settings.cover_t } })}>Elegir automáticamente</button>}
                {coverBusy && <span className="dim" style={{ fontSize: 12 }}>Generando portada…</span>}
              </div>
            </div>
          </div>

          <div className="section" style={{ borderBottom: 0 }}>
            <div className="section-body" style={{ paddingTop: 12 }}><RightsNotice clip={c} /></div>
          </div>
        </aside>
      </div>

      <Timeline detail={detail} hookSeconds={hookSeconds} playhead={rStart + time} minDur={options?.clip_min ?? 3} maxDur={options?.clip_max ?? 180}
        disabled={busy}
        onSeek={(abs) => { if (video.current) video.current.currentTime = Math.max(0, abs - rStart) }}
        onTrim={(start, end) => apply({ start, end }, { start: c.start, end: c.end })} />
      {importing && options && (
        <MusicImport licenses={options.licenses} onClose={() => setImporting(false)}
          onImported={async (t) => {
            setImporting(false)
            setOptions(await editorApi.options())
            apply({ settings: { music: t.id } }, { settings: { music: musicId } })
          }} />
      )}
    </div>
  )
}

/** Área de texto que guarda al salir (Ctrl+Enter también). */
function TextArea({ value, onCommit, disabled }: { value: string; onCommit: (v: string) => void; disabled?: boolean }) {
  const [v, setV] = useState(value)
  useEffect(() => setV(value), [value])
  return (
    <textarea className="input textarea" value={v} disabled={disabled} rows={3}
      onChange={(e) => setV(e.target.value)} onBlur={() => { if (v !== value) onCommit(v) }}
      onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) e.currentTarget.blur(); if (e.key === 'Escape') { setV(value); e.currentTarget.blur() } }} />
  )
}

/** Campo de texto que guarda al pulsar Enter o al salir. */
function TextField({ value, onCommit, placeholder, disabled }: { value: string; onCommit: (v: string) => void; placeholder?: string; disabled?: boolean }) {
  const [v, setV] = useState(value)
  useEffect(() => setV(value), [value])
  const commit = () => { if (v !== value) onCommit(v) }
  return (
    <input className="input" value={v} placeholder={placeholder} disabled={disabled}
      onChange={(e) => setV(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { setV(value); e.currentTarget.blur() } }} />
  )
}
