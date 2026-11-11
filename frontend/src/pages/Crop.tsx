import { useEffect, useMemo, useRef, useState } from 'react'
import { LayoutIcon } from '../components/LayoutIcon'
import { Segmented } from '../components/Segmented'
import type { ClipDetail, CropBox, CropSettings, Layout } from '../lib/api'
import { editorApi, fileUrl, shortDuration } from '../lib/api'
import { PERSON_COLORS, cropStyle, manualPanels, personLetter } from '../lib/geometry'
import { SideResizer } from '../components/Resizers'

const LAYOUTS: { id: Layout; label: string }[] = [
  { id: 'follow', label: 'Seguir cara' },
  { id: 'facecam', label: 'Facecam + juego' },
  { id: 'blur', label: 'Desenfocado' },
  { id: 'split2', label: 'Dividido ×2' },
  { id: 'split3', label: 'Dividido ×3' },
  { id: 'speaker', label: 'Quien habla' },
  { id: 'react', label: 'Streamer + vídeo' },
]

interface Props { clipId: number | null; onNavigate: (tab: 'sources' | 'editor', clipId?: number) => void }

function boxesFromPlan(detail: ClipDetail, t: number): CropBox[] {
  const shot = detail.plan?.shots.find((s) => t >= s.start && t < s.end) ?? detail.plan?.shots[0]
  const v = shot?.video
  const inVideo = (p: { box: { x: number; y: number; w: number; h: number } }) => !!v
    && p.box.x + p.box.w / 2 >= v.x && p.box.x + p.box.w / 2 <= v.x + v.w && p.box.y + p.box.h / 2 >= v.y && p.box.y + p.box.h / 2 <= v.y + v.h
  // Con vídeo insertado: primero su caja (arriba) y después las personas de fuera del vídeo.
  const videoBox: CropBox[] = v ? [{ id: 'v', include: true, x: v.x, y: v.y, w: v.w, h: v.h }] : []
  const people = (shot?.people ?? []).filter((p) => !inVideo(p)).slice(0, v ? 2 : 3)
  return [...videoBox, ...people.map((p, i) => {
    // La caja manual es la zona visible de la persona: cara ampliada (hombros incluidos).
    const h = Math.min(0.95, p.box.h * 4)
    const w = Math.min(0.9, Math.max(p.box.w * 3, h * 0.5))
    const cx = p.box.x + p.box.w / 2
    const cy = p.box.y + p.box.h / 2 + h * 0.2
    return {
      id: `p${i}`, include: true,
      x: Math.min(Math.max(cx - w / 2, 0), 1 - w), y: Math.min(Math.max(cy - h / 2, 0), 1 - h), w, h,
    }
  })]
}

function speakingLabel(s?: number): string {
  if (s == null) return '—'
  return s > 0 ? `${s.toFixed(1)} s` : '0 s'
}

