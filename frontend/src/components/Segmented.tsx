interface Option<T extends string> { value: T; label: string; disabled?: boolean; title?: string }

export function Segmented<T extends string>({ value, options, onChange }: {
  value: T
  options: Option<T>[]
  onChange: (v: T) => void
}) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
          className={value === o.value ? 'on' : ''} disabled={o.disabled} title={o.title} aria-label={o.label}
          onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
