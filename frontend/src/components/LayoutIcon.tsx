// Miniaturas esquemáticas 9:16 de cada diseño (sin decoración: rectángulos y líneas).
const F = '#56565c' // persona / contenido
const L = '#36363a' // separadores

function Head({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g>
      <circle cx={x} cy={y} r={3.2 * s} fill={F} />
      <path d={`M${x - 6 * s} ${y + 10 * s} q${6 * s} ${-7 * s} ${12 * s} 0`} fill={F} />
    </g>
  )
}

export function LayoutIcon({ id, size = 36 }: { id: string; size?: number }) {
  const w = 27
  const h = 48
  let body: React.ReactNode
  switch (id) {
    case 'follow':
      body = <Head x={13.5} y={20} s={1.4} />
      break
    case 'group':
      body = <><Head x={8} y={22} /><Head x={19} y={22} /></>
      break
    case 'facecam':
      body = (
        <>
          <rect x="0" y="0" width={w} height="18" fill="#26262a" />
          <Head x={13.5} y={6} />
          <line x1="0" y1="18" x2={w} y2="18" stroke={L} />
          <path d="M3 44 L10 30 L16 38 L20 33 L25 44 Z" fill={F} />
        </>
      )
      break
    case 'react':
      body = (
        <>
          <rect x="0" y="0" width={w} height="14" fill="#26262a" />
          <path d="M11 4 L17 7 L11 10 Z" fill={F} />
          <line x1="0" y1="14" x2={w} y2="14" stroke={L} />
          <Head x={13.5} y={28} s={1.3} />
        </>
      )
      break
    case 'split2':
      body = <><Head x={13.5} y={8} /><line x1="0" y1="24" x2={w} y2="24" stroke={L} /><Head x={13.5} y={32} /></>
      break
    case 'split3':
      body = (
        <>
          <Head x={13.5} y={5} s={0.8} /><line x1="0" y1="16" x2={w} y2="16" stroke={L} />
          <Head x={13.5} y={21} s={0.8} /><line x1="0" y1="32" x2={w} y2="32" stroke={L} />
          <Head x={13.5} y={37} s={0.8} />
        </>
      )
      break
    case 'speaker':
      body = (
        <>
          <Head x={13.5} y={20} s={1.4} />
          <path d="M21 14 q3 3 0 6 M23.5 12 q4.5 5 0 10" stroke={F} fill="none" strokeWidth="1.2" />
        </>
      )
      break
    default: // blur
      body = (
        <>
          <rect x="0" y="0" width={w} height="16" fill="#26262a" />
          <rect x="0" y="32" width={w} height="16" fill="#26262a" />
          <rect x="0" y="16" width={w} height="16" fill="#303035" />
          <Head x={13.5} y={20} s={0.6} />
        </>
      )
  }
  return (
    <svg width={size * (w / h)} height={size} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <rect x="0.5" y="0.5" width={w - 1} height={h - 1} fill="#1f1f22" stroke={L} />
      {body}
    </svg>
  )
}
