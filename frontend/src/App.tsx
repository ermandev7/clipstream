import { useCallback, useEffect, useState } from 'react'
import type { GpuStatus } from './lib/api'
import { api } from './lib/api'
import { useConnected, useWs } from './lib/live'
import { SetupPanel } from './components/SetupPanel'
import { CropPage } from './pages/Crop'
import { EditorPage } from './pages/Editor'
import { ExportPage } from './pages/Export'
import { SourcesPage } from './pages/Sources'

// Microseries (pages/Stories.tsx) desactivadas a petición del usuario: sin pestaña.
type Tab = 'sources' | 'editor' | 'crop' | 'export'
const TABS: { id: Tab; label: string; phase?: string }[] = [
  { id: 'sources', label: 'Fuentes' },
  { id: 'editor', label: 'Editor' },
  { id: 'crop', label: 'Recorte' },
  { id: 'export', label: 'Exportar' },
]
// Pantallas aún no disponibles (ninguna por ahora).
const PLANNED: Record<string, { title: string; text: string }> = {}

function initialTab(): Tab {
  // Enlace directo: ?tab=editor&clip=12 (también sirve para las capturas automáticas del README).
  const fromUrl = new URLSearchParams(location.search).get('tab') as Tab | null
  if (fromUrl && TABS.some((x) => x.id === fromUrl)) return fromUrl
  try {
    const t = localStorage.getItem('clipstream.tab') as Tab | null
    if (t && TABS.some((x) => x.id === t)) return t
  } catch { /* almacenamiento no disponible */ }
  return 'sources'
}

function initialClip(): number | null {
  const fromUrl = Number(new URLSearchParams(location.search).get('clip'))
  if (fromUrl) return fromUrl
  try {
    const v = localStorage.getItem('clipstream.clip')
    return v ? Number(v) : null
  } catch { return null }
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [clipId, setClipId] = useState<number | null>(initialClip)
  const [exportFocus, setExportFocus] = useState<number | null>(null)
  const clearExportFocus = useCallback(() => setExportFocus(null), [])
  const [gpu, setGpu] = useState<GpuStatus | null>(null)
  const connected = useConnected()

  useEffect(() => { api.status().then((s) => setGpu(s.gpu)).catch(() => {}) }, [])
  useEffect(() => { try { localStorage.setItem('clipstream.tab', tab) } catch { /* sin almacenamiento */ } }, [tab])
  useEffect(() => { try { if (clipId != null) localStorage.setItem('clipstream.clip', String(clipId)) } catch { /* sin almacenamiento */ } }, [clipId])

  const navigate = (t: Tab, id?: number) => {
    if (id != null) setClipId(id)
    if (t === 'export' && id != null) setExportFocus(id)
    setTab(t)
  }
  useWs((m) => { if (m.type === 'gpu') setGpu(m.gpu) })

  return (
    <div className="app">
      <header className="header">
        <span className="brand">ClipStream</span>
        <nav className="tabs" aria-label="Secciones">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)} aria-current={tab === t.id}>
              {t.label}{t.phase && <span className="phase">{t.phase}</span>}
            </button>
          ))}
        </nav>
        <div className="header-right mono">
          <SetupPanel />
          <span className={`conn-dot ${connected ? '' : 'off'}`} title={connected ? 'Conectado al motor' : 'Sin conexión con el motor'} />
          {gpu?.available
            ? <span><span className="gpu-name">{gpu.name} · </span>VRAM {gpu.used_gb.toFixed(1)} / {gpu.total_gb.toFixed(1)} GB</span>
            : <span>GPU no detectada</span>}
        </div>
      </header>
      {tab === 'sources' && <SourcesPage onOpenClip={(id) => navigate('editor', id)} />}
      {tab === 'editor' && <EditorPage clipId={clipId} onNavigate={navigate} />}
      {tab === 'crop' && <CropPage clipId={clipId} onNavigate={navigate} />}
      {tab === 'export' && <ExportPage onOpenClip={(id) => navigate('editor', id)} focusClipId={exportFocus} onFocused={clearExportFocus} />}
      {PLANNED[tab] && (
        <div className="page">
          <div className="placeholder">
            <div className="box">
              <div className="label" style={{ marginBottom: 8 }}>{PLANNED[tab].title}</div>
              <div className="muted">{PLANNED[tab].text}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
