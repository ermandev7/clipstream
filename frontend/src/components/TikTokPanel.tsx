import { useCallback, useEffect, useState } from 'react'
import { useWs } from '../lib/live'
import type { CreatorInfo, Post, TikTokStatus } from '../lib/publish'
import { POST_LABEL, publishApi } from '../lib/publish'

/** Conexión con TikTok: credenciales de tu app de desarrollador, inicio de sesión y últimas publicaciones. */
export function TikTokPanel({ onStatus }: { onStatus?: (s: TikTokStatus | null) => void }) {
  const [st, setSt] = useState<TikTokStatus | null>(null)
  const [creator, setCreator] = useState<CreatorInfo | null>(null)
  const [posts, setPosts] = useState<Post[]>([])
  const [keyIn, setKeyIn] = useState('')
  const [secretIn, setSecretIn] = useState('')
  const [editKeys, setEditKeys] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [waiting, setWaiting] = useState(false)

  const load = useCallback(async () => {
    try {
      const s = await publishApi.status()
      setSt(s)
      onStatus?.(s)
      if (s.connected) setCreator(await publishApi.creator().catch(() => null))
      else setCreator(null)
    } catch (e) { setError((e as Error).message) }
    publishApi.posts().then(setPosts).catch(() => {})
  }, [onStatus])
  useEffect(() => { load() }, [load])
  useWs((m) => {
    const msg = m as unknown as { type: string; post?: Post; connected?: boolean; error?: string }
    if (msg.type === 'tiktok') {
      setWaiting(false)
      if (msg.error) setError(msg.error)
      load()
    } else if (msg.type === 'post' && msg.post) {
      const p = msg.post
      setPosts((l) => (l.some((x) => x.id === p.id) ? l.map((x) => (x.id === p.id ? { ...x, ...p } : x)) : [p, ...l]))
    }
  })

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn() } catch (e) { setError((e as Error).message) }
  }
  const saveKeys = () => act(async () => {
    const s = await publishApi.setKeys(keyIn, secretIn)
    setSt(s)
    onStatus?.(s)
    setKeyIn('')
    setSecretIn('')
    setEditKeys(false)
  })

  if (!st) return null
  const showKeys = !st.configured || editKeys
  return (
    <div className="section">
      <div className="section-head"><span className="label">TikTok</span>
        {st.connected ? <span className="status st-ok"><span className="dot" />Conectado</span> : <span className="dim" style={{ fontSize: 12 }}>Sin conectar</span>}
      </div>
      <div className="section-body tiktok-panel">
        {showKeys && (
          <>
            <div className="dim">Datos de tu app en <a href="https://developers.tiktok.com/apps" target="_blank" rel="noreferrer">developers.tiktok.com</a>. Se guardan cifrados en este PC.</div>
            <label className="field"><span className="label">Client Key</span>
              <input className="input mono" value={keyIn} onChange={(e) => setKeyIn(e.target.value)} autoComplete="off" spellCheck={false} /></label>
            <label className="field"><span className="label">Client Secret</span>
              <input className="input mono" type="password" value={secretIn} onChange={(e) => setSecretIn(e.target.value)} autoComplete="off" /></label>
            <div className="field"><span className="label">Redirect URI (regístrala tal cual en Login Kit)</span>
              <div className="copy-row"><input className="input mono" readOnly value={st.redirect_uri} />
                <button className="btn small" onClick={() => navigator.clipboard?.writeText(st.redirect_uri)}>Copiar</button></div></div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn primary small" disabled={!keyIn.trim() || !secretIn.trim()} onClick={saveKeys}>Guardar</button>
              {st.configured && <button className="btn small" onClick={() => setEditKeys(false)}>Cancelar</button>}
            </div>
          </>
        )}
        {!showKeys && !st.connected && (
          <>
            <div className="muted">App configurada <span className="mono dim">({st.client_key_hint})</span>.</div>
            <button className="btn primary" disabled={waiting} onClick={() => act(async () => { setWaiting(true); await publishApi.connect() })}>
              {waiting ? 'Esperando a TikTok…' : 'Conectar con TikTok'}
            </button>
            {waiting && <div className="dim">Se ha abierto TikTok en tu navegador: inicia sesión y autoriza ClipStream. Esta pantalla se actualizará sola.</div>}
            <button className="btn link" onClick={() => setEditKeys(true)}>Cambiar Client Key / Secret</button>
          </>
        )}
        {st.connected && (
          <>
            <div className="account">
              {creator?.creator_avatar_url ? <img src={creator.creator_avatar_url} alt="" /> : <span className="avatar-empty" />}
              <div style={{ minWidth: 0 }}>
                <div className="account-name">{creator?.creator_nickname ?? 'Cuenta conectada'}</div>
                {creator?.creator_username && <div className="dim mono">@{creator.creator_username}</div>}
              </div>
              <button className="btn small" style={{ marginLeft: 'auto' }} onClick={() => act(async () => { const s = await publishApi.disconnect(); setSt(s); onStatus?.(s) })}>Desconectar</button>
            </div>
            <div className="dim">Publicaciones en 24 h: <span className="mono">{st.last_24h} / {st.daily_cap}</span> (límite de TikTok)</div>
          </>
        )}
        {error && <div className="notice error">{error}</div>}
        {posts.length > 0 && (
          <div className="post-list">
            <div className="label" style={{ marginTop: 6 }}>Publicaciones</div>
            {posts.slice(0, 12).map((p) => (
              <div key={p.id} className="post-row">
                <span className="post-title" title={p.settings?.title}>{p.clip_title || p.settings?.title || `Clip ${p.clip_id}`}</span>
                <span className={`post-st ${p.status}`} title={p.error ?? ''}>
                  {POST_LABEL[p.status]}{p.status === 'uploading' ? ` ${Math.round((p.progress || 0) * 100)} %` : ''}
                  {p.status === 'scheduled' && p.scheduled_at ? ` · ${new Date(p.scheduled_at * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' })}` : ''}
                </span>
                {(p.status === 'scheduled' || p.status === 'queued') && (
                  <button className="btn small" onClick={() => act(async () => { await publishApi.cancel(p.id); load() })}>Cancelar</button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
