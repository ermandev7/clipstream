import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ActivityBar, MiniHist } from '../components/Charts'
import { ClipModal } from '../components/ClipModal'
import { ProgressSteps } from '../components/ProgressSteps'
import { LiveVideo } from '../components/LiveVideo'
import { LogPanel } from '../components/LogPanel'
import { MonitorResizer, SideResizer } from '../components/Resizers'
import { ClipStatus } from '../components/RightsBadge'
import { Segmented } from '../components/Segmented'
import type { Clip, Source, SourceDetail, SourceKind, StorageReport } from '../lib/api'
import { api, fileUrl, fmtBytes, getCleanMode, shortDuration, timecode } from '../lib/api'
import { StartCleanDialog, StorageDialog } from '../components/StorageDialogs'
import { useWs } from '../lib/live'
import type { TikTokStatus } from '../lib/publish'
import { publishApi } from '../lib/publish'
import { BatchDialog } from '../components/BatchDialog'

type Flow = 'manual' | 'auto'

const KIND_LABEL: Record<SourceKind, string> = { live: 'Directo', vod: 'Vídeo publicado', local: 'Archivo local' }
const BUSY = new Set(['pending', 'downloading', 'analyzing'])
const PLACEHOLDER: Record<string, Partial<Record<SourceKind, string>>> = {
  youtube: { live: 'https://www.youtube.com/watch?v=… (en directo)', vod: 'https://www.youtube.com/watch?v=…' },
  kick: { live: 'https://kick.com/canal', vod: 'https://kick.com/canal/videos/…' },
}

function upsert<T extends { id: number }>(list: T[], item: T, sortKey?: (x: T) => number): T[] {
  const i = list.findIndex((x) => x.id === item.id)
  const next = i >= 0 ? list.map((x) => (x.id === item.id ? { ...x, ...item } : x)) : [...list, item]
  return sortKey ? next.sort((a, b) => sortKey(a) - sortKey(b)) : next
}

