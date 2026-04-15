import type { Scope } from '../types'

interface Props {
  scope:          Scope
  onScopeChange:  (s: Scope) => void
}

const SCOPES: { value: Scope; label: string }[] = [
  { value: 'network', label: 'Full network' },
  { value: 'single',  label: 'Per location' },
]

export default function ScopeToggle({ scope, onScopeChange }: Props) {
  return (
    <div className="flex bg-white/[0.07] border border-white/15 rounded-lg overflow-hidden flex-shrink-0 self-center">
      {SCOPES.map(({ value, label }) => (
        <button
          key={value}
          onClick={() => onScopeChange(value)}
          className={[
            'px-[22px] py-[9px] text-[12px] font-medium font-sans whitespace-nowrap',
            'transition-all duration-[180ms] border-none outline-none cursor-pointer',
            scope === value
              ? 'bg-white text-[#0047ba]'
              : 'bg-transparent text-white/55 hover:text-white/80',
          ].join(' ')}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
