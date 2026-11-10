import { useCallback, useEffect, useRef, useState } from 'react'
import { useWs } from '../lib/live'

interface SetupItem {
  id: string
  label: string
  size_mb: number
  required: boolean
  ok: boolean
  detail: string
  installable: boolean
  help_url: string | null
  progress: number | null
  error: string | null
}
interface SetupStatus { items: SetupItem[]; running: string | null; ready: boolean }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json' } })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText)
  return r.json()
}

const gb = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`)

/** Preparación del primer arranque: tapa la app mientras falte algo imprescindible. */
export function SetupPanel() {
  const [st, setSt] = useState<SetupStatus | null>(null)
  const [live, setLive] = useState<Record<string, { p: number; msg: string }>>({})
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    try { setSt(await call<SetupStatus>('/api/setup')) } catch { /* motor arrancando */ }
  }, [])
  useEffect(() => { load() }, [load])

  useWs((m) => {
    const msg = m as unknown as { type: string; id?: string; progress?: number; message?: string; done?: boolean }
    if (msg.type !== 'setup') return
    if (msg.id && msg.progress != null) setLive((l) => ({ ...l, [msg.id!]: { p: msg.progress!, msg: msg.message ?? '' } }))
    if (msg.done && !timer.current) {
      timer.current = window.setTimeout(() => { timer.current = null; load() }, 400)
    }
  })

  if (!st) return null
  const missingRequired = st.items.filter((i) => i.required && !i.ok)
  const optionalMissing = st.items.filter((i) => !i.required && !i.ok)
  const show = !st.ready || open
  const install = async (ids: string[]) => {
    setError(null)
    try { await call('/api/setup/install', { method: 'POST', body: JSON.stringify({ ids }) }); load() } catch (e) { setError((e as Error).message) }
  }

  if (!show) {
    return optionalMissing.length ? (
      <button className="setup-chip" onClick={() => setOpen(true)} title="La IA (títulos, puntuación, traducción) no está lista">IA no preparada</button>
    ) : null
  }
  const needMb = missingRequired.filter((i) => i.installable).reduce((a, i) => a + i.size_mb, 0)
  return (
    <div className="setup-back">
      <div className="setup-box" role="dialog" aria-modal="true" aria-label="Preparar ClipStream">
        <div className="modal-head">
          <strong>Preparar ClipStream</strong>
          {st.ready && <button className="btn small" style={{ marginLeft: 'auto' }} onClick={() => setOpen(false)}>Cerrar</button>}
        </div>
        <div style={{ padding: 14, display: 'grid', gap: 12 }}>
          <div className="muted" style={{ fontSize: 12 }}>
            {st.ready
              ? 'Todo lo imprescindible está listo. El LLM es opcional pero mejora puntuaciones, títulos y traducciones.'
              : 'La primera vez hay que descargar los componentes que no van en el instalador. Se guardan en tu carpeta de usuario y no se vuelven a descargar.'}
          </div>
          <table className="grid">
            <tbody>
              {st.items.map((i) => {
                const pr = st.running === i.id ? (live[i.id] ?? { p: i.progress ?? 0, msg: i.detail }) : null
                return (
                  <tr key={i.id}>
                    <td style={{ width: 18 }}><span className={`status ${i.ok ? 'st-ok' : i.required ? 'st-warn' : 'st-muted'}`}><span className="dot" /></span></td>
                    <td>
                      <div>{i.label}{!i.required && <span className="dim"> · opcional</span>}</div>
                      {pr
                        ? <div className="setup-progress"><div style={{ width: `${pr.p * 100}%` }} /><span className="mono">{pr.msg}</span></div>
                        : <div className={i.error ? 'st-live' : 'dim'} style={{ fontSize: 12 }}>{i.detail}{!i.ok && i.size_mb ? ` · ${gb(i.size_mb)}` : ''}</div>}
                    </td>
                    <td className="num" style={{ whiteSpace: 'nowrap' }}>
                      {!i.ok && i.installable && st.running !== i.id && (
                        <button className="btn small" disabled={!!st.running} onClick={() => install([i.id])}>Descargar</button>
                      )}
                      {!i.ok && i.help_url && (
                        <button className="btn small" onClick={() => call('/api/setup/open-ollama', { method: 'POST' })}>Descargar Ollama</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {error && <div className="notice error">{error}</div>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
            <button className="btn" onClick={load}>Comprobar de nuevo</button>
            {!st.ready && (
              <button className="btn primary" disabled={!!st.running || !missingRequired.some((i) => i.installable)}
                onClick={() => install(missingRequired.filter((i) => i.installable).map((i) => i.id))}>
                {st.running ? 'Descargando…' : `Descargar lo necesario (${gb(needMb)})`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
