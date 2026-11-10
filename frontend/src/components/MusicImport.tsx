import { useEffect, useState } from 'react'
import type { MusicTrack } from '../lib/api'
import { api, musicApi } from '../lib/api'

const LICENSE_HELP: Record<string, string> = {
  CC0: 'Dominio público: sin crédito obligatorio.',
  'CC-BY': 'Exige crédito: se añadirá automáticamente a la descripción.',
  Pixabay: 'Licencia de contenido de Pixabay.',
  'YouTube-AudioLibrary': 'Biblioteca de audio de YouTube (pistas sin atribución obligatoria).',
}

export function MusicImport({ licenses, onClose, onImported }: {
  licenses: string[]
  onClose: () => void
  onImported: (t: MusicTrack) => void
}) {
  const musicLicenses = licenses.filter((l) => !['Apache-2.0', 'MIT', 'OFL'].includes(l))
  const [path, setPath] = useState('')
  const [license, setLicense] = useState(musicLicenses[0] ?? 'CC0')
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const pick = async () => {
    try {
      const p = await api.pickFile('audio')
      if (p) {
        setPath(p)
        if (!title) setTitle(p.split(/[\\/]/).pop()!.replace(/\.[^.]+$/, ''))
      }
    } catch (e) { setError((e as Error).message) }
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      onImported(await musicApi.import({ path, license, title, author, source }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ width: 'min(520px, 100%)' }} role="dialog" aria-modal="true" aria-label="Importar música" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>Importar música</strong>
          <button className="btn small" style={{ marginLeft: 'auto' }} onClick={onClose}>Cerrar</button>
        </div>
        <div style={{ padding: 14, display: 'grid', gap: 12 }}>
          <div className="muted" style={{ fontSize: 12 }}>
            Solo se aceptan pistas gratuitas con licencia de uso comercial. La licencia queda registrada y aparece en el informe de derechos.
          </div>
          <div className="kv">
            <span>Archivo</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <input className="input mono" style={{ flex: 1 }} readOnly value={path} placeholder="Ningún archivo" />
              <button className="btn" onClick={pick}>Abrir ubicación…</button>
            </span>
            <span>Licencia</span>
            <select className="select" value={license} onChange={(e) => setLicense(e.target.value)}>
              {musicLicenses.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <span />
            <span className="dim" style={{ fontSize: 12 }}>{LICENSE_HELP[license] ?? ''}</span>
            <span>Título</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
            <span>Autor</span>
            <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder={license === 'CC-BY' ? 'Obligatorio para CC-BY' : 'Opcional'} />
            <span>Fuente (URL)</span>
            <input className="input mono" value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://…" />
          </div>
          {error && <div className="notice error">{error}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button className="btn" onClick={onClose}>Cancelar</button>
            <button className="btn primary" disabled={!path || busy || (license === 'CC-BY' && !author.trim())} onClick={submit}>
              {busy ? 'Importando…' : 'Importar'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
