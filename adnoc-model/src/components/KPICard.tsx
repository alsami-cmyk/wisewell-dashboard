import { useEffect, useRef } from 'react'
import { useAnimatedNumber } from '../hooks/useAnimatedNumber'
import { fAED, fCount } from '../lib/format'

type Format = 'aed' | 'count'

interface Props {
  label:  string
  value:  number
  sub:    string
  sub2?:  string
  format: Format
}

function display(n: number, fmt: Format): string {
  return fmt === 'aed' ? fAED(n) : fCount(n)
}

export default function KPICard({ label, value, sub, sub2, format }: Props) {
  const animated = useAnimatedNumber(value)
  const cardRef  = useRef<HTMLDivElement>(null)
  const prevRef  = useRef(value)

  // Trigger pulse animation whenever the target value changes
  useEffect(() => {
    if (prevRef.current === value) return
    prevRef.current = value
    const el = cardRef.current
    if (!el) return
    el.classList.remove('animate-kpi-pulse')
    // Force reflow so re-adding the class restarts the animation
    void el.offsetWidth
    el.classList.add('animate-kpi-pulse')
  }, [value])

  return (
    <div
      ref={cardRef}
      className={[
        'relative overflow-hidden bg-white border border-black/10 rounded-xl',
        'px-[18px] py-5 shadow-sm',
        'hover:shadow-md hover:border-black/[0.18]',
        'transition-[box-shadow,border-color] duration-200',
      ].join(' ')}
    >
      {/* Accent top bar */}
      <div className="absolute top-0 left-0 right-0 h-[3px] rounded-t-xl bg-[#0047ba]" />

      <div className="text-[10px] font-semibold tracking-[0.1em] uppercase text-[#7A8CAE] mb-2">
        {label}
      </div>
      <div className="text-[26px] font-light leading-none mb-1 tabular-nums tracking-[-0.5px] text-[#0047ba]">
        {display(animated, format)}
      </div>
      <div className="text-[11px] text-[#7A8CAE]">{sub}</div>
      {sub2 && (
        <div className="text-[11px] text-[#7A8CAE] mt-0.5 opacity-75">{sub2}</div>
      )}
    </div>
  )
}
