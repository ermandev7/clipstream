import Hls from 'hls.js'
import { useEffect, useRef, useState } from 'react'

/**
 * Monitor del directo: reproduce los trozos que ClipStream va capturando (lista HLS del búfer) con hls.js.
 * Va unos segundos por detrás de la emisión, pero fluido y con sonido. Si algo falla, vuelve a la imagen fija.
 */
export function LiveVideo({ sourceId, onTime }: { sourceId: number; onTime?: (t: number) => void }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [failed, setFailed] = useState(false)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const video = ref.current
    if (!video || failed) return
    const url = `/api/sources/${sourceId}/live.m3u8`
    let hls: Hls | null = null
    let retries = 0
    if (Hls.isSupported()) {
      // Sin ir pegados al borde del directo: 2 trozos (~20 s) de colchón para que no se corte.
      // backBufferLength: lo ya visto se libera (si no, en un directo de horas la memoria crece sin parar).
      hls = new Hls({ liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5, lowLatencyMode: false, backBufferLength: 30 })
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (!data.fatal) return
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && retries++ < 6) {
          // Al empezar la captura aún no hay trozos: se reintenta unos segundos después.
          window.setTimeout(() => hls?.loadSource(url), 4000)
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && retries++ < 6) {
          hls?.recoverMediaError()
        } else {
          setFailed(true)
        }
      })
      hls.loadSource(url)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => { /* el navegador puede exigir un clic */ }) })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url
      video.play().catch(() => {})
    } else {
      setFailed(true)
    }
    return () => { hls?.destroy() }
  }, [sourceId, failed])

  // Respaldo: la imagen del último trozo, cada 5 s.
  useEffect(() => {
    if (!failed) return
    const t = window.setInterval(() => setTick((x) => x + 1), 5000)
    return () => window.clearInterval(t)
  }, [failed])

  if (failed) {
    return <img src={`/api/sources/${sourceId}/frame?t=${tick}`} alt="Último fotograma del directo"
      onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} onLoad={(e) => { e.currentTarget.style.visibility = 'visible' }} />
  }
  return (
    <>
      <video ref={ref} controls muted playsInline onTimeUpdate={(e) => onTime?.(e.currentTarget.currentTime)} />
      <span className="live-badge"><span className="dot" />EN DIRECTO · unos segundos de retraso</span>
    </>
  )
}
