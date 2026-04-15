interface Props {
  label:    string
  id:       string
  min:      number
  max:      number
  step:     number
  value:    number
  unit:     string
  onChange: (value: number) => void
  variant?: 'default' | 'pricing'
}

export default function AssumptionCard({
  label, id, min, max, step, value, unit, onChange, variant = 'default',
}: Props) {
  const isPricing = variant === 'pricing'

  function handleInput(raw: string) {
    const v = parseFloat(raw)
    if (isNaN(v)) return
    onChange(Math.min(max, Math.max(min, v)))
  }

  const cardClass = [
    'bg-white border rounded-[10px] px-4 pt-4 pb-[13px] shadow-sm',
    'transition-[border-color] duration-200',
    isPricing
      ? 'border-[rgba(0,71,186,0.25)] bg-gradient-to-br from-white via-white to-[rgba(0,71,186,0.03)] hover:border-[rgba(0,71,186,0.45)]'
      : 'border-black/10 hover:border-black/[0.18]',
  ].join(' ')

  return (
    <div className={cardClass}>
      <div className="text-[10px] font-semibold tracking-[0.09em] uppercase text-[#7A8CAE] mb-1.5">
        {label}
      </div>

      {/* Value row: number input + unit */}
      <div className="flex items-baseline gap-1.5 mb-2.5">
        <input
          type="number"
          id={`ni-${id}`}
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={e => handleInput(e.target.value)}
          className={[
            'w-[72px] text-[20px] font-light font-sans',
            'bg-transparent border-0 border-b-[1.5px] rounded-none p-0 px-0.5',
            'outline-none tabular-nums tracking-[-0.3px]',
            'transition-[border-color] duration-150',
            isPricing
              ? 'text-[#0047ba] border-b-black/[0.18] focus:border-b-[#0047ba]'
              : 'text-[#0047ba] border-b-black/[0.18] focus:border-b-[#0047ba]',
          ].join(' ')}
        />
        <span className="text-[11px] text-[#7A8CAE]">{unit}</span>
      </div>

      {/* Slider */}
      <input
        type="range"
        id={`sl-${id}`}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="slider-navy"
      />

      {/* Bounds */}
      <div className="flex justify-between mt-[5px] text-[9px] text-[#7A8CAE] tracking-[0.03em]">
        <span>{isPricing ? `AED ${min}` : min}{unit === '%' ? '%' : ''}</span>
        <span>{isPricing ? `AED ${max}` : max}{unit === '%' ? '%' : ''}</span>
      </div>
    </div>
  )
}