export function SourcesPage({ onOpenClip, onGoExport }: { onOpenClip: (id: number) => void; onGoExport?: () => void }) {
  // Automático: pegas el link y ClipStream analiza, genera los clips y deja preparada la tanda de TikTok
  // (los mejores primero, N al día). Solo falta aprobarla: TikTok exige revisar cada publicación.
  const [flow, setFlow] = useState<Flow>(() => {
    try { return localStorage.getItem('clipstream.flow') === 'auto' ? 'auto' : 'manual' } catch { return 'manual' }
  })
  const chooseFlow = (f: Flow) => {
    setFlow(f)
    try { localStorage.setItem('clipstream.flow', f) } catch { /* sin almacenamiento */ }
  }
  const [tiktok, setTiktok] = useState<TikTokStatus | null>(null)
  const [batchFor, setBatchFor] = useState<number | null>(null)
  const lastStatus = useRef<Map<number, string>>(new Map())
  useEffect(() => { publishApi.status().then(setTiktok).catch(() => {}) }, [])
  const [mode, setMode] = useState<SourceKind>('vod')
  const [platform, setPlatform] = useState('youtube')
  const [url, setUrl] = useState('')
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [detail, setDetail] = useState<SourceDetail | null>(null)
  const [openClip, setOpenClip] = useState<Clip | null>(null)
  const refetchTimer = useRef<number | null>(null)
  const [askClean, setAskClean] = useState<StorageReport | null>(null)
  // Directo: además de los picos fuertes, el mejor momento de cada bloque de N segundos (0 = solo picos).
  const [liveEvery, setLiveEvery] = useState<number>(() => {
    try { const v = Number(localStorage.getItem('clipstream.liveEvery')); if ([0, 180, 300, 600].includes(v) && localStorage.getItem('clipstream.liveEvery') !== null) return v } catch { /* sin almacenamiento */ }
    return 300
  })
  const chooseLiveEvery = (v: number) => {
    setLiveEvery(v)
    try { localStorage.setItem('clipstream.liveEvery', String(v)) } catch { /* sin almacenamiento */ }
  }
  const [storageOpen, setStorageOpen] = useState(false)
  const [totalBytes, setTotalBytes] = useState<number | null>(null)
  const refreshStorage = useCallback(() => { api.storage().then((r) => setTotalBytes(r.total_bytes)).catch(() => {}) }, [])
  useEffect(() => { refreshStorage() }, [refreshStorage, sources.length])
  const reloadSources = useCallback(async () => {
    const list = await api.sources()
    setSources(list)
    if (selected !== null && !list.some((s) => s.id === selected)) {
      setSelected(list[0]?.id ?? null)
      if (!list.length) setDetail(null)
    }
    refreshStorage()
  }, [selected, refreshStorage])

  const loadDetail = useCallback(async (id: number) => {
    try { setDetail(await api.source(id)) } catch { setDetail(null) }
  }, [])

  useEffect(() => {
    api.sources().then((list) => {
      setSources(list)
      if (list.length && selected === null) setSelected(list[0].id)
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { if (selected !== null) loadDetail(selected) }, [selected, loadDetail])

  const scheduleRefetch = useCallback((id: number) => {
    if (refetchTimer.current) return
    refetchTimer.current = window.setTimeout(() => {
      refetchTimer.current = null
      loadDetail(id)
    }, 1500)
  }, [loadDetail])

  useWs((m) => {
    if (m.type === 'source' && m.source) {
      setSources((l) => upsert(l, m.source, (x) => -x.id))
      if (m.source.id === selected) {
        setDetail((d) => (d ? { ...d, ...m.source } : d))
        scheduleRefetch(m.source.id) // señales y miniaturas nuevas
      }
    } else if (m.type === 'clip' && m.clip && m.clip.source_id === selected) {
      setDetail((d) => (d ? { ...d, clips: upsert(d.clips, m.clip, (x) => x.start) } : d))
      setOpenClip((c) => (c && c.id === m.clip.id ? { ...c, ...m.clip } : c))
    } else if (m.type === 'log' && m.source_id === selected) {
      setDetail((d) => (d ? { ...d, events: [...d.events.slice(-199), { ts: m.ts, level: m.level, message: m.message, source_id: m.source_id }] } : d))
    }
  })

  // Empezar un vídeo nuevo: según la preferencia, pregunta si borrar los anteriores (con sus clips).
  const start = async () => {
    setError(null)
    const pref = getCleanMode()
    if (pref !== 'keep') {
      try {
        const report = await api.storage()
        if (report.sources.some((s) => !s.busy)) {
          if (pref === 'ask') { setAskClean(report); return }
          return doStart(true)
        }
      } catch { /* sin datos de espacio: se empieza sin limpiar */ }
    }
    return doStart(false)
  }
  const doStart = async (clean: boolean) => {
    setAskClean(null)
    setBusy(true)
    try {
      const auto = flow === 'auto'
      const body = mode === 'local' ? { kind: mode, path, clean_previous: clean, auto_publish: auto }
        : { kind: mode, platform, url: url.trim(), clean_previous: clean, auto_publish: auto, ...(mode === 'live' ? { live_every: liveEvery } : {}) }
      const src = await api.createSource(body)
      if (clean) await reloadSources()
      setSources((l) => upsert(l, src, (x) => -x.id))
      setSelected(src.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const pick = async () => {
    setError(null)
    try {
      const p = await api.pickFile()
      if (p) setPath(p)
    } catch (e) { setError((e as Error).message) }
  }

  const capturing = detail?.kind === 'live' && detail.status === 'live'
  const readyCount = detail?.clips.filter((c) => c.status === 'ready').length ?? 0
  const isAuto = !!detail?.auto_publish
  // Al terminar un vídeo en modo automático (mientras la app está abierta) se abre la tanda para aprobarla.
  useEffect(() => {
    if (!detail) return
    const prev = lastStatus.current.get(detail.id)
    lastStatus.current.set(detail.id, detail.status)
    if (isAuto && prev && prev !== 'done' && detail.status === 'done' && readyCount > 0 && tiktok?.connected) setBatchFor(detail.id)
  }, [detail, isAuto, readyCount, tiktok?.connected])
  const reprocess = async () => {
    if (!detail) return
    setError(null)
    try {
      const src = await api.reprocess(detail.id)
      setSources((l) => upsert(l, src, (x) => -x.id))
      setSelected(src.id)
    } catch (e) { setError((e as Error).message) }
  }
  const stop = async () => {
    if (!detail) return
    try { await api.stopSource(detail.id) } catch (e) { setError((e as Error).message) }
  }

  return (
    <div className="page">
      <div className="bar">
        <span className="label">Fuente</span>
        <Segmented<SourceKind> value={mode} onChange={(v) => { setMode(v); setError(null) }} options={[
          { value: 'live', label: 'Directo' },
          { value: 'vod', label: 'Vídeo publicado' },
          { value: 'local', label: 'Archivo local' },
        ]} />
        {mode !== 'local' ? (
          <>
            <select className="select platform" value={platform} onChange={(e) => setPlatform(e.target.value)} aria-label="Plataforma">
              <option value="youtube">YouTube</option>
              <option value="kick">Kick</option>
            </select>
            <input className="input mono grow" placeholder={PLACEHOLDER[platform]?.[mode] ?? ''}
              value={url} onChange={(e) => {
                const v = e.target.value
                setUrl(v)
                // El link manda: si es de Kick o de YouTube, se elige la plataforma sola.
                if (/kick\.com\//i.test(v)) setPlatform('kick')
                else if (/(youtube\.com|youtu\.be)\//i.test(v)) setPlatform('youtube')
              }} onKeyDown={(e) => e.key === 'Enter' && url && start()}
              aria-label="Link" spellCheck={false} />
            {mode === 'live' && !capturing && (
              <select className="select" value={liveEvery} onChange={(e) => chooseLiveEvery(Number(e.target.value))} aria-label="Ritmo de clips del directo"
                title="Además de los momentos fuertes (que se recortan al momento), ClipStream analiza el directo por bloques y saca el mejor momento de cada uno">
                <option value={180}>Un clip cada 3 min</option>
                <option value={300}>Un clip cada 5 min</option>
                <option value={600}>Un clip cada 10 min</option>
                <option value={0}>Solo momentos fuertes</option>
              </select>
            )}
            {mode === 'live' && capturing
              ? <button className="btn danger" onClick={stop}>Detener captura</button>
              : <button className="btn primary" disabled={!url.trim() || busy} onClick={start}>
                  {busy ? 'Conectando…' : mode === 'live' ? 'Iniciar captura' : 'Analizar'}
                </button>}
          </>
        ) : (
          <>
            <input className="input mono grow" readOnly value={path} placeholder="Ningún archivo elegido" aria-label="Ruta del archivo" />
            <button className="btn" onClick={pick}>Abrir ubicación…</button>
            <button className="btn primary" disabled={!path || busy} onClick={start}>Analizar</button>
          </>
        )}
        <Segmented<Flow> value={flow} onChange={chooseFlow} options={[
          { value: 'manual', label: 'Manual' },
          { value: 'auto', label: 'Automático' },
        ]} />
        <button className="btn storage-btn" onClick={() => setStorageOpen(true)} title="Cuánto ocupa cada vídeo y borrar los que ya no necesites">
          Espacio{totalBytes != null ? <span className="mono dim"> · {fmtBytes(totalBytes)}</span> : null}
        </button>
      </div>
      {error && <div style={{ padding: '8px 16px 0' }}><div className="notice error">{error}</div></div>}
      {isAuto && detail && (
        <div className="auto-banner">
          <span><b>Automático</b> · {detail.status === 'done' || capturing
            ? readyCount ? `${readyCount} clips listos. La tanda de TikTok está preparada: solo falta aprobarla.` : 'Sin clips listos todavía.'
            : 'Analizando y generando clips; al terminar se prepara la tanda de TikTok.'}</span>
          {readyCount > 0 && (tiktok?.connected
            ? <button className="btn small primary" onClick={() => setBatchFor(detail.id)}>Revisar y aprobar tanda</button>
            : <button className="btn small" onClick={onGoExport}>Conectar TikTok en Exportar</button>)}
        </div>
      )}
      {batchFor != null && tiktok && <BatchDialog status={tiktok} sourceId={batchFor} onClose={() => setBatchFor(null)} />}
      {askClean && <StartCleanDialog report={askClean} onChoose={(clean) => doStart(clean)} onCancel={() => setAskClean(null)} />}
      {storageOpen && <StorageDialog currentId={selected} onChanged={reloadSources} onClose={() => setStorageOpen(false)} />}

      <div className="workspace">
        <div className="main-col">
          <MonitorHead detail={detail} sources={sources} selected={selected} onSelect={setSelected} onStop={stop} onReprocess={reprocess} />
          {detail && <ProgressSteps detail={detail} />}
          <Monitor detail={detail} />
          <MonitorResizer />
          <ActivityBar values={detail?.signals?.activity} duration={detail?.signals?.duration ?? detail?.duration ?? 0}
            offset={detail?.signals?.offset ?? 0} clips={detail?.clips ?? []} onPick={setOpenClip} />
          <ClipTable clips={detail?.clips ?? []} onOpen={(c) => onOpenClip(c.id)} status={detail?.status} />
        </div>
        <SideResizer />
        <aside className="side">
          <SignalsPanel detail={detail} />
          {detail && detail.kind !== 'local' && <PermissionPanel detail={detail} onChange={() => loadDetail(detail.id)} />}
          <LogPanel detail={detail} />
        </aside>
      </div>
      {openClip && <ClipModal clip={openClip} onClose={() => setOpenClip(null)} />}
    </div>
  )
}

function statusNode(d: SourceDetail | null) {
  if (!d) return <span className="dim label">Sin fuente</span>
  if (d.status === 'live') return <span className="live-pill"><span className="dot" />EN VIVO</span>
  if (d.status === 'downloading') return <span className="label" style={{ color: 'var(--accent)' }}>Descargando {Math.round(d.progress * 100)} %</span>
  if (d.status === 'analyzing' || d.status === 'pending') return <span className="label" style={{ color: 'var(--accent)' }}>Analizando {Math.round(d.progress * 100)} %</span>
  if (d.status === 'error') return <span className="label" style={{ color: 'var(--live-text)' }}>Error</span>
  if (d.status === 'rendering') return <span className="label" style={{ color: 'var(--accent)' }}>Generando clips</span>
  if (d.kind === 'local') return <span className="label">Archivo local</span>
  if (d.status === 'stopped') return <span className="label">Detenido</span>
  return <span className="label" style={{ color: 'var(--ok)' }}>Terminado</span>
}

function MonitorHead({ detail, sources, selected, onSelect, onStop, onReprocess }: {
  detail: SourceDetail | null; sources: Source[]; selected: number | null; onSelect: (id: number) => void; onStop: () => void
  onReprocess: () => void
}) {
  const capturing = detail?.kind === 'live' && detail.status === 'live'
  const ended = detail?.kind === 'live' && detail.status !== 'live'
  return (
    <div className="monitor-head">
      {statusNode(detail)}
      <span className="monitor-title">
        {detail ? (detail.title || detail.url || detail.path) : 'Pega un link o abre un archivo para empezar'}
        {detail?.channel && <span className="muted"> · {detail.channel}</span>}
      </span>
      {capturing && <button className="btn small danger" style={{ marginLeft: 'auto', flex: 'none' }} onClick={onStop}>Detener captura</button>}
      {ended && <button className="btn small" style={{ marginLeft: 'auto', flex: 'none' }} onClick={onReprocess}
        title="Analiza la repetición publicada del directo entero (cuando YouTube o Kick la tengan lista)">Reprocesar directo completo</button>}
      {sources.length > 0 && (
        <select className="select" style={{ marginLeft: capturing || ended ? 0 : 'auto', maxWidth: 220 }} value={selected ?? ''} aria-label="Historial de fuentes"
          onChange={(e) => onSelect(Number(e.target.value))}>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>#{s.id} · {KIND_LABEL[s.kind]} · {(s.title || s.url || s.path || '').slice(0, 40)}</option>
          ))}
        </select>
      )}
    </div>
  )
}

function Monitor({ detail }: { detail: SourceDetail | null }) {
  const [tc, setTc] = useState(0)
  const live = detail?.kind === 'live' && detail.status === 'live'
  const hasMedia = detail && detail.kind !== 'live' && detail.path && !['pending', 'downloading'].includes(detail.status)
  return (
    <div className="monitor-wrap">
      <div className="monitor-box">
        <div className="monitor">
          {hasMedia && <video key={detail.id} src={`/api/sources/${detail.id}/media`} controls preload="metadata"
            onTimeUpdate={(e) => setTc(e.currentTarget.currentTime)} />}
          {live && <LiveVideo key={detail.id} sourceId={detail.id} />}
          {!hasMedia && !live && (
            <div className="monitor-empty">
              {detail?.status === 'downloading' ? 'Descargando vídeo…' : detail ? 'Sin vista previa' : 'Monitor'}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 12 }}>
          <span className="mono">{timecode(live ? detail!.duration : tc, true)}</span>
          <span className="mono muted">{detail?.duration ? `${timecode(detail.duration)} ${live ? 'capturado' : 'total'}` : ''}</span>
        </div>
      </div>
    </div>
  )
}

function ClipTable({ clips, onOpen, status }: { clips: Clip[]; onOpen: (c: Clip) => void; status?: string }) {
  const sorted = useMemo(() => [...clips].sort((a, b) => a.start - b.start), [clips])
  // Se generan de uno en uno en este mismo orden: posición de cada clip en la cola.
  const queue = useMemo(() => sorted.filter((c) => c.status === 'pending').map((c) => c.id), [sorted])
  return (
    <div>
      <div className="section-head" style={{ padding: '10px 16px 6px' }}>
        <span className="label">Clips detectados</span>
        <span className="dim" style={{ fontSize: 12 }}>{clips.filter((c) => c.status === 'ready').length} listos de {clips.length}{clips.some((c) => c.status === 'rendering' || c.status === 'pending') ? ' · se generan de uno en uno' : ''}</span>
      </div>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th style={{ width: 44 }} />
              <th>Título</th>
              <th>Puntuación</th>
              <th className="hide-sm col-opt">Entrada</th>
              <th className="hide-sm col-opt">Duración</th>
              <th className="hide-sm hide-md col-opt2">Señales</th>
              <th>Estado</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr className="empty-row"><td colSpan={8}>
                {status && BUSY.has(status) ? 'Analizando: los clips aparecerán aquí.' : status === 'live' ? 'Esperando momentos destacados…' : 'Sin clips todavía.'}
              </td></tr>
            )}
            {sorted.map((c) => (
              <tr key={c.id}>
                <td>{c.status === 'ready' && c.files?.thumb
                  ? <img className="thumb-v" src={fileUrl(c.id, 'thumb')} alt="" loading="lazy" />
                  : <div className="thumb-v empty">9:16</div>}</td>
                <td><div className="clip-title" title={c.title}>{c.title}</div></td>
                <td>
                  <div className="score">
                    <span className="mono" style={{ width: 22, textAlign: 'right' }}>{c.score.toFixed(0)}</span>
                    <div className="score-bar"><div style={{ width: `${Math.min(100, c.score)}%` }} /></div>
                  </div>
                </td>
                <td className="mono hide-sm col-opt">{timecode(c.start)}</td>
                <td className="mono hide-sm col-opt">{shortDuration(c.end - c.start)}</td>
                <td className="muted hide-sm hide-md col-opt2" style={{ whiteSpace: 'nowrap' }}>{c.signals_label}</td>
                <td>
                  <ClipStatus clip={c} queuePos={c.status === 'pending' ? queue.indexOf(c.id) + 1 : undefined} />
                  {c.status === 'rendering' && <div className="row-bar"><div style={{ width: `${Math.round((c.progress || 0) * 100)}%` }} /></div>}
                </td>
                <td className="num"><button className="btn small" onClick={() => onOpen(c)}>Abrir</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SignalsPanel({ detail }: { detail: SourceDetail | null }) {
  const s = detail?.signals
  const local = detail?.kind === 'local'
  return (
    <div className="section">
      <div className="section-head"><span className="label">Señales</span></div>
      <div className="section-body">
        <MiniHist label="Mensajes de chat" values={s?.chat_rate} unit="msg/s"
          na={local ? 'No disponible en archivo local' : detail?.platform === 'kick' ? 'Chat de Kick: pendiente' : detail?.kind === 'vod' && !detail.was_live ? 'Sin chat (no fue un directo)' : 'No disponible'} />
        <MiniHist label="Nivel de audio" values={s?.audio_db} unit="dB" format={(v) => v.toFixed(0)} />
        <MiniHist label="Risas" values={s?.laughter} format={(v) => v.toFixed(2)} na={detail ? "Sin risas detectadas" : "No disponible"} />
        <MiniHist label="Espectadores" values={s?.viewers} format={(v) => Math.round(v).toLocaleString('es-ES')}
          na={local ? 'No disponible en archivo local' : detail?.kind === 'vod' ? 'Solo durante el directo' : 'Esperando datos…'} />
      </div>
    </div>
  )
}

function PermissionPanel({ detail, onChange }: { detail: SourceDetail; onChange: () => void }) {
  const set = async (p: string) => { await api.setPermission(detail.id, p); onChange() }
  return (
    <div className="section">
      <div className="section-head"><span className="label">Permiso del streamer</span></div>
      <div className="section-body">
        <div className="kv" style={{ marginBottom: 8 }}>
          <span>Canal</span><span>{detail.channel || '—'}</span>
        </div>
        <select className="select" style={{ width: '100%' }} value={detail.permission} onChange={(e) => set(e.target.value)} aria-label="Permiso del streamer">
          <option value="unknown">Sin marcar</option>
          <option value="yes">Sí, tengo permiso</option>
          <option value="program">Programa de clips</option>
          <option value="no">No</option>
        </select>
        <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>Solo genera un aviso en los clips nuevos; nunca los bloquea.</div>
      </div>
    </div>
  )
}