export function CropPage({ clipId, onNavigate }: Props) {
  const [detail, setDetail] = useState<ClipDetail | null>(null)
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [layout, setLayout] = useState<Layout | null>(null)
  const [boxes, setBoxes] = useState<CropBox[]>([])
  const [smoothing, setSmoothing] = useState(0.6)
  const [deadZone, setDeadZone] = useState(0.12)
  const [cut, setCut] = useState(false)
  const [applyAll, setApplyAll] = useState(false)
  const [t, setT] = useState(2)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const frameRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (clipId == null) return
    setDetail(null)
    editorApi.detail(clipId).then((d) => {
      setDetail(d)
      const c = d.crop
      setMode(c.mode ?? 'auto')
      setLayout((c.layout as Layout) ?? (c.mode === 'manual' ? 'follow' : null))
      setSmoothing(c.tracking?.smoothing ?? 0.6)
      setDeadZone(c.tracking?.dead_zone ?? 0.12)
      setCut(c.tracking?.cut ?? false)
      const start = Math.min(2, (d.clip.end - d.clip.start) / 2)
      setT(start)
      setBoxes(c.boxes?.length ? c.boxes : boxesFromPlan(d, start))
    }).catch((e) => setError(e.message))
  }, [clipId])

  const srcW = detail?.plan?.src_w ?? 1920
  const srcH = detail?.plan?.src_h ?? 1080
  const frame = detail ? editorApi.frameUrl(detail.clip.id, t) : ''
  const effLayout: Layout = layout ?? ((detail?.clip.layout as Layout) || 'follow')
  const panels = useMemo(() => (mode === 'manual' ? manualPanels(effLayout, boxes, srcW, srcH) : null), [mode, effLayout, boxes, srcW, srcH])

  if (clipId == null) {
    return (
      <div className="page"><div className="placeholder"><div className="box">
        <div className="label" style={{ marginBottom: 8 }}>Recorte vertical</div>
        <div className="muted">Abre un clip en el Editor y pulsa «Editar recorte manual».</div>
        <button className="btn" style={{ marginTop: 14 }} onClick={() => onNavigate('sources')}>Ir a Fuentes</button>
      </div></div></div>
    )
  }
  if (!detail) return <div className="page"><div className="placeholder">{error ? <div className="notice error">{error}</div> : <span className="muted">Cargando…</span>}</div></div>

  const dur = detail.clip.end - detail.clip.start
  const busyClip = detail.clip.status === 'pending' || detail.clip.status === 'rendering'

  const dragBox = (i: number, kind: 'move' | 'resize') => (e: React.PointerEvent) => {
    if (mode !== 'manual') return
    e.preventDefault()
    e.stopPropagation()
    setSelected(i)
    const r = frameRef.current!.getBoundingClientRect()
    const x0 = e.clientX
    const y0 = e.clientY
    const b0 = boxes[i]
    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - x0) / r.width
      const dy = (ev.clientY - y0) / r.height
      setBoxes((bs) => bs.map((b, j) => {
        if (j !== i) return b
        if (kind === 'move') {
          return { ...b, x: Math.min(Math.max(b0.x + dx, 0), 1 - b0.w), y: Math.min(Math.max(b0.y + dy, 0), 1 - b0.h) }
        }
        return { ...b, w: Math.min(Math.max(b0.w + dx, 0.04), 1 - b0.x), h: Math.min(Math.max(b0.h + dy, 0.06), 1 - b0.y) }
      }))
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const addBox = () => {
    if (boxes.length >= 5) return
    setBoxes((bs) => [...bs, { id: `m${Date.now()}`, x: 0.35, y: 0.15, w: 0.3, h: 0.7, include: true }])
    setSelected(boxes.length)
  }
  const removeBox = (i: number) => { setBoxes((bs) => bs.filter((_, j) => j !== i)); setSelected(null) }
  const redetect = () => setBoxes(boxesFromPlan(detail, t))

  const settings = (): CropSettings => ({
    mode,
    layout: mode === 'manual' ? effLayout : layout,
    boxes: mode === 'manual' ? boxes.map((b) => ({ ...b })) : [],
    tracking: { smoothing, dead_zone: deadZone, cut },
  })

  const save = async () => {
    setError(null)
    if (mode === 'manual' && !panels && effLayout !== 'blur' && effLayout !== 'speaker') {
      setError('Ese diseño necesita más cajas incluidas (dividido ×2: 2, ×3: 3; facecam: la webcam primero; streamer + vídeo: el vídeo primero y después el streamer).')
      return
    }
    setBusy(true)
    try {
      if (applyAll) {
        await editorApi.savePreset(detail.source.id, settings(), true)
      } else {
        await editorApi.patch(detail.clip.id, { settings: { crop: settings() } })
        await editorApi.render(detail.clip.id)
      }
      onNavigate('editor', detail.clip.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const positionLabel = (i: number): string => {
    if (mode !== 'manual' || !boxes[i]?.include) return '—'
    if (effLayout === 'facecam') {
      const inc = boxes.filter((b) => b.include)
      const k = inc.indexOf(boxes[i])
      return k === 0 ? 'Arriba (webcam)' : k === 1 ? 'Abajo (juego)' : 'Sin usar'
    }
    if (effLayout === 'react') {
      const inc = boxes.filter((b) => b.include)
      const k = inc.indexOf(boxes[i])
      return k === 0 ? 'Arriba (vídeo)' : k === 1 ? 'Abajo (streamer)' : 'Sin usar'
    }
    if (effLayout === 'split2' || effLayout === 'split3') {
      const n = effLayout === 'split2' ? 2 : 3
      const order = boxes.map((b, j) => ({ b, j })).filter((o) => o.b.include).sort((p, q) => p.b.x - q.b.x).slice(0, n)
      const k = order.findIndex((o) => o.j === i)
      return k < 0 ? 'Sin usar' : n === 2 ? ['Arriba', 'Abajo'][k] : ['Arriba', 'Centro', 'Abajo'][k]
    }
    if (effLayout === 'blur') return 'Plano general'
    return 'En el encuadre'
  }

  const autoPeople = (detail.plan?.shots.find((s) => t >= s.start && t < s.end) ?? detail.plan?.shots[0])?.people ?? []

  return (
    <div className="page">
      <div className="bar">
        <strong>Recorte vertical</strong><span className="muted hide-sm">· preset del directo</span>
        <Segmented value={mode} onChange={(v) => { setMode(v); if (v === 'manual' && !boxes.length) redetect(); if (v === 'manual' && !layout) setLayout(effLayout) }}
          options={[{ value: 'auto', label: 'Automático' }, { value: 'manual', label: 'Manual' }]} />
        <label className="check hide-sm"><input type="checkbox" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} />Aplicar a todos los clips de este directo</label>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn" onClick={() => onNavigate('editor', detail.clip.id)}>Volver al editor</button>
          <button className="btn primary" disabled={busy || busyClip} onClick={save}>{busy ? 'Guardando…' : 'Guardar y renderizar'}</button>
        </span>
      </div>
      {error && <div style={{ padding: '8px 16px 0' }}><div className="notice error">{error}</div></div>}

      <div className="workspace">
        <div className="main-col">
          <div className="crop-stage">
            <div className="crop-frame" ref={frameRef} style={{ aspectRatio: `${srcW} / ${srcH}` }} onPointerDown={() => setSelected(null)}>
              <img src={frame} alt="Fotograma de la fuente" draggable={false} />
              {mode === 'manual' && panels && panels.map((p, i) => (
                <div key={`r${i}`} className="crop-region" style={{ left: `${p.rect.x * 100}%`, top: `${p.rect.y * 100}%`, width: `${p.rect.w * 100}%`, height: `${p.rect.h * 100}%` }} />
              ))}
              {mode === 'manual' && boxes.map((b, i) => (
                <div key={b.id ?? i} className={`crop-box ${selected === i ? 'sel' : ''} ${b.include ? '' : 'excluded'}`}
                  style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%`, borderColor: PERSON_COLORS[i] }}
                  onPointerDown={dragBox(i, 'move')}>
                  <span className="crop-tag" style={{ background: PERSON_COLORS[i] }}>{personLetter(i)}</span>
                  <span className="crop-resize" style={{ background: PERSON_COLORS[i] }} onPointerDown={dragBox(i, 'resize')} />
                </div>
              ))}
              {mode === 'auto' && autoPeople.map((p, i) => (
                <div key={p.id} className="crop-box readonly" style={{ left: `${p.box.x * 100}%`, top: `${p.box.y * 100}%`, width: `${p.box.w * 100}%`, height: `${p.box.h * 100}%`, borderColor: PERSON_COLORS[i] }}>
                  <span className="crop-tag" style={{ background: PERSON_COLORS[i] }}>{personLetter(i)}</span>
                </div>
              ))}
            </div>
            <div className="crop-scrub">
              <span className="mono dim">{t.toFixed(1)} s</span>
              <input type="range" min={0} max={Math.max(0.1, dur - 0.1)} step={0.5} value={t} onChange={(e) => setT(Number(e.target.value))} aria-label="Fotograma" />
              <span className="mono dim">{shortDuration(dur)}</span>
              {mode === 'manual' && <>
                <button className="btn small" onClick={addBox} disabled={boxes.length >= 5}>Añadir caja</button>
                <button className="btn small" onClick={redetect} title="Cajas a partir de las personas detectadas en este fotograma">Detectar personas</button>
              </>}
            </div>
          </div>

          <div className="section-head" style={{ padding: '10px 16px 6px' }}><span className="label">Personas</span>
            <span className="dim" style={{ fontSize: 12 }}>{mode === 'manual' ? 'Arrastra las cajas; esquina inferior para redimensionar' : 'Detectadas automáticamente en este plano'}</span></div>
          <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>Persona</th><th>Confianza</th><th className="hide-sm">Tiempo hablando</th><th>Posición en el vertical</th><th>Incluir</th><th /></tr></thead>
              <tbody>
                {mode === 'manual' && boxes.length === 0 && <tr className="empty-row"><td colSpan={6}>Sin cajas. Pulsa «Añadir caja» o «Detectar personas».</td></tr>}
                {mode === 'manual' && boxes.map((b, i) => (
                  <tr key={b.id ?? i} className={selected === i ? 'selected' : ''} onClick={() => setSelected(i)}>
                    <td><span className="swatch" style={{ background: PERSON_COLORS[i] }} />{personLetter(i)}</td>
                    <td className="mono">{autoPeople[i] ? autoPeople[i].confidence.toFixed(2) : 'manual'}</td>
                    <td className="mono hide-sm">{speakingLabel(autoPeople[i]?.speaking_s)}</td>
                    <td>{positionLabel(i)}</td>
                    <td><label className="check"><input type="checkbox" checked={b.include} onChange={(e) => setBoxes((bs) => bs.map((x, j) => (j === i ? { ...x, include: e.target.checked } : x)))} /></label></td>
                    <td className="num"><button className="btn small" onClick={(e) => { e.stopPropagation(); removeBox(i) }}>Quitar</button></td>
                  </tr>
                ))}
                {mode === 'auto' && autoPeople.length === 0 && <tr className="empty-row"><td colSpan={6}>No se detectan personas en este plano.</td></tr>}
                {mode === 'auto' && autoPeople.map((p, i) => (
                  <tr key={p.id}>
                    <td><span className="swatch" style={{ background: PERSON_COLORS[i] }} />{personLetter(i)}</td>
                    <td className="mono">{p.confidence.toFixed(2)}</td>
                    <td className="mono hide-sm">{speakingLabel(p.speaking_s)}</td>
                    <td className="muted">Automático</td>
                    <td><label className="check"><input type="checkbox" checked disabled /></label></td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <SideResizer />
        <aside className="side">
          <div className="section">
            <div className="section-head"><span className="label">Diseño</span>
              {mode === 'auto' && <button className={`btn link ${layout == null ? '' : ''}`} onClick={() => setLayout(null)}>{layout == null ? 'Automático' : 'Volver a automático'}</button>}
            </div>
            <div className="section-body layout-thumbs">
              {LAYOUTS.map((l) => (
                <button key={l.id} className={`thumb-btn ${(layout ?? (mode === 'manual' ? effLayout : null)) === l.id ? 'on' : ''}`}
                  aria-label={l.label} title={l.id === 'speaker' ? 'Sigue a quien habla (en manual se usa la detección automática)' : l.label}
                  onClick={() => setLayout(l.id as Layout)}>
                  <LayoutIcon id={l.id} size={44} />
                  <span>{l.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span className="label">Seguimiento</span></div>
            <div className="section-body kv">
              <span>Suavizado</span>
              <span className="range-row"><input type="range" min={0.2} max={2} step={0.1} value={smoothing} onChange={(e) => setSmoothing(Number(e.target.value))} /><span className="mono">{smoothing.toFixed(1)} s</span></span>
              <span>Zona muerta</span>
              <span className="range-row"><input type="range" min={0} max={0.3} step={0.01} value={deadZone} onChange={(e) => setDeadZone(Number(e.target.value))} /><span className="mono">{Math.round(deadZone * 100)} %</span></span>
              <span>Cambio de plano</span>
              <Segmented value={cut ? 'cut' : 'smooth'} onChange={(v) => setCut(v === 'cut')} options={[{ value: 'cut', label: 'Corte seco' }, { value: 'smooth', label: 'Suave' }]} />
            </div>
          </div>

          <div className="section" style={{ borderBottom: 0 }}>
            <div className="section-head"><span className="label">Vista previa 9:16</span></div>
            <div className="section-body" style={{ display: 'flex', justifyContent: 'center' }}>
              <div className="preview-916">
                {mode === 'manual' && panels && panels.map((p, i) => (
                  <div key={i} style={{ height: `${(p.panel[1] / 1920) * 100}%`, ...cropStyle(frame, p.rect) }} />
                ))}
                {mode === 'manual' && !panels && effLayout === 'blur' && <div className="preview-blur" style={{ backgroundImage: `url(${frame})` }} />}
                {mode === 'manual' && !panels && effLayout === 'speaker' && <div className="monitor-empty" style={{ fontSize: 12, padding: 12 }}>Quien habla se decide en cada momento con labios y voz; las cajas no se usan</div>}
                {mode === 'manual' && !panels && effLayout !== 'blur' && effLayout !== 'speaker' && <div className="monitor-empty" style={{ fontSize: 12 }}>Faltan cajas para este diseño</div>}
                {mode === 'auto' && (detail.clip.files?.png
                  ? <img src={`${fileUrl(detail.clip.id, 'png')}?v=${(detail.clip.rendered as { at?: number }).at ?? 0}`} alt="Último render" />
                  : <div className="monitor-empty" style={{ fontSize: 12 }}>Se decide al renderizar</div>)}
              </div>
            </div>
            {mode === 'auto' && <div className="section-body dim" style={{ fontSize: 12 }}>En automático se elige el diseño en cada plano de cámara. La vista previa es el último render.</div>}
          </div>
        </aside>
      </div>
    </div>
  )
}
