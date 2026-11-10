import { useCallback, useRef } from 'react'

/** Tamaños de paneles que el usuario puede arrastrar; se recuerdan entre sesiones. */
const SIZES: Record<string, { def: number; min: number; max: () => number }> = {
  '--side-w': { def: 320, min: 260, max: () => Math.max(320, Math.min(900, window.innerWidth * 0.6)) },
  '--monitor-h': { def: 360, min: 0, max: () => Math.max(200, window.innerHeight * 0.7) },
}

function read(name: string): number {
  const s = SIZES[name]
  try {
    const v = Number(localStorage.getItem(`clipstream.size${name}`))
    if (v || v === 0) return Math.min(s.max(), Math.max(s.min, v || s.def))
  } catch { /* sin almacenamiento */ }
  return s.def
}

function apply(name: string, v: number) {
  document.documentElement.style.setProperty(name, `${Math.round(v)}px`)
}

function save(name: string, v: number) {
  try { localStorage.setItem(`clipstream.size${name}`, String(Math.round(v))) } catch { /* sin almacenamiento */ }
}

/** Aplica los tamaños guardados (al arrancar la interfaz). */
export function initLayoutSizes() {
  for (const name of Object.keys(SIZES)) apply(name, read(name))
}

function useDrag(name: string, axis: 'x' | 'y', sign: 1 | -1) {
  const start = useRef<{ pos: number; size: number } | null>(null)
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    start.current = { pos: axis === 'x' ? e.clientX : e.clientY, size: read(name) }
    document.body.classList.add(axis === 'x' ? 'resizing-x' : 'resizing-y')
  }, [axis, name])
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!start.current) return
    const s = SIZES[name]
    const delta = ((axis === 'x' ? e.clientX : e.clientY) - start.current.pos) * sign
    apply(name, Math.min(s.max(), Math.max(s.min, start.current.size + delta)))
  }, [axis, name, sign])
  const onPointerUp = useCallback(() => {
    if (!start.current) return
    start.current = null
    document.body.classList.remove('resizing-x', 'resizing-y')
    const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)) || SIZES[name].def
    save(name, v)
  }, [name])
  const onDoubleClick = useCallback(() => {
    apply(name, SIZES[name].def)
    save(name, SIZES[name].def)
  }, [name])
  return { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onDoubleClick }
}

/** Separador vertical entre el contenido y el panel derecho: arrastrar para ensanchar el panel. */
export function SideResizer() {
  const handlers = useDrag('--side-w', 'x', -1)
  return <div className="side-resizer" role="separator" aria-orientation="vertical" aria-label="Ancho del panel derecho"
    title="Arrastra para cambiar el ancho · doble clic para restablecer" {...handlers} />
}

/** Separador horizontal bajo el monitor: arrastrar hacia arriba deja más sitio a los clips. */
export function MonitorResizer() {
  const handlers = useDrag('--monitor-h', 'y', 1)
  return <div className="h-resizer" role="separator" aria-orientation="horizontal" aria-label="Alto del monitor"
    title="Arrastra para dar más espacio a los clips o al vídeo · doble clic para restablecer" {...handlers}><span /></div>
}
