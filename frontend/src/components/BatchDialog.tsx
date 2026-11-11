import { useCallback, useEffect, useState } from 'react'
import { fileUrl } from '../lib/api'
import type { CreatorInfo, NextBatch, Privacy, PublishPrefs, TikTokStatus } from '../lib/publish'
import { BATCH_INTERVALS, PRIVACY_LABEL, publishApi } from '../lib/publish'

/**
 * «Aprobar tanda»: la siguiente tanda de clips (los mejores primero), cada uno con su hora, texto editable y su
 * propia casilla de aprobación. Cumple las normas de TikTok para apps (Content Sharing Guidelines): cuenta visible,
 * vista previa, privacidad elegida a mano y sin valor por defecto, interacciones desmarcadas, divulgación comercial
 * y declaración de música. Solo se programan los clips aprobados uno a uno.
 */
function when(ts: number): string {
  const d = new Date(ts * 1000)
  const today = new Date()
  const day0 = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day0(d) - day0(today)) / 86400000)
  const hm = d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
  const day = diff === 0 ? 'Hoy' : diff === 1 ? 'Mañana' : d.toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric', month: 'short' })
  return `${day} ${hm}`
}

function defaultTitle(c: { title: string; hashtags?: string[] }): string {
  const tags = (c.hashtags ?? []).map((t) => (t.startsWith('#') ? t : `#${t.replace(/\s+/g, '')}`)).join(' ')
  return [c.title, tags].filter(Boolean).join(' ').trim()
}

