export type SourceKind = 'live' | 'vod' | 'local'
export type SourceStatus = 'pending' | 'downloading' | 'analyzing' | 'live' | 'rendering' | 'done' | 'stopped' | 'error'
export type ClipStatus = 'pending' | 'rendering' | 'ready' | 'error'

export interface Warning { kind: string; message: string }

export interface Clip {
  id: number
  source_id: number
  start: number
  end: number
  peak: number
  score: number
  signal_score: number
  llm_score: number | null
  reason: string
  title: string
  hook: string
  hashtags: string[]
  signals_label: string
  warnings: Warning[]
  status: ClipStatus
  progress: number
  error: string | null
  files: Record<string, string>
  hidden?: number
  created_at: number
  source_title?: string
  source_kind?: SourceKind
}

export interface Signals {
  duration: number
  offset: number
  bins: number
  activity: number[] | null
  audio_db: number[] | null
  chat_rate: number[] | null
  laughter: number[] | null
  viewers: number[] | null
}

export interface LogEvent { id?: number; source_id: number | null; ts: number; level: string; message: string }

export interface Source {
  id: number
  kind: SourceKind
  platform: string | null
  url: string | null
  path: string | null
  title: string
  channel: string
  status: SourceStatus
  progress: number
  stage: string
  stage_progress?: number
  duration: number
  was_live: number
  has_chat: number
  error: string | null
  created_at: number
  clip_count?: number
}

export interface SourceDetail extends Source {
  signals: Signals | null
  clips: Clip[]
  events: LogEvent[]
  permission: string
}

export interface StorageSource {
  id: number; kind: SourceKind; title: string; status: SourceStatus; created_at: number | null; clips: number
  total_bytes: number; clips_bytes: number; original_bytes: number; busy: boolean
}
export interface StorageReport { sources: StorageSource[]; total_bytes: number }

