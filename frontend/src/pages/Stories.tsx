import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Segmented } from '../components/Segmented'
import type { MusicTrack } from '../lib/api'
import { editorApi } from '../lib/api'
import { useWs } from '../lib/live'
import type {
  Episode, EpisodeDetail, Finding, Lang, Scene, Series, SeriesDetail, Story, Template, TemplateId, VoiceOption,
} from '../lib/stories'
import { BUSY, BlockedError, CHECK_LABEL, PHASES, SCENE_LABEL, storiesApi } from '../lib/stories'
import '../stories.css'

type Catalog = { templates: Record<TemplateId, Template>; stories: Story[] }
type Voices = { available: boolean; voices: Record<Lang, VoiceOption[]>; default: Record<Lang, string> }

function remembered(key: string): number | null {
  try { const v = localStorage.getItem(key); return v ? Number(v) : null } catch { return null }
}
function remember(key: string, v: number | null) {
  try { if (v != null) localStorage.setItem(key, String(v)) } catch { /* sin almacenamiento */ }
}

export function StoriesPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [list, setList] = useState<Series[]>([])
  const [seriesId, setSeriesId] = useState<number | null>(() => remembered('clipstream.series'))
  const [series, setSeries] = useState<SeriesDetail | null>(null)
  const [epNumber, setEpNumber] = useState<number>(1)
  const [episode, setEpisode] = useState<EpisodeDetail | null>(null)
  const [sceneId, setSceneId] = useState<number | null>(null)
  const [voices, setVoices] = useState<Voices | null>(null)
  const [music, setMusic] = useState<MusicTrack[]>([])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<string | null>(null)

  const loadList = useCallback(() => storiesApi.list().then(setList).catch((e) => setError(String(e.message ?? e))), [])
  useEffect(() => {
    loadList()
    storiesApi.catalog().then(setCatalog).catch(() => {})
    storiesApi.voices().then(setVoices).catch(() => {})
    editorApi.options().then((o) => setMusic(o.music.filter((m) => m.allowed))).catch(() => {})
  }, [loadList])
  useEffect(() => {
    if (seriesId == null && list.length) setSeriesId(list[0].id)
  }, [list, seriesId])

  const loadSeries = useCallback(async (id: number) => {
    try { setSeries(await storiesApi.get(id)) } catch { setSeries(null); setSeriesId(null) }
  }, [])
  useEffect(() => {
    remember('clipstream.series', seriesId)
    if (seriesId != null) loadSeries(seriesId)
    else setSeries(null)
  }, [seriesId, loadSeries])

  const epRow: Episode | undefined = series?.episodes.find((e) => e.number === epNumber)
  // Solo se muestra la respuesta del episodio que sigue seleccionado (si no, una respuesta lenta
  // del anterior pisaría al nuevo).
  const wanted = useRef<number | null>(null)
  const loadEpisode = useCallback(async (id: number) => {
    try {
      const e = await storiesApi.episode(id)
      if (wanted.current === id) setEpisode(e)
    } catch { if (wanted.current === id) setEpisode(null) }
  }, [])
  useEffect(() => {
    wanted.current = epRow?.id ?? null
    if (epRow) loadEpisode(epRow.id)
    else setEpisode(null)
  }, [epRow?.id, loadEpisode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Al cambiar de serie, abrir el primer episodio sin terminar.
  const lastSeries = useRef<number | null>(null)
  useEffect(() => {
    if (!series || lastSeries.current === series.id) return
    lastSeries.current = series.id
    const firstOpen = (series.bible.episodes ?? []).find((o) => series.episodes.find((e) => e.number === o.number)?.status !== 'done')
    setEpNumber(firstOpen?.number ?? 1)
    setSceneId(null)
  }, [series])

  // Actualización en directo: el motor avisa de cada paso.
  const pending = useRef<number | null>(null)
  useWs((m) => {
    const msg = m as unknown as { type: string; series_id?: number; episode_id?: number }
    if (msg.type === 'series') loadList()
    if (msg.type !== 'episode' || msg.series_id !== seriesId) return
    if (pending.current) window.clearTimeout(pending.current)
    pending.current = window.setTimeout(() => {
      if (seriesId != null) loadSeries(seriesId)
      if (msg.episode_id) loadEpisode(msg.episode_id)
      loadList()
    }, 250)
  })

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  const scene = episode?.scenes.find((s) => s.id === sceneId) ?? null
  const busy = !!episode && (BUSY.has(episode.status) || episode.stage === 'En cola')

  return (
    <div className="page">
      <div className="stories-grid">
        <SeriesList list={list} catalog={catalog} selected={seriesId} onSelect={setSeriesId} onNew={() => setCreating(true)} />
        <div className="main-col">
          {error && <div className="notice error" style={{ margin: '10px 16px 0' }}>{error}</div>}
          {!series && (
            <div className="placeholder">
              <div className="box">
                <div className="label" style={{ marginBottom: 8 }}>Microseries</div>
                <div className="muted">Series cortas a partir de historias de dominio público. El guardián de derechos bloquea cualquier escena con contenido protegido.</div>
                <button className="btn primary" style={{ marginTop: 14 }} onClick={() => setCreating(true)}>Nueva serie</button>
              </div>
            </div>
          )}
          {series && (
            <>
              <SeriesHeader series={series} voices={voices} music={music} busy={busy}
                onProduce={(n) => act(async () => { setEpNumber(n); await storiesApi.produce(series.id, n) })}
                onDelete={() => act(async () => {
                  if (!window.confirm(`¿Borrar la serie «${series.name}» y todos sus episodios de la lista?`)) return
                  await storiesApi.remove(series.id)
                  setSeriesId(null)
                  await loadList()
                })}
                onVoice={(lang, v) => act(async () => setSeries(await storiesApi.setVoices(series.id, { [lang]: v })))}
                onMusic={(id) => act(async () => setSeries(await storiesApi.setMusic(series.id, id)))} />
              <EpisodeTabs series={series} current={epNumber} onSelect={(n) => { setEpNumber(n); setSceneId(null) }} />
              <PhaseBar series={series} number={epNumber} episode={episode} busy={busy} act={act} />
              <Storyboard episode={episode} selected={sceneId} onSelect={setSceneId} languages={series.languages} />
            </>
          )}
        </div>
        <aside className="side">
          {series && (
            <>
              <GuardPanel series={series} episode={episode} onSelect={setSceneId}
                onFix={(s) => act(() => (s.block_reason ?? '').startsWith('Imagen:') ? storiesApi.reimageScene(s.id) : storiesApi.rewriteScene(s.id))} />
              {/* La clave incluye el texto: si el servidor lo cambia (reescritura con IA), el formulario se recarga. */}
              {scene && <SceneInspector key={`${scene.id}|${scene.narration_es}|${scene.narration_en}|${scene.image_prompt}`} scene={scene} languages={series.languages} busy={busy} act={act}
                onSaved={setEpisode} onClose={() => setSceneId(null)} />}
              {episode?.files?.videos && <ResultPanel episode={episode} onReport={() => act(async () => setReport(await storiesApi.report(episode.id)))} />}
              <CharactersPanel series={series} voices={voices} />
            </>
          )}
        </aside>
      </div>
      {creating && catalog && (
        <NewSeriesModal catalog={catalog} onClose={() => setCreating(false)}
          onCreated={(s) => { setCreating(false); loadList(); setSeriesId(s.id) }} />
      )}
      {report != null && (
        <div className="modal-back" onClick={() => setReport(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="Informe de derechos" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head"><span className="label">Informe de derechos</span>
              <button className="btn small" style={{ marginLeft: 'auto' }} onClick={() => setReport(null)}>Cerrar</button></div>
            <pre className="report-text mono">{report}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ---- lista de series ---------------------------------------------------------

function SeriesList({ list, catalog, selected, onSelect, onNew }: {
  list: Series[]; catalog: Catalog | null; selected: number | null; onSelect: (id: number) => void; onNew: () => void
}) {
  return (
    <nav className="series-list" aria-label="Series">
      <div className="section-head"><span className="label">Series</span><button className="btn small" onClick={onNew}>Nueva</button></div>
      {list.length === 0 && <div className="dim" style={{ padding: '6px 14px' }}>Aún no hay series.</div>}
      {list.map((s) => (
        <button key={s.id} className={`series-item ${s.id === selected ? 'on' : ''}`} onClick={() => onSelect(s.id)} aria-current={s.id === selected}>
          <span className="series-name">{s.name}</span>
          <span className="dim">{catalog?.templates[s.template]?.label ?? s.template} · <span className="mono">{s.episode_count ?? 0}/{s.planned_episodes ?? 0}</span> episodios</span>
        </button>
      ))}
    </nav>
  )
}

// ---- cabecera de serie -----------------------------------------------------

function nextEpisode(series: SeriesDetail): number | null {
  const outline = series.bible.episodes ?? []
  const open = outline.find((o) => series.episodes.find((e) => e.number === o.number)?.status !== 'done')
  return open?.number ?? null
}

function SeriesHeader({ series, voices, music, busy, onProduce, onDelete, onVoice, onMusic }: {
  series: SeriesDetail; voices: Voices | null; music: MusicTrack[]; busy: boolean
  onProduce: (n: number) => void; onDelete: () => void; onVoice: (l: Lang, v: string) => void; onMusic: (id: string | null) => void
}) {
  const next = nextEpisode(series)
  const story = series.story
  return (
    <div className="series-head">
      <div className="series-title-row">
        <h1 className="series-title">{series.name}</h1>
        <span className="chip">{series.template_info?.label ?? series.template}</span>
        <Segmented value={series.mode} onChange={() => {}} options={[
          { value: 'narrated', label: 'Narrado' },
          { value: 'dramatized', label: 'Dramatizado', disabled: true, title: 'Voces por personaje y labios: llega en la Fase 4' },
        ]} />
        <span className="mono dim">{series.languages.map((l) => l.toUpperCase()).join(' · ')}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn danger small" onClick={onDelete}>Borrar</button>
          <button className="btn primary" disabled={busy || next == null} onClick={() => next != null && onProduce(next)}
            title="Guion, imágenes, voz y montaje seguidos; se detiene si el guardián bloquea una escena">
            {next == null ? 'Serie completa' : `Generar episodio ${next}`}
          </button>
        </div>
      </div>
      <div className="series-meta">
        <span><span className="dim">Fuente</span> {story ? `${story.title} — ${story.source}` : 'Historia original'}</span>
        <span className="st-ok">{story ? story.basis : 'Sin obra ajena'}</span>
        {series.languages.map((lang) => (
          <label key={lang} className="inline-field"><span className="dim">Voz {lang.toUpperCase()}</span>
            <select className="select" value={series.bible.voices?.[lang] ?? voices?.default[lang] ?? ''} onChange={(e) => onVoice(lang, e.target.value)}
              disabled={!voices}>
              {(voices?.voices[lang] ?? []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </label>
        ))}
        <label className="inline-field"><span className="dim">Música</span>
          <select className="select" value={series.bible.music ?? ''} onChange={(e) => onMusic(e.target.value || null)}>
            <option value="">Base propia del ambiente ({series.template_info?.music_mood})</option>
            {music.filter((m) => !m.id.startsWith('ambient_')).map((m) => <option key={m.id} value={m.id}>{m.title} · {m.license}</option>)}
            {music.filter((m) => m.id.startsWith('ambient_')).map((m) => <option key={m.id} value={m.id}>{m.title} (CC0)</option>)}
          </select>
        </label>
      </div>
      {series.synopsis && <p className="synopsis muted">{series.synopsis}</p>}
    </div>
  )
}

// ---- episodios y fases -----------------------------------------------------

function EpisodeTabs({ series, current, onSelect }: { series: SeriesDetail; current: number; onSelect: (n: number) => void }) {
  const outline = series.bible.episodes ?? []
  return (
    <div className="ep-tabs" role="tablist">
      {outline.map((o) => {
        const e = series.episodes.find((x) => x.number === o.number)
        const cls = !e ? 'none' : e.status === 'done' ? 'ok' : e.status === 'blocked' || e.status === 'error' ? 'bad' : BUSY.has(e.status) ? 'run' : 'part'
        return (
          <button key={o.number} role="tab" aria-selected={o.number === current} className={`ep-tab ${o.number === current ? 'on' : ''}`}
            onClick={() => onSelect(o.number)} title={o.summary}>
            <span className={`ep-dot ${cls}`} /><span className="mono">{String(o.number).padStart(2, '0')}</span> {e?.title || o.title}
          </button>
        )
      })}
    </div>
  )
}

function PhaseBar({ series, number, episode, busy, act }: {
  series: SeriesDetail; number: number; episode: EpisodeDetail | null; busy: boolean; act: (fn: () => Promise<unknown>) => void
}) {
  const phases = episode?.phases ?? {}
  const outline = series.bible.episodes?.find((o) => o.number === number)
  const hasScenes = !!episode?.scenes.length
  const blocked = !!episode?.scenes.some((s) => s.status === 'blocked')
  const allImages = hasScenes && episode!.scenes.every((s) => s.files?.image?.png)
  const allVoice = hasScenes && episode!.scenes.every((s) => s.files?.voice && Object.keys(s.files.voice).length)
  const label = episode?.status === 'done' ? 'Volver a producir' : hasScenes ? 'Continuar' : 'Producir episodio'
  return (
    <div className="phase-bar">
      <div className="phase-row">
        {PHASES.map((p, i) => {
          const st = phases[p.id] ?? 'pending'
          return (
            <div key={p.id} className={`phase ${st}`}>
              <span className="mono phase-n">{i + 1}</span>
              <span>{p.label}</span>
              <span className="phase-st">{({ pending: 'Pendiente', running: 'En curso', done: 'Hecho', blocked: 'Bloqueado', error: 'Error' } as const)[st]}</span>
            </div>
          )
        })}
      </div>
      <div className="phase-actions">
        <span className="muted ep-summary" title={episode?.summary ?? outline?.summary}>
          {episode?.stage ? <span className={episode.status === 'error' || episode.status === 'blocked' ? 'st-live' : busy ? '' : 'st-ok'}>{episode.stage}</span> : outline?.summary}
          {episode?.error && <span className="st-live"> · {episode.error}</span>}
        </span>
        <button className="btn small" disabled={busy} onClick={() => act(async () => {
          if (hasScenes && !window.confirm('Se reescribe el guion completo y se pierden las ediciones de este episodio. ¿Seguir?')) return
          await storiesApi.script(series.id, number)
        })}>{hasScenes ? 'Reescribir guion' : 'Solo guion'}</button>
        <button className="btn small" disabled={busy || !hasScenes} onClick={() => act(() => storiesApi.images(episode!.id))}>Imágenes</button>
        <button className="btn small" disabled={busy || !hasScenes || blocked} onClick={() => act(() => storiesApi.voice(episode!.id))}>Voz</button>
        <button className="btn small" disabled={busy || !allImages || !allVoice || blocked} onClick={() => act(() => storiesApi.assemble(episode!.id))}>Montar</button>
        <button className="btn primary small" disabled={busy || blocked} onClick={() => act(() => storiesApi.produce(series.id, number))}
          title={blocked ? 'Corrige las escenas bloqueadas primero' : 'Hace lo que falte, en orden'}>{label}</button>
      </div>
      {busy && <div className="progress-line"><div className="indeterminate" /></div>}
    </div>
  )
}

// ---- storyboard ------------------------------------------------------------

function Storyboard({ episode, selected, onSelect, languages }: {
  episode: EpisodeDetail | null; selected: number | null; onSelect: (id: number) => void; languages: Lang[]
}) {
  const [lang, setLang] = useState<Lang>('es')
  if (!episode?.scenes.length) {
    return <div className="placeholder"><div className="dim">{episode?.status === 'scripting' ? 'Escribiendo el guion…' : 'Este episodio aún no tiene guion.'}</div></div>
  }
  const total = episode.scenes.reduce((a, s) => a + s.duration, 0)
  return (
    <section className="storyboard">
      <div className="section-head">
        <span className="label">Storyboard · {episode.scenes.length} escenas · <span className="mono">{total.toFixed(1)} s</span></span>
        {languages.length > 1 && <Segmented value={lang} onChange={setLang} options={languages.map((l) => ({ value: l, label: l.toUpperCase() }))} />}
      </div>
      <div className="scene-grid">
        {episode.scenes.map((s) => (
          <button key={s.id} className={`scene ${s.status} ${s.id === selected ? 'on' : ''}`} onClick={() => onSelect(s.id)}>
            <div className="scene-thumb">
              {s.files?.image?.png ? <img src={storiesApi.imageUrl(s)} alt={`Escena ${s.idx + 1}`} loading="lazy" /> : <span className="dim">Sin imagen</span>}
            </div>
            <div className="scene-info">
              <div className="scene-top">
                <span className="mono">{String(s.idx + 1).padStart(2, '0')}</span>
                <span className="mono dim">{s.duration.toFixed(1)} s</span>
                <span className={`scene-st ${s.status}`}>{SCENE_LABEL[s.status] ?? s.status}</span>
              </div>
              <p className="scene-text">{(lang === 'en' ? s.narration_en : s.narration_es) || <span className="dim">—</span>}</p>
            </div>
          </button>
        ))}
      </div>
    </section>
  )
}

// ---- guardián de derechos ----------------------------------------------------

function GuardPanel({ series, episode, onSelect, onFix }: {
  series: SeriesDetail; episode: EpisodeDetail | null; onSelect: (id: number) => void; onFix: (s: Scene) => void
}) {
  const rows = useMemo(() => {
    const out: { key: string; ok: boolean; detail: string }[] = []
    const story = series.rights.story
    if (story) out.push({ key: 'historia', ok: story.ok, detail: story.message })
    const findings: Finding[] = episode?.rights?.findings ?? []
    for (const check of ['franquicias', 'letras', 'citas']) {
      const g = findings.filter((f) => f.check === check)
      if (!g.length) continue
      const bad = g.filter((f) => !f.ok).length
      out.push({ key: check, ok: bad === 0, detail: bad ? `${bad} de ${g.length} con problemas` : `${g.length} revisiones correctas` })
    }
    const imgs = (episode?.scenes ?? []).filter((s) => s.files?.image?.check)
    if (imgs.length) {
      const bad = imgs.filter((s) => !s.files!.image!.check.ok).length
      out.push({ key: 'imagenes', ok: bad === 0, detail: bad ? `${bad} de ${imgs.length} se parecen a un personaje protegido` : `${imgs.length} imágenes sin parecidos` })
    }
    return out
  }, [series, episode])
  const blocked = episode?.scenes.filter((s) => s.status === 'blocked') ?? []
  return (
    <section className="section">
      <div className="section-head"><span className="label">Guardián de derechos · bloqueo activo</span></div>
      <div className="section-body">
        <ul className="guard-list">
          {rows.map((r) => (
            <li key={r.key}><span className={`status ${r.ok ? 'st-ok' : 'st-live'}`}><span className="dot" /></span>
              <span><span>{CHECK_LABEL[r.key] ?? r.key}</span><span className="dim guard-detail">{r.detail}</span></span></li>
          ))}
          {!rows.length && <li className="dim">Se revisa al escribir el guion.</li>}
        </ul>
        {blocked.map((s) => (
          <div key={s.id} className="block-box">
            <div className="label" style={{ color: 'var(--live-text)' }}>Escena {s.idx + 1} bloqueada</div>
            <div>{s.block_reason}</div>
            {s.suggestion && <div className="dim">{s.suggestion}</div>}
            <div className="block-actions">
              <button className="btn small" onClick={() => onFix(s)}>Regenerar</button>
              <button className="btn small" onClick={() => onSelect(s.id)}>Ver detalle</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ---- escena seleccionada -------------------------------------------------------

function SceneInspector({ scene, languages, busy, act, onSaved, onClose }: {
  scene: Scene; languages: Lang[]; busy: boolean; act: (fn: () => Promise<unknown>) => void
  onSaved: (e: EpisodeDetail) => void; onClose: () => void
}) {
  const [es, setEs] = useState(scene.narration_es)
  const [en, setEn] = useState(scene.narration_en)
  const [prompt, setPrompt] = useState(scene.image_prompt)
  const dirty = es !== scene.narration_es || en !== scene.narration_en || prompt !== scene.image_prompt
  const chk = scene.files?.image?.check
  return (
    <section className="section">
      <div className="section-head"><span className="label">Escena {scene.idx + 1}</span><button className="btn small" onClick={onClose}>Cerrar</button></div>
      <div className="section-body inspector">
        {scene.files?.image?.png && <img className="inspector-img" src={storiesApi.imageUrl(scene)} alt={`Escena ${scene.idx + 1}`} />}
        {chk && <div className={chk.ok ? 'dim' : 'st-live'}>{chk.ok ? `Imagen revisada · más parecido: ${chk.match} (${Math.round(chk.prob * 100)} %)` : chk.message}</div>}
        {scene.files?.image?.fidelity?.message && <div className={scene.files.image.fidelity.ok ? 'dim' : 'st-warn'}>Contenido: {scene.files.image.fidelity.message}</div>}
        <label className="field"><span className="label">Narración ES</span>
          <textarea className="input area" rows={4} value={es} onChange={(e) => setEs(e.target.value)} /></label>
        {languages.includes('en') && <label className="field"><span className="label">Narración EN</span>
          <textarea className="input area" rows={4} value={en} onChange={(e) => setEn(e.target.value)} /></label>}
        <label className="field"><span className="label">Descripción de la imagen (inglés)</span>
          <textarea className="input area mono" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
        {languages.map((l) => scene.files?.voice?.[l] && (
          <div key={l} className="voice-row"><span className="mono dim">{l.toUpperCase()}</span>
            <audio controls preload="none" src={storiesApi.voiceUrl(scene, l)} /></div>
        ))}
        <div className="inspector-actions">
          <button className="btn primary small" disabled={!dirty || busy} title="El guardián vuelve a revisar el texto"
            onClick={() => act(async () => onSaved(await storiesApi.editScene(scene.id, { narration_es: es, narration_en: en, image_prompt: prompt })))}>Guardar</button>
          <button className="btn small" disabled={busy || scene.status === 'blocked' || scene.status === 'approved'}
            onClick={() => act(async () => onSaved(await storiesApi.editScene(scene.id, { status: 'approved' })))}>Aprobar</button>
          <button className="btn small" disabled={busy} onClick={() => act(() => storiesApi.rewriteScene(scene.id))}>Reescribir con IA</button>
          <button className="btn small" disabled={busy} onClick={() => act(() => storiesApi.reimageScene(scene.id))}>Otra imagen</button>
        </div>
        {dirty && <div className="dim">Al cambiar el texto, la voz de esta escena se debe volver a generar.</div>}
      </div>
    </section>
  )
}

// ---- resultado y personajes ----------------------------------------------------

function ResultPanel({ episode, onReport }: { episode: Episode; onReport: () => void }) {
  const langs = Object.keys(episode.files?.videos ?? {}) as Lang[]
  const [lang, setLang] = useState<Lang>(langs[0] ?? 'es')
  return (
    <section className="section">
      <div className="section-head"><span className="label">Episodio montado</span>
        {langs.length > 1 && <Segmented value={lang} onChange={setLang} options={langs.map((l) => ({ value: l, label: l.toUpperCase() }))} />}</div>
      <div className="section-body">
        <video key={lang} className="result-video" controls preload="metadata" src={storiesApi.videoUrl(episode, lang)} />
        <div className="block-actions">
          <a className="btn small" href={storiesApi.videoUrl(episode, lang)} download={`ep${String(episode.number).padStart(2, '0')}_${lang}.mp4`}>Descargar</a>
          <button className="btn small" onClick={onReport}>Informe de derechos</button>
          <span className="mono dim" style={{ marginLeft: 'auto' }}>{(episode.files?.duration ?? 0).toFixed(1)} s</span>
        </div>
      </div>
    </section>
  )
}

function CharactersPanel({ series, voices }: { series: SeriesDetail; voices: Voices | null }) {
  const narrator = series.languages.map((l) => {
    const id = series.bible.voices?.[l] ?? voices?.default[l]
    return `${voices?.voices[l]?.find((v) => v.id === id)?.label ?? id ?? '—'} (${l.toUpperCase()})`
  }).join(' · ')
  return (
    <section className="section">
      <div className="section-head"><span className="label">Personajes</span></div>
      <div className="section-body">
        <div className="char"><div><b>Narrador</b> <span className="dim">voz</span></div><div className="muted">{narrator}</div></div>
        {series.characters.map((c) => (
          <div key={c.id} className="char">
            <div><b>{c.name}</b> <span className="dim">{c.role}{c.kind ? ` · ${c.kind}` : ''}</span></div>
            {c.description && <div className="muted">{c.description}</div>}
            {c.visual && <div className="dim mono char-visual" title="Referencia visual fija que se usa en todas las imágenes">{c.visual}</div>}
          </div>
        ))}
      </div>
    </section>
  )
}

// ---- nueva serie ------------------------------------------------------------

function NewSeriesModal({ catalog, onClose, onCreated }: { catalog: Catalog; onClose: () => void; onCreated: (s: SeriesDetail) => void }) {
  const ids = Object.keys(catalog.templates) as TemplateId[]
  const [template, setTemplate] = useState<TemplateId>(ids[0])
  const stories = catalog.stories.filter((s) => s.template === template)
  const original = !!catalog.templates[template]?.original
  const [storyId, setStoryId] = useState<string>('')
  const [custom, setCustom] = useState('')
  const [name, setName] = useState('')
  const [langs, setLangs] = useState<Lang[]>(['es', 'en'])
  const [episodes, setEpisodes] = useState(4)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<{ message: string; suggestion?: string } | null>(null)

  useEffect(() => {
    const first = catalog.stories.find((s) => s.template === template)
    setStoryId(first?.id ?? '')
    setEpisodes(Math.min(first?.episodes ?? 4, 20))
    setErr(null)
  }, [template, catalog])

  const create = async () => {
    setBusy(true)
    setErr(null)
    try {
      onCreated(await storiesApi.create({
        template, story_id: storyId && storyId !== 'custom' ? storyId : null,
        custom_title: storyId === 'custom' || original ? custom : '', name, languages: langs, episodes,
      }))
    } catch (e) {
      setErr(e instanceof BlockedError ? { message: e.message, suggestion: e.suggestion } : { message: e instanceof Error ? e.message : String(e) })
    } finally { setBusy(false) }
  }
  const story = stories.find((s) => s.id === storyId)
  return (
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal" style={{ width: 'min(560px, 100%)' }} role="dialog" aria-modal="true" aria-label="Nueva serie" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><span className="label">Nueva serie</span></div>
        <div className="new-series">
          <div className="field"><span className="label">Plantilla</span>
            <Segmented value={template} onChange={setTemplate} options={ids.map((t) => ({ value: t, label: catalog.templates[t].label }))} /></div>
          <div className="dim">{catalog.templates[template]?.tone} · {catalog.templates[template]?.style}</div>
          {!original && (
            <label className="field"><span className="label">Historia de dominio público</span>
              <select className="select" value={storyId} onChange={(e) => setStoryId(e.target.value)}>
                {stories.map((s) => <option key={s.id} value={s.id}>{s.title} — {s.source}</option>)}
                <option value="custom">Otra historia (se comprueba que sea de dominio público)</option>
              </select></label>
          )}
          {story && <div className="dim">{story.basis}{story.avoid?.length ? ` · se evita: ${story.avoid.join('; ')}` : ''}</div>}
          {(storyId === 'custom' || original) && (
            <label className="field"><span className="label">{original ? 'Idea o título (opcional)' : 'Título y autor de la historia'}</span>
              <input className="input" value={custom} maxLength={120} onChange={(e) => setCustom(e.target.value)}
                placeholder={original ? 'La piña que quería ser reina' : 'El gato con botas (Perrault, 1697)'} /></label>
          )}
          <label className="field"><span className="label">Nombre (opcional)</span>
            <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="Lo propone la IA" /></label>
          <div className="field-row">
            <div className="field"><span className="label">Idiomas</span>
              <div style={{ display: 'flex', gap: 14 }}>
                {(['es', 'en'] as Lang[]).map((l) => (
                  <label key={l} className="check"><input type="checkbox" checked={langs.includes(l)} disabled={l === 'es'}
                    onChange={(e) => setLangs(e.target.checked ? [...langs, l] : langs.filter((x) => x !== l))} />{l === 'es' ? 'Español' : 'Inglés'}</label>
                ))}
              </div></div>
            <label className="field"><span className="label">Episodios</span>
              <input className="input mono" type="number" min={1} max={20} value={episodes} style={{ width: 80 }}
                onChange={(e) => setEpisodes(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} /></label>
          </div>
          {err && (
            <div className="block-box">
              <div className="label" style={{ color: 'var(--live-text)' }}>Bloqueado por el guardián de derechos</div>
              <div>{err.message}</div>
              {err.suggestion && <div className="dim">{err.suggestion}</div>}
            </div>
          )}
          <div className="block-actions" style={{ justifyContent: 'flex-end' }}>
            {busy && <span className="dim" style={{ marginRight: 'auto' }}>Escribiendo la biblia de la serie… (unos 20 s)</span>}
            <button className="btn" disabled={busy} onClick={onClose}>Cancelar</button>
            <button className="btn primary" disabled={busy || (!original && !storyId) || (storyId === 'custom' && !custom.trim())} onClick={create}>Crear serie</button>
          </div>
        </div>
      </div>
    </div>
  )
}
