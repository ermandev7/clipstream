import { useEffect, useRef, useState } from 'react'
import type { Clip, GpuStatus, LogEvent, Source } from './api'

export type WsMessage =
  | { type: 'gpu'; gpu: GpuStatus }
  | { type: 'source'; source: Source }
  | { type: 'clip'; clip: Clip }
  | ({ type: 'log' } & LogEvent)

type Listener = (m: WsMessage) => void
const listeners = new Set<Listener>()
let socket: WebSocket | null = null
let retry = 0
let connected = false
const connListeners = new Set<(c: boolean) => void>()

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${proto}://${location.host}/ws`)
  socket.onopen = () => {
    retry = 0
    connected = true
    connListeners.forEach((l) => l(true))
  }
  socket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data) as WsMessage
      listeners.forEach((l) => l(msg))
    } catch { /* mensaje inválido */ }
  }
  socket.onclose = () => {
    connected = false
    connListeners.forEach((l) => l(false))
    socket = null
    setTimeout(connect, Math.min(5000, 500 * 2 ** retry++))
  }
}

export function useWs(listener: Listener) {
  const ref = useRef(listener)
  ref.current = listener
  useEffect(() => {
    const l: Listener = (m) => ref.current(m)
    listeners.add(l)
    if (!socket) connect()
    return () => { listeners.delete(l) }
  }, [])
}

export function useConnected(): boolean {
  const [c, setC] = useState(connected)
  useEffect(() => {
    connListeners.add(setC)
    return () => { connListeners.delete(setC) }
  }, [])
  return c
}