export function fmtBytes(b: number): string {
  if (b >= 1e9) return `${(b / 1e9).toLocaleString('es-ES', { maximumFractionDigits: 1 })} GB`
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`
  return `${Math.max(0, Math.round(b / 1e3))} KB`
}

export type CleanMode = 'ask' | 'clean' | 'keep'
export function getCleanMode(): CleanMode {
  try {
    const v = localStorage.getItem('clipstream.cleanMode')
    if (v === 'clean' || v === 'keep') return v
  } catch { /* sin almacenamiento */ }
  return 'ask'
}
export function setCleanMode(m: CleanMode) {
  try { localStorage.setItem('clipstream.cleanMode', m) } catch { /* sin almacenamiento */ }
}

export interface GpuStatus {
  name: string
  used_gb: number
  total_gb: number
  available: boolean
  active_model: string | null
  busy: boolean
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { msg = (await r.json()).detail ?? msg } catch { /* sin cuerpo JSON */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  status: () => req<{ gpu: GpuStatus; ollama: boolean; llm_model: string; whisper_model: string }>('/api/system/status'),
  sources: () => req<Source[]>('/api/sources'),
  source: (id: number) => req<SourceDetail>(`/api/sources/${id}`),
  storage: () => req<StorageReport>('/api/sources/storage'),
  cleanup: (body: { ids?: number[]; keep?: number[] }) =>
    req<{ deleted: number; freed_bytes: number; skipped: number; errors?: string[] }>('/api/sources/cleanup', { method: 'POST', body: JSON.stringify(body) }),
  createSource: (body: { kind: SourceKind; platform?: string; url?: string; path?: string; clean_previous?: boolean; live_every?: number }) =>
    req<Source>('/api/sources', { method: 'POST', body: JSON.stringify(body) }),
  stopSource: (id: number) => req<{ ok: boolean }>(`/api/sources/${id}/stop`, { method: 'POST' }),
  reprocess: (id: number) => req<Source>(`/api/sources/${id}/reprocess`, { method: 'POST' }),
  deleteSource: (id: number) => req<{ ok: boolean }>(`/api/sources/${id}`, { method: 'DELETE' }),
  setPermission: (id: number, permission: string) =>
    req<{ ok: boolean }>(`/api/sources/${id}/permission`, { method: 'POST', body: JSON.stringify({ permission }) }),
  clips: () => req<Clip[]>('/api/clips'),
  pickFile: async (kind: 'video' | 'audio' = 'video'): Promise<string | null> => {
    // En la ventana de escritorio se usa el diálogo nativo de pywebview; en el navegador, el del servidor.
    const pw = (window as unknown as { pywebview?: { api?: { pick_file?: (k: string) => Promise<string | null> } } }).pywebview
    if (pw?.api?.pick_file) return pw.api.pick_file(kind)
    return (await req<{ path: string | null }>(`/api/system/pick-file?kind=${kind}`, { method: 'POST' })).path
  },
  /** Quitar clips de la lista de Exportar (o volver a mostrarlos). No borra archivos. */
  hideClips: (ids: number[], hidden: boolean) =>
    req<{ ok: boolean }>('/api/clips/hide', { method: 'POST', body: JSON.stringify({ ids, hidden }) }),
  /** En la ventana de escritorio no hay descargas de navegador: se elige carpeta y se copian los archivos. */
  isDesktop: () => !!(window as unknown as { pywebview?: unknown }).pywebview,
  pickFolder: async (start?: string | null): Promise<string | null> =>
    (await req<{ path: string | null }>('/api/system/pick-folder', { method: 'POST', body: JSON.stringify({ start: start ?? null }) })).path,
  saveClips: (ids: number[], dest: string, include: Record<string, boolean>) =>
    req<{ ok: boolean; files: number; clips: number; dest: string }>('/api/clips/save', {
      method: 'POST', body: JSON.stringify({ ids, dest, ...include }),
    }),
  openFolder: (path: string) => req<{ ok: boolean }>('/api/system/open-folder', { method: 'POST', body: JSON.stringify({ path }) }),
  zip: async (ids: number[], include: Record<string, boolean>): Promise<void> => {
    const r = await fetch('/api/clips/zip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, ...include }),
    })
    if (!r.ok) {
      let msg = r.statusText
      try { msg = (await r.json()).detail ?? msg } catch { /* sin cuerpo JSON */ }
      throw new Error(msg)
    }
    const blob = await r.blob()
    saveBlob(blob, 'clipstream_clips.zip')
  },
}

export type SaveResult = { dest: string; clips: number } | 'browser' | null

/**
 * Guardar clips en el PC. En la ventana de escritorio (pywebview bloquea las descargas del navegador) se elige
 * carpeta con el diálogo de Windows y se copian los archivos; recuerda la última carpeta. En un navegador
 * normal se descarga: un MP4 suelto si es un clip y solo el vídeo, si no un ZIP.
 * Devuelve null si el usuario cancela el diálogo.
 */
export async function saveClipsToPc(ids: number[], include: Record<string, boolean>): Promise<SaveResult> {
  if (api.isDesktop()) {
    let last: string | null = null
    try { last = localStorage.getItem('clipstream.saveDir') } catch { /* sin almacenamiento */ }
    const dest = await api.pickFolder(last)
    if (!dest) return null
    try { localStorage.setItem('clipstream.saveDir', dest) } catch { /* sin almacenamiento */ }
    const r = await api.saveClips(ids, dest, include)
    return { dest: r.dest, clips: r.clips }
  }
  const kinds = Object.entries(include).filter(([, on]) => on).map(([k]) => k)
  if (ids.length === 1 && kinds.length === 1) {
    const a = document.createElement('a')
    a.href = fileUrl(ids[0], kinds[0], true)
    a.download = ''
    document.body.appendChild(a)
    a.click()
    a.remove()
    return 'browser'
  }
  await api.zip(ids, include)
  return 'browser'
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export const fileUrl = (clipId: number, kind: string, download = false) =>
  `/api/clips/${clipId}/file/${kind}${download ? '?download=true' : ''}`

export function timecode(sec: number, frames = false): string {
  const s = Math.max(0, sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = Math.floor(s % 60)
  const base = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
  return frames ? `${base}:${String(Math.floor((s % 1) * 30)).padStart(2, '0')}` : base
}

export function shortDuration(sec: number): string {
  if (sec < 60) return `${sec.toFixed(1)} s`
  return `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('es-ES', { hour12: false })
}

// ---- Editor y Recorte ------------------------------------------------------

export type Layout = 'follow' | 'facecam' | 'blur' | 'split2' | 'split3' | 'group' | 'speaker'