export function BatchDialog({ status, sourceId, onClose, onScheduled }: {
  status: TikTokStatus
  sourceId?: number | null
  onClose: () => void
  onScheduled?: () => void
}) {
  const [creator, setCreator] = useState<CreatorInfo | null>(null)
  const [batch, setBatch] = useState<NextBatch | null>(null)
  const [titles, setTitles] = useState<Record<number, string>>({})
  const [approved, setApproved] = useState<Set<number>>(new Set())
  const [privacy, setPrivacy] = useState<Privacy | ''>('')
  const [comment, setComment] = useState(false)
  const [duet, setDuet] = useState(false)
  const [stitch, setStitch] = useState(false)
  const [commercial, setCommercial] = useState(false)
  const [organic, setOrganic] = useState(false)
  const [branded, setBranded] = useState(false)
  const [aigc, setAigc] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [tanda, setTanda] = useState(1)

  const load = useCallback(() => {
    publishApi.nextBatch(sourceId).then((b) => {
      setBatch(b)
      setTitles(Object.fromEntries(b.items.map((it) => [it.clip.id, defaultTitle(it.clip)])))
      setApproved(new Set()) // cada clip se aprueba a mano
    }).catch((e) => setError((e as Error).message))
  }, [sourceId])

  useEffect(() => {
    // La cuenta se consulta cada vez que se abre: TikTok no permite usar datos guardados.
    publishApi.creator().then(setCreator).catch((e) => setError((e as Error).message))
    load()
  }, [load])
  useEffect(() => { if (branded && privacy === 'SELF_ONLY') setPrivacy('') }, [branded, privacy])

  const savePrefs = async (p: Partial<PublishPrefs>) => {
    setError(null)
    try { await publishApi.savePrefs(p); load() } catch (e) { setError((e as Error).message) }
  }

  const items = batch?.items ?? []
  const maxDur = creator?.max_video_post_duration_sec
  const tooLong = (id: number) => {
    const c = items.find((it) => it.clip.id === id)?.clip
    return !!c && !!maxDur && c.end - c.start > maxDur + 0.5
  }
  const toggle = (id: number) => setApproved((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const commercialIncomplete = commercial && !organic && !branded
  const chosen = items.filter((it) => approved.has(it.clip.id) && !tooLong(it.clip.id))
  const canPost = !!creator && !!privacy && !commercialIncomplete && chosen.length > 0 && !busy &&
    chosen.every((it) => [...(titles[it.clip.id] ?? '')].length <= 2200)

  const submit = async () => {
    if (!privacy) return
    setBusy(true)
    setError(null)
    try {
      const r = await publishApi.postBatch(
        { privacy_level: privacy, allow_comment: comment, allow_duet: duet, allow_stitch: stitch, commercial,
          brand_organic: organic, brand_content: branded, is_aigc: aigc, consent: true },
        chosen.map((it) => ({ clip_id: it.clip.id, title: titles[it.clip.id] ?? '', scheduled_at: it.scheduled_at })))
      onScheduled?.()
      if (r.errors.length) setError(r.errors.map((e) => `Clip ${e.clip_id}: ${e.error}`).join(' · '))
      setDone(`${r.created.length === 1 ? '1 publicación programada' : `${r.created.length} publicaciones programadas`} (tanda ${tanda}).`)
      setTanda((t) => t + 1)
      load() // la siguiente tanda (días siguientes), por si quieres aprobarla ya
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const music = <a href={status.music_url} target="_blank" rel="noreferrer">Confirmación de uso de música</a>
  const bc = <a href={status.bc_policy_url} target="_blank" rel="noreferrer">Política de contenido de marca</a>
  const prefs = batch?.prefs
  return (
    <div className="modal-back" onClick={() => !busy && onClose()}>
      <div className="modal batch-modal" role="dialog" aria-modal="true" aria-label="Aprobar tanda de TikTok" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>Aprobar tanda {tanda}</strong>
          {batch && <span className="dim">{items.length} clips{batch.remaining > 0 ? ` · quedan ${batch.remaining} para las próximas tandas` : ''}</span>}
          <button className="btn small" style={{ marginLeft: 'auto' }} disabled={busy} onClick={onClose}>Cerrar</button>
        </div>
        {prefs && (
          <div className="batch-prefs">
            <label className="inline-field"><span className="dim">Vídeos al día</span>
              <select className="select" value={prefs.per_day} onChange={(e) => savePrefs({ per_day: Number(e.target.value) })}>
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => <option key={n} value={n}>{n}</option>)}
              </select></label>
            <label className="inline-field"><span className="dim">Uno cada</span>
              <select className="select" value={prefs.interval_min} onChange={(e) => savePrefs({ interval_min: Number(e.target.value) })}>
                {BATCH_INTERVALS.map((m) => <option key={m} value={m}>{m < 60 ? `${m} min` : m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`}</option>)}
              </select></label>
            <label className="inline-field"><span className="dim">Desde las</span>
              <input className="input mono" type="time" value={prefs.start} onChange={(e) => e.target.value && savePrefs({ start: e.target.value })} /></label>
            <span className="dim" style={{ fontSize: 12 }}>Los clips restantes pasan a los días siguientes. ClipStream debe estar abierto a esas horas.</span>
          </div>
        )}
        <div className="batch-body">
          <div className="batch-list">
            {!batch && !error && <div className="muted" style={{ padding: 14 }}>Preparando la tanda…</div>}
            {batch && items.length === 0 && (
              <div className="muted" style={{ padding: 14 }}>{done ? 'No quedan clips por programar.' : 'No hay clips listos sin publicar.'}</div>
            )}
            {items.map((it, i) => {
              const c = it.clip
              const long = tooLong(c.id)
              return (
                <div key={c.id} className={`batch-row ${approved.has(c.id) ? 'on' : ''}`}>
                  <video src={fileUrl(c.id, 'mp4')} poster={c.files?.thumb ? fileUrl(c.id, 'thumb') : undefined} controls preload="none" />
                  <div className="batch-row-main">
                    <div className="batch-row-head">
                      <span className="mono batch-when">{i + 1}. {when(it.scheduled_at)}</span>
                      <span className="dim mono" style={{ fontSize: 11 }}>{Math.round(c.score)}/100 · {Math.round(c.end - c.start)} s</span>
                    </div>
                    <textarea className="input area" rows={3} value={titles[c.id] ?? ''} aria-label={`Texto de la publicación ${i + 1}`}
                      onChange={(e) => setTitles((t) => ({ ...t, [c.id]: e.target.value }))} />
                    {long && <div className="notice error">Dura {Math.round(c.end - c.start)} s: tu cuenta solo admite hasta {maxDur} s.</div>}
                    {c.warnings?.length > 0 && <div className="notice">Aviso de derechos: {c.warnings.map((w) => w.message).join(' · ')}</div>}
                    <label className="check"><input type="checkbox" checked={approved.has(c.id)} disabled={long} onChange={() => toggle(c.id)} />
                      Apruebo publicar este clip</label>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="modal-info publish-form batch-side">
            {!creator && !error && <div className="muted">Consultando tu cuenta de TikTok…</div>}
            {creator && (
              <>
                <div className="account">
                  {creator.creator_avatar_url ? <img src={creator.creator_avatar_url} alt="" /> : <span className="avatar-empty" />}
                  <div><div className="dim" style={{ fontSize: 12 }}>Se publicará en la cuenta</div>
                    <div className="account-name">{creator.creator_nickname}{creator.creator_username ? <span className="dim mono"> @{creator.creator_username}</span> : null}</div></div>
                </div>
                <label className="field"><span className="label">¿Quién puede ver estos vídeos?</span>
                  <select className="select" value={privacy} onChange={(e) => setPrivacy(e.target.value as Privacy)}>
                    <option value="" disabled>Elige una opción…</option>
                    {creator.privacy_level_options.map((p) => (
                      <option key={p} value={p} disabled={branded && p === 'SELF_ONLY'}>{PRIVACY_LABEL[p] ?? p}</option>
                    ))}
                  </select></label>
                <div className="field"><span className="label">Permitir a los usuarios</span>
                  <div className="checks-row">
                    <label className="check"><input type="checkbox" checked={comment} disabled={creator.comment_disabled} onChange={(e) => setComment(e.target.checked)} />Comentar</label>
                    <label className="check"><input type="checkbox" checked={duet} disabled={creator.duet_disabled} onChange={(e) => setDuet(e.target.checked)} />Dúo</label>
                    <label className="check"><input type="checkbox" checked={stitch} disabled={creator.stitch_disabled} onChange={(e) => setStitch(e.target.checked)} />Stitch</label>
                  </div></div>
                <div className="field">
                  <label className="check toggle-row"><input type="checkbox" checked={commercial} onChange={(e) => { setCommercial(e.target.checked); if (!e.target.checked) { setOrganic(false); setBranded(false) } }} />
                    <span><b>Divulgar contenido comercial</b><span className="dim" style={{ display: 'block', fontSize: 12 }}>Si estos vídeos promocionan productos, servicios o marcas a cambio de algo de valor.</span></span></label>
                  {commercial && (
                    <div className="commercial">
                      <label className="check"><input type="checkbox" checked={organic} onChange={(e) => setOrganic(e.target.checked)} /><span><b>Tu marca</b></span></label>
                      <label className="check"><input type="checkbox" checked={branded} onChange={(e) => setBranded(e.target.checked)} /><span><b>Contenido de marca</b> (un tercero)</span></label>
                    </div>
                  )}
                </div>
                <label className="check"><input type="checkbox" checked={aigc} onChange={(e) => setAigc(e.target.checked)} />Marcar como contenido generado con IA</label>
                <div className="declaration">
                  {commercial && branded ? <>Al publicar, aceptas la {bc} y la {music} de TikTok.</> : <>Al publicar, aceptas la {music} de TikTok.</>}
                </div>
                {done && <div className="notice ok">{done}{items.length ? ' Abajo tienes la siguiente tanda.' : ''}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                  <button className="btn" disabled={busy || !items.length}
                    onClick={() => setApproved(new Set(items.filter((it) => !tooLong(it.clip.id)).map((it) => it.clip.id)))}>Marcar todos</button>
                  <span title={!privacy ? 'Elige quién puede ver los vídeos' : commercialIncomplete ? 'Indica si promocionas tu marca, a un tercero o ambos' : !chosen.length ? 'Aprueba al menos un clip' : undefined}>
                    <button className="btn primary" disabled={!canPost} onClick={submit}>
                      {busy ? 'Programando…' : `Programar ${chosen.length || ''} aprobado${chosen.length === 1 ? '' : 's'}`}
                    </button>
                  </span>
                </div>
              </>
            )}
            {error && <div className="notice error">{error}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
