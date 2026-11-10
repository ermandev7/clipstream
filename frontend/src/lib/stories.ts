// API de la pantalla Microseries.

export type TemplateId = 'biblicas' | 'mitologia' | 'fabulas' | 'leyendas' | 'frutinovela'
export type Lang = 'es' | 'en'
export type PhaseId = 'guion' | 'personajes' | 'imagenes' | 'voz' | 'montaje'
export type PhaseState = 'pending' | 'running' | 'done' | 'blocked' | 'error'
export type SceneStatus = 'pending' | 'review' | 'approved' | 'generating' | 'blocked'

export interface Template { label: string; style: string; tone: string; music_mood: string; quote_rule?: string; original?: boolean }
export interface Story {
  id: string; template: TemplateId; title: string; source: string; basis: string
  episodes: number; characters?: string[]; refs?: string[]; avoid?: string[]
}
export interface Finding { check: string; ok: boolean; message: string; where?: string; suggestion?: string }

export interface Series {
  id: number; name: string; template: TemplateId; mode: 'narrated' | 'dramatized'; languages: Lang[]
  story_id: string | null; synopsis: string; status: string; created_at: number; updated_at: number
  bible: { episodes?: { number: number; title: string; summary: string }[]; style?: string; tone?: string; voices?: Partial<Record<Lang, string>>; music?: string | null }
  rights: { story?: Finding }
  episode_count?: number; planned_episodes?: number
}
export interface Character { id: number; name: string; role: string; description: string; kind?: string; visual: string; voice: string }
export interface Episode {
  id: number; series_id: number; number: number; title: string | null; summary: string | null
  status: string; stage: string | null; phases: Partial<Record<PhaseId, PhaseState>>
  rights: { blocked?: boolean; findings?: Finding[] } | null
  files: { videos?: Partial<Record<Lang, string>>; report?: string; duration?: number } | null
  error: string | null
}
export interface SeriesDetail extends Series {
  characters: Character[]; episodes: Episode[]; template_info: Template; story: Story | null
}
export interface ImageCheck { ok: boolean; message: string; match: string; prob: number; suggestion?: string }
export interface Scene {
  id: number; episode_id: number; idx: number; narration_es: string; narration_en: string; image_prompt: string
  characters: string[]; duration: number; status: SceneStatus; block_reason: string | null; suggestion: string | null
  files: {
    image?: { png: string; seed: number; attempt: number; check: ImageCheck; fidelity?: { ok: boolean; message: string; score: number; top: string } }
    voice?: Partial<Record<Lang, { wav: string; duration: number; words: { start: number; end: number; text: string }[] }>>
  } | null
}
export interface EpisodeDetail extends Episode { scenes: Scene[] }
export interface VoiceOption { id: string; label: string }

export class BlockedError extends Error {
  suggestion: string
  constructor(message: string, suggestion: string) {
    super(message)
    this.suggestion = suggestion
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!r.ok) {
    let detail: unknown = `${r.status} ${r.statusText}`
    try { detail = (await r.json()).detail ?? detail } catch { /* sin cuerpo JSON */ }
    if (detail && typeof detail === 'object' && (detail as { blocked?: boolean }).blocked) {
      const d = detail as { message: string; suggestion: string }
      throw new BlockedError(d.message, d.suggestion)
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return r.json() as Promise<T>
}

const B = '/api/stories'
export const storiesApi = {
  catalog: () => req<{ templates: Record<TemplateId, Template>; stories: Story[] }>(`${B}/catalog`),
  list: () => req<Series[]>(`${B}/series`),
  get: (id: number) => req<SeriesDetail>(`${B}/series/${id}`),
  create: (body: { template: TemplateId; story_id?: string | null; custom_title?: string; name?: string; languages: Lang[]; episodes: number }) =>
    req<SeriesDetail>(`${B}/series`, { method: 'POST', body: JSON.stringify(body) }),
  remove: (id: number) => req<{ ok: boolean }>(`${B}/series/${id}`, { method: 'DELETE' }),
  voices: () => req<{ available: boolean; voices: Record<Lang, VoiceOption[]>; default: Record<Lang, string> }>(`${B}/voices`),
  setVoices: (id: number, v: Partial<Record<Lang, string>>) => req<SeriesDetail>(`${B}/series/${id}/voices`, { method: 'PUT', body: JSON.stringify(v) }),
  setMusic: (id: number, track_id: string | null) => req<SeriesDetail>(`${B}/series/${id}/music`, { method: 'PUT', body: JSON.stringify({ track_id }) }),
  produce: (id: number, n: number) => req<{ ok: boolean }>(`${B}/series/${id}/episodes/${n}/produce`, { method: 'POST' }),
  script: (id: number, n: number) => req<{ ok: boolean }>(`${B}/series/${id}/episodes/${n}`, { method: 'POST' }),
  episode: (id: number) => req<EpisodeDetail>(`${B}/episodes/${id}`),
  images: (id: number) => req<{ ok: boolean }>(`${B}/episodes/${id}/images`, { method: 'POST' }),
  voice: (id: number) => req<{ ok: boolean }>(`${B}/episodes/${id}/voice`, { method: 'POST' }),
  assemble: (id: number) => req<{ ok: boolean }>(`${B}/episodes/${id}/assemble`, { method: 'POST' }),
  report: async (id: number) => {
    const r = await fetch(`${B}/episodes/${id}/report`)
    if (!r.ok) throw new Error('El informe se genera al montar el episodio')
    return r.text()
  },
  editScene: (id: number, body: Partial<Pick<Scene, 'narration_es' | 'narration_en' | 'image_prompt' | 'status'>>) =>
    req<EpisodeDetail>(`${B}/scenes/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  rewriteScene: (id: number) => req<{ ok: boolean }>(`${B}/scenes/${id}/regenerate`, { method: 'POST' }),
  reimageScene: (id: number) => req<{ ok: boolean }>(`${B}/scenes/${id}/image`, { method: 'POST' }),
  imageUrl: (s: Scene) => `${B}/scenes/${s.id}/image.png?v=${s.files?.image?.seed ?? 0}`,
  voiceUrl: (s: Scene, lang: Lang) => `${B}/scenes/${s.id}/voice/${lang}.wav?v=${s.files?.voice?.[lang]?.duration ?? 0}`,
  videoUrl: (e: Episode, lang: Lang) => `${B}/episodes/${e.id}/video/${lang}.mp4?v=${e.files?.duration ?? 0}`,
}

export const PHASES: { id: PhaseId; label: string }[] = [
  { id: 'guion', label: 'Guion' }, { id: 'personajes', label: 'Personajes' }, { id: 'imagenes', label: 'Imágenes' },
  { id: 'voz', label: 'Voz' }, { id: 'montaje', label: 'Montaje' },
]
export const BUSY = new Set(['scripting', 'illustrating', 'voicing', 'assembling'])
export const SCENE_LABEL: Record<SceneStatus, string> = {
  approved: 'Aprobada', review: 'Revisar', generating: 'Generando', pending: 'Pendiente', blocked: 'Bloqueada',
}
export const CHECK_LABEL: Record<string, string> = {
  historia: 'Historia de dominio público', franquicias: 'Franquicias y personajes protegidos', letras: 'Letras de canciones',
  citas: 'Citas textuales', imagenes: 'Parecido en imágenes',
}