export interface CropBox { id?: string; x: number; y: number; w: number; h: number; include: boolean }
export interface TrackingCfg { smoothing?: number; dead_zone?: number; max_speed?: number; cut?: boolean }
export interface CropSettings { mode: 'auto' | 'manual'; layout?: Layout | null; boxes?: CropBox[]; tracking?: TrackingCfg }

export interface PlanShot {
  start: number
  end: number
  layout: Layout
  reason: string
  people: { id: number; kind: string; confidence: number; presence: number; speaking_s?: number; box: { x: number; y: number; w: number; h: number } }[]
  window_w: number
}
export interface ClipPlan { layout: Layout; src_w: number; src_h: number; crop: number[] | null; manual?: boolean; shots: PlanShot[] }

export type SubLang = 'orig' | 'es' | 'en' | 'both'
export type SfxMode = 'none' | 'auto' | 'subtle' | 'meme'
export type FxMode = 'none' | 'subtle' | 'dynamic'
export interface ClipSettings { style?: string; hook_seconds?: number; crop?: CropSettings; sfx?: SfxMode; fx?: FxMode; zoom?: boolean; music?: string; music_gain?: number; sub_lang?: SubLang; cover_t?: number; cover_text?: string }
export interface ClipMeta { title_en?: string; description_es?: string; description_en?: string; hashtags?: string[]; platforms?: Record<string, string[]>; cover_auto_t?: number }
export interface EditableClip extends Clip { layout: string; settings: ClipSettings; rendered: Record<string, unknown>; meta: ClipMeta }

export interface ClipDetail {
  clip: EditableClip
  source: { id: number; kind: SourceKind; title: string; channel: string; url: string | null; platform: string | null; preset: CropSettings | Record<string, never> }
  words: { t: number; e: number; w: string }[]
  plan: ClipPlan | null
  wave: number[]
  audio: AudioPlan | null
  language: string
  crop: CropSettings
  limits: { min: number; max: number }
  dirty: boolean
}

export interface EditorOptions {
  layouts: { id: string; label: string; available: boolean }[]
  styles: { id: string; label: string }[]
  music: MusicTrack[]
  licenses: string[]
  clip_min: number
  clip_max: number
}

export interface ClipPatchBody {
  title?: string
  hashtags?: string[]
  hook?: string
  start?: number
  end?: number
  title_en?: string
  description_es?: string
  description_en?: string
  settings?: { style?: string; hook_seconds?: number; crop?: CropSettings; clear_crop?: boolean; sfx?: SfxMode; fx?: FxMode; zoom?: boolean; music?: string; music_gain?: number; sub_lang?: SubLang; cover_t?: number; cover_text?: string; cover_auto?: boolean }
}

export const editorApi = {
  options: () => req<EditorOptions>('/api/editor/options'),
  detail: (id: number) => req<ClipDetail>(`/api/clips/${id}/detail`),
  patch: (id: number, body: ClipPatchBody) => req<ClipDetail>(`/api/clips/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  render: (id: number) => req<{ ok: boolean }>(`/api/clips/${id}/render`, { method: 'POST' }),
  savePreset: (sourceId: number, crop: CropSettings, applyAll: boolean, fromClip?: number) =>
    req<{ ok: boolean; rerendered: number }>(`/api/sources/${sourceId}/preset`, {
      method: 'POST', body: JSON.stringify({ crop, apply_all: applyAll, from_clip: fromClip ?? null }),
    }),
  frameUrl: (id: number, t: number) => `/api/clips/${id}/frame?t=${t.toFixed(1)}`,
  cover: (id: number) => req<ClipDetail>(`/api/clips/${id}/cover`, { method: 'POST' }),
  metadata: (id: number) => req<{ ok: boolean }>(`/api/clips/${id}/metadata`, { method: 'POST' }),
}

// ---- Audio (bloque 2.3) -----------------------------------------------------

export interface MusicTrack { id: string; title: string; author: string; license: string; source: string; allowed: boolean }
export interface AudioPlan {
  zoom_at: number | null
  sfx: { t: number; sound: string; gain_db: number; reason?: string }[]
  fx?: { push: number; events: { t: number; kind: 'punch' | 'shake' | 'flash' | 'pulse'; reason?: string }[] }
  music: { id: string; gain_db: number } | null
}

export const musicApi = {
  import: (body: { path: string; license: string; title: string; author: string; source: string }) =>
    req<MusicTrack>('/api/music/import', { method: 'POST', body: JSON.stringify(body) }),
}
