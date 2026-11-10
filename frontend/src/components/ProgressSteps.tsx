import type { Clip, SourceDetail } from '../lib/api'

/** El proceso de una fuente por pasos, con el porcentaje del paso en curso. */
interface Step { id: string; label: string; active: string; stages?: string[] }

const VOD: Step[] = [
  { id: 'download', label: 'Descargar', active: 'Descargando', stages: ['Descargando'] },
  { id: 'audio', label: 'Preparar audio', active: 'Preparando audio', stages: ['Preparando audio'] },
  { id: 'transcribe', label: 'Transcribir', active: 'Transcribiendo', stages: ['Transcribiendo'] },
  { id: 'detect', label: 'Detectar momentos', active: 'Detectando momentos', stages: ['Detectando'] },
  { id: 'judge', label: 'Evaluar con IA', active: 'Evaluando con IA', stages: ['Evaluando con IA'] },
  { id: 'render', label: 'Generar clips', active: 'Generando clips' },
]

export function stepsFor(d: SourceDetail): Step[] {
  if (d.kind === 'local') return VOD.filter((s) => s.id !== 'download')
  if (d.kind === 'live') return [{ id: 'capture', label: 'Capturar directo', active: 'Capturando' }, VOD[5]]
  return VOD
}

/** Progreso de «Generar clips»: clips terminados + el que se está haciendo. */
export function renderProgress(clips: Clip[]): { frac: number; done: number; total: number } {
  const total = clips.length
  const done = clips.filter((c) => c.status === 'ready' || c.status === 'error').length
  const running = clips.filter((c) => c.status === 'rendering').reduce((a, c) => a + (c.progress || 0), 0)
  return { frac: total ? (done + running) / total : 0, done, total }
}

function currentIndex(d: SourceDetail, steps: Step[]): number {
  if (d.status === 'done') return steps.length
  if (d.kind === 'live') return d.status === 'live' ? 0 : steps.length - (d.clips.some((c) => c.status !== 'ready') ? 1 : 0)
  if (d.status === 'pending') return -1
  if (d.status === 'downloading') return steps.findIndex((s) => s.id === 'download')
  if (d.status === 'rendering') return steps.findIndex((s) => s.id === 'render')
  const i = steps.findIndex((s) => s.stages?.some((st) => (d.stage || '').startsWith(st)))
  return i >= 0 ? i : 0
}

export function ProgressSteps({ detail }: { detail: SourceDetail }) {
  const steps = stepsFor(detail)
  const cur = currentIndex(detail, steps)
  const failed = detail.status === 'error'
  const rp = renderProgress(detail.clips)
  const pct = (i: number): number | null => {
    if (i !== cur || failed) return null
    if (steps[i].id === 'render') return rp.frac
    if (steps[i].id === 'capture') return null
    return detail.stage_progress ?? 0
  }
  const live = detail.kind === 'live' && detail.status === 'live'
  return (
    <ol className="steps" aria-label="Progreso">
      {cur === -1 && <li className="steps-wait muted">En cola: empezará en cuanto termine el trabajo anterior.</li>}
      {steps.map((s, i) => {
        const state = failed && i === Math.max(0, cur) ? 'error' : i < cur ? 'done' : i === cur ? 'active' : 'todo'
        const p = pct(i)
        let detailText = ''
        if (state === 'active') {
          if (s.id === 'render') detailText = rp.total ? `${detail.stage?.startsWith('Generando clip ') ? detail.stage.replace('Generando ', '') : `${rp.done}/${rp.total}`} · ${Math.round(rp.frac * 100)} %` : 'Preparando…'
          else if (s.id === 'capture') detailText = live ? `${detail.clips.length} clips hasta ahora` : ''
          else detailText = `${Math.round((p ?? 0) * 100)} %`
        } else if (state === 'error') detailText = 'Error'
        return (
          <li key={s.id} className={`step ${state}`} aria-current={state === 'active' ? 'step' : undefined}>
            <span className="step-dot mono">{state === 'done' ? '✓' : state === 'error' ? '!' : i + 1}</span>
            <span className="step-text">
              <span className="step-label">{state === 'active' ? s.active : s.label}</span>
              {detailText && <span className="step-detail mono">{detailText}</span>}
            </span>
            {state === 'active' && p != null && <span className="step-bar"><span style={{ width: `${Math.round(p * 100)}%` }} /></span>}
          </li>
        )
      })}
    </ol>
  )
}
