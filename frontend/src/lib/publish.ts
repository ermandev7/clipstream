// Publicación en TikTok (Content Posting API · Direct Post).

export type Privacy = 'PUBLIC_TO_EVERYONE' | 'MUTUAL_FOLLOW_FRIENDS' | 'FOLLOWER_OF_CREATOR' | 'SELF_ONLY'
export const PRIVACY_LABEL: Record<Privacy, string> = {
  PUBLIC_TO_EVERYONE: 'Todo el mundo',
  MUTUAL_FOLLOW_FRIENDS: 'Amigos (seguidores mutuos)',
  FOLLOWER_OF_CREATOR: 'Seguidores',
  SELF_ONLY: 'Solo yo',
}

export interface TikTokStatus {
  configured: boolean
  client_key_hint: string
  redirect_uri: string
  connected: boolean
  scope: string
  open_id: string
  daily_cap: number
  last_24h: number
  music_url: string
  bc_policy_url: string
}

export interface CreatorInfo {
  creator_avatar_url?: string
  creator_username?: string
  creator_nickname?: string
  privacy_level_options: Privacy[]
  comment_disabled: boolean
  duet_disabled: boolean
  stitch_disabled: boolean
  max_video_post_duration_sec?: number
}

export type PostStatus = 'scheduled' | 'queued' | 'uploading' | 'processing' | 'published' | 'failed' | 'cancelled'
export interface Post {
  id: number
  clip_id: number | null
  platform: string
  status: PostStatus
  settings: { title?: string; privacy_level?: Privacy; account?: string }
  scheduled_at: number | null
  published_at: number | null
  progress: number
  post_id: string | null
  error: string | null
  created_at: number
  clip_title?: string
}

export interface PostBody {
  clip_id: number
  title: string
  privacy_level: Privacy | null
  allow_comment: boolean
  allow_duet: boolean
  allow_stitch: boolean
  commercial: boolean
  brand_organic: boolean
  brand_content: boolean
  is_aigc: boolean
  consent: boolean
  scheduled_at: number | null
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const d = (await r.json()).detail
      msg = typeof d === 'string' ? d : d?.message ?? msg
    } catch { /* sin cuerpo JSON */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const POST_LABEL: Record<PostStatus, string> = {
  scheduled: 'Programado', queued: 'En cola', uploading: 'Subiendo', processing: 'Procesando en TikTok',
  published: 'Publicado', failed: 'Error', cancelled: 'Cancelado',
}

export const publishApi = {
  status: () => req<TikTokStatus>('/api/publish/tiktok'),
  setKeys: (client_key: string, client_secret: string) =>
    req<TikTokStatus>('/api/publish/tiktok/keys', { method: 'PUT', body: JSON.stringify({ client_key, client_secret }) }),
  connect: () => req<{ url: string }>('/api/publish/tiktok/connect', { method: 'POST', body: JSON.stringify({ open_browser: true }) }),
  disconnect: () => req<TikTokStatus>('/api/publish/tiktok/disconnect', { method: 'POST' }),
  creator: () => req<CreatorInfo>('/api/publish/tiktok/creator'),
  post: (body: PostBody) => req<Post>('/api/publish/tiktok/posts', { method: 'POST', body: JSON.stringify(body) }),
  posts: () => req<Post[]>('/api/publish/posts'),
  cancel: (id: number) => req<{ ok: boolean }>(`/api/publish/posts/${id}`, { method: 'DELETE' }),
}
