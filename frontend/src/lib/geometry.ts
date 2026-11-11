// Misma geometría que app/clipper/reframe/planner.py y manual.py, para la vista previa del recorte.
import type { CropBox, Layout } from './api'

export interface Rect { x: number; y: number; w: number; h: number }

export const PANELS: Record<string, [number, number][]> = {
  facecam: [[1080, 768], [1080, 1152]],
  split2: [[1080, 960], [1080, 960]],
  split3: [[1080, 640], [1080, 640], [1080, 640]],
  react: [[1080, 608], [1080, 1312]],
}

export function windowWidth(srcW: number, srcH: number): number {
  return Math.min(1, (9 / 16) * srcH / srcW)
}

export function clampRect(r: Rect): Rect {
  const w = Math.min(r.w, 1)
  const h = Math.min(r.h, 1)
  return { x: Math.min(Math.max(r.x, 0), 1 - w), y: Math.min(Math.max(r.y, 0), 1 - h), w, h }
}

export function regionWithAspect(cx: number, cy: number, h: number, aspectPx: number, srcW: number, srcH: number): Rect {
  h = Math.min(h, 1)
  let w = aspectPx * h * srcH / srcW
  if (w > 1) {
    w = 1
    h = w * srcW / (aspectPx * srcH)
  }
  return clampRect({ x: cx - w / 2, y: cy - h / 2, w, h })
}

/** Paneles de salida (de arriba abajo) que produciría un recorte manual. null = no es válido. */
export function manualPanels(layout: Layout, boxes: CropBox[], srcW: number, srcH: number): { rect: Rect; panel: [number, number] }[] | null {
  const inc = boxes.filter((b) => b.include)
  if (layout === 'follow' || layout === 'group') {
    if (!inc.length) return null
    const ww = windowWidth(srcW, srcH)
    const left = Math.min(...inc.map((b) => b.x))
    const right = Math.max(...inc.map((b) => b.x + b.w))
    const cx = Math.min(Math.max((left + right) / 2, ww / 2), 1 - ww / 2)
    return [{ rect: { x: cx - ww / 2, y: 0, w: ww, h: 1 }, panel: [1080, 1920] }]
  }
  if (layout === 'split2' || layout === 'split3') {
    const k = layout === 'split2' ? 2 : 3
    const sorted = [...inc].sort((a, b) => a.x - b.x).slice(0, k)
    if (sorted.length < k) return null
    const [pw, ph] = PANELS[layout][0]
    return sorted.map((b) => ({ rect: regionWithAspect(b.x + b.w / 2, b.y + b.h / 2, Math.max(0.1, b.h), pw / ph, srcW, srcH), panel: [pw, ph] }))
  }
  if (layout === 'facecam') {
    if (!inc.length) return null
    const [[cw, ch], [gw, gh]] = PANELS.facecam
    const cam = regionWithAspect(inc[0].x + inc[0].w / 2, inc[0].y + inc[0].h / 2, Math.max(0.1, inc[0].h), cw / ch, srcW, srcH)
    const game = inc[1]
      ? regionWithAspect(inc[1].x + inc[1].w / 2, inc[1].y + inc[1].h / 2, Math.max(0.1, inc[1].h), gw / gh, srcW, srcH)
      : regionWithAspect(0.5, 0.5, 1, gw / gh, srcW, srcH)
    return [{ rect: cam, panel: [cw, ch] }, { rect: game, panel: [gw, gh] }]
  }
  if (layout === 'react') {
    // Primera caja: el vídeo insertado (el mayor 16:9 dentro de ella); segunda: el streamer.
    if (inc.length < 2) return null
    const [[vw, vh], [sw, sh]] = PANELS.react
    const v = inc[0]
    const hv = Math.min(v.h, v.w * srcW / ((vw / vh) * srcH))
    const video = regionWithAspect(v.x + v.w / 2, v.y + v.h / 2, hv, vw / vh, srcW, srcH)
    const s = inc[1]
    const streamer = regionWithAspect(s.x + s.w / 2, s.y + s.h / 2, Math.max(0.1, s.h), sw / sh, srcW, srcH)
    return [{ rect: video, panel: [vw, vh] }, { rect: streamer, panel: [sw, sh] }]
  }
  return null
}

/** Estilo CSS para mostrar `rect` de la imagen `url` llenando una caja. */
export function cropStyle(url: string, rect: Rect): React.CSSProperties {
  const bx = rect.w >= 1 ? 0 : (rect.x / (1 - rect.w)) * 100
  const by = rect.h >= 1 ? 0 : (rect.y / (1 - rect.h)) * 100
  return {
    backgroundImage: `url(${url})`,
    backgroundSize: `${100 / rect.w}% ${100 / rect.h}%`,
    backgroundPosition: `${bx}% ${by}%`,
    backgroundRepeat: 'no-repeat',
  }
}

export const PERSON_COLORS = ['#4C8DFF', '#D9A441', '#8FD19E', '#C77DFF', '#F2777B']
export const personLetter = (i: number) => String.fromCharCode(65 + i)
