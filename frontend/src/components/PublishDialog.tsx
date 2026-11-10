import { useEffect, useMemo, useState } from 'react'
import type { Clip } from '../lib/api'
import { fileUrl } from '../lib/api'
import type { CreatorInfo, Privacy, TikTokStatus } from '../lib/publish'
import { PRIVACY_LABEL, publishApi } from '../lib/publish'

/**
 * Ventana de publicación en TikTok según sus normas de integración (Content Sharing Guidelines):
 * cuenta de destino visible, vista previa, privacidad elegida a mano y SIN valor por defecto, interacciones
 * desmarcadas (y bloqueadas si la cuenta las tiene desactivadas), divulgación de contenido comercial,
 * declaración de uso de música y aviso de procesado. Nada se envía a TikTok hasta pulsar «Publicar».
 */
function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export function PublishDialog({ clip, status, index, total, suggestedAt, onDone, onClose }: {
  clip: Clip
  status: TikTokStatus
  index: number
  total: number
  suggestedAt: number | null
  onDone: (scheduledAt: number | null) => void
  onClose: () => void
}) {
  const [creator, setCreator] = useState<CreatorInfo | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const defaultTitle = useMemo(() => {
    const tags = (clip.hashtags ?? []).map((t) => (t.startsWith('#') ? t : `#${t.replace(/\s+/g, '')}`)).join(' ')
    return [clip.title, tags].filter(Boolean).join(' ').trim()
  }, [clip])
  const [title, setTitle] = useState(defaultTitle)
  const [privacy, setPrivacy] = useState<Privacy | ''>('')
  const [comment, setComment] = useState(false)
  const [duet, setDuet] = useState(false)
  const [stitch, setStitch] = useState(false)
  const [commercial, setCommercial] = useState(false)
  const [organic, setOrganic] = useState(false)
  const [branded, setBranded] = useState(false)
  const [aigc, setAigc] = useState(false)
  const [when, setWhen] = useState<'now' | 'later'>(suggestedAt ? 'later' : 'now')
  const [at, setAt] = useState(toLocalInput(suggestedAt ?? Date.now() / 1000 + 1800))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Se pide cada vez que se abre la ventana: TikTok no permite usar datos guardados de la cuenta.
    publishApi.creator().then(setCreator).catch((e) => setLoadError((e as Error).message))
  }, [clip.id])

  const duration = clip.end - clip.start
  const tooLong = !!creator?.max_video_post_duration_sec && duration > creator.max_video_post_duration_sec + 0.5
  const titleLen = [...title].length
  useEffect(() => { if (branded && privacy === 'SELF_ONLY') setPrivacy('') }, [branded, privacy])

  const commercialIncomplete = commercial && !organic && !branded
  const label = commercial && branded ? 'Tu vídeo se etiquetará como «Colaboración pagada»'
    : commercial && organic ? 'Tu vídeo se etiquetará como «Contenido promocional»' : null
  const scheduledAt = when === 'later' ? new Date(at).getTime() / 1000 : null
  const canPost = !!creator && !!privacy && !tooLong && !commercialIncomplete && titleLen <= 2200 && !busy &&
    (scheduledAt == null || scheduledAt > Date.now() / 1000 - 60)

  const submit = async () => {
    if (!privacy) return
    setBusy(true)
    setError(null)
    try {
      await publishApi.post({
        clip_id: clip.id, title, privacy_level: privacy, allow_comment: comment, allow_duet: duet, allow_stitch: stitch,
        commercial, brand_organic: organic, brand_content: branded, is_aigc: aigc, consent: true, scheduled_at: scheduledAt,
      })
      onDone(scheduledAt)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const music = <a href={status.music_url} target="_blank" rel="noreferrer">Confirmación de uso de música</a>
  const bc = <a href={status.bc_policy_url} target="_blank" rel="noreferrer">Política de contenido de marca</a>
  return (
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal publish-modal" role="dialog" aria-modal="true" aria-label="Publicar en TikTok" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>Publicar en TikTok</strong>
          {total > 1 && <span className="dim">Clip {index + 1} de {total}</span>}
          <button className="btn small" style={{ marginLeft: 'auto' }} disabled={busy} onClick={onClose}>Cerrar</button>
        </div>
        <div className="modal-body">
          <div className="modal-video">
            <video src={fileUrl(clip.id, 'mp4')} controls preload="metadata" poster={clip.files?.thumb ? fileUrl(clip.id, 'thumb') : undefined} />
          </div>
          <div className="modal-info publish-form">
            {!creator && !loadError && <div className="muted">Consultando tu cuenta de TikTok…</div>}
            {loadError && <div className="notice error">{loadError}</div>}
            {creator && (
              <>
                <div className="account">
                  {creator.creator_avatar_url ? <img src={creator.creator_avatar_url} alt="" /> : <span className="avatar-empty" />}
                  <div><div className="dim" style={{ fontSize: 12 }}>Se publicará en la cuenta</div>
                    <div className="account-name">{creator.creator_nickname}{creator.creator_username ? <span className="dim mono"> @{creator.creator_username}</span> : null}</div></div>
                </div>
                {tooLong && <div className="notice error">Este clip dura {Math.round(duration)} s y tu cuenta solo puede publicar vídeos de hasta {creator.max_video_post_duration_sec} s.</div>}
                {clip.warnings?.length > 0 && (
                  <div className="notice">Aviso de derechos: {clip.warnings.map((w) => w.message).join(' · ')}. Publica solo contenido que tengas derecho a usar.</div>
                )}

                <label className="field"><span className="label">Texto de la publicación</span>
                  <textarea className="input area" rows={4} value={title} onChange={(e) => setTitle(e.target.value)} />
                  <span className={`dim mono ${titleLen > 2200 ? 'st-live' : ''}`} style={{ fontSize: 11, alignSelf: 'flex-end' }}>{titleLen} / 2200</span></label>

                <label className="field"><span className="label">¿Quién puede ver este vídeo?</span>
                  <select className="select" value={privacy} onChange={(e) => setPrivacy(e.target.value as Privacy)}>
                    <option value="" disabled>Elige una opción…</option>
                    {creator.privacy_level_options.map((p) => (
                      <option key={p} value={p} disabled={branded && p === 'SELF_ONLY'}
                        title={branded && p === 'SELF_ONLY' ? 'El contenido de marca no puede ser privado.' : undefined}>
                        {PRIVACY_LABEL[p] ?? p}
                      </option>
                    ))}
                  </select></label>

                <div className="field"><span className="label">Permitir a los usuarios</span>
                  <div className="checks-row">
                    <label className="check" title={creator.comment_disabled ? 'Desactivado en los ajustes de tu cuenta' : undefined}>
                      <input type="checkbox" checked={comment} disabled={creator.comment_disabled} onChange={(e) => setComment(e.target.checked)} />Comentar</label>
                    <label className="check" title={creator.duet_disabled ? 'Desactivado en los ajustes de tu cuenta' : undefined}>
                      <input type="checkbox" checked={duet} disabled={creator.duet_disabled} onChange={(e) => setDuet(e.target.checked)} />Dúo</label>
                    <label className="check" title={creator.stitch_disabled ? 'Desactivado en los ajustes de tu cuenta' : undefined}>
                      <input type="checkbox" checked={stitch} disabled={creator.stitch_disabled} onChange={(e) => setStitch(e.target.checked)} />Stitch</label>
                  </div></div>

                <div className="field">
                  <label className="check toggle-row"><input type="checkbox" checked={commercial} onChange={(e) => { setCommercial(e.target.checked); if (!e.target.checked) { setOrganic(false); setBranded(false) } }} />
                    <span><b>Divulgar contenido comercial</b><span className="dim" style={{ display: 'block', fontSize: 12 }}>Indica si este vídeo promociona productos, servicios o marcas a cambio de algo de valor.</span></span></label>
                  {commercial && (
                    <div className="commercial">
                      <label className="check"><input type="checkbox" checked={organic} onChange={(e) => setOrganic(e.target.checked)} />
                        <span><b>Tu marca</b><span className="dim" style={{ display: 'block', fontSize: 12 }}>Promocionas tu propio negocio o marca personal.</span></span></label>
                      <label className="check"><input type="checkbox" checked={branded} onChange={(e) => setBranded(e.target.checked)} />
                        <span><b>Contenido de marca</b><span className="dim" style={{ display: 'block', fontSize: 12 }}>Promocionas a otra marca o a un tercero.</span></span></label>
                      {label && <div className="notice">{label}</div>}
                    </div>
                  )}
                </div>

                <label className="check"><input type="checkbox" checked={aigc} onChange={(e) => setAigc(e.target.checked)} />Marcar como contenido generado con IA</label>

                <div className="field"><span className="label">Cuándo</span>
                  <div className="checks-row">
                    <label className="check"><input type="radio" name="when" checked={when === 'now'} onChange={() => setWhen('now')} />Ahora</label>
                    <label className="check"><input type="radio" name="when" checked={when === 'later'} onChange={() => setWhen('later')} />Programar</label>
                    {when === 'later' && <input className="input mono" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />}
                  </div>
                  {when === 'later' && <span className="dim" style={{ fontSize: 12 }}>ClipStream tiene que estar abierto a esa hora. Antes de subir se vuelve a comprobar tu cuenta.</span>}
                </div>

                <div className="declaration">
                  {commercial && branded
                    ? <>Al publicar, aceptas la {bc} y la {music} de TikTok.</>
                    : <>Al publicar, aceptas la {music} de TikTok.</>}
                </div>
                <div className="dim" style={{ fontSize: 12 }}>Tras publicar, el vídeo puede tardar unos minutos en procesarse y aparecer en tu perfil.</div>
                {error && <div className="notice error">{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button className="btn" disabled={busy} onClick={onClose}>{total > 1 ? 'Saltar' : 'Cancelar'}</button>
                  <span title={commercialIncomplete ? 'Debes indicar si tu contenido promociona tu marca, a un tercero o ambos.' : !privacy ? 'Elige quién puede ver el vídeo' : undefined}>
                    <button className="btn primary" disabled={!canPost} onClick={submit}>
                      {busy ? 'Enviando…' : when === 'later' ? 'Programar publicación' : 'Publicar'}
                    </button>
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
