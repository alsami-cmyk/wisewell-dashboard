import type { Scope } from '../types'
import ScopeToggle from './ScopeToggle'

interface Props {
  scope:          Scope
  onScopeChange:  (s: Scope) => void
}

export default function Header({ scope, onScopeChange }: Props) {
  return (
    <header className="relative bg-[#0047ba] overflow-hidden -mx-6 mb-10 px-6 animate-fade-up">
      <div className="relative z-10 flex items-center justify-between gap-6 flex-wrap py-7">
        <div>
          {/* Brand row */}
          <div className="flex items-center gap-5 mb-4">
            <img
              src="/whitelogo.png"
              alt="ADNOC"
              className="h-10 w-auto flex-shrink-0"
            />
            <div className="w-px h-[34px] bg-white/20" />
            <div>
              <div className="text-[13px] font-medium tracking-[0.10em] uppercase text-white/80 leading-tight">
                Oasis Water Proposal
              </div>
              <div className="text-[10px] font-light tracking-[0.09em] uppercase text-white/45 mt-0.5">
                Financial Model · Confidential
              </div>
            </div>
          </div>

          {/* Title */}
          <h1 className="text-[26px] font-light text-white leading-[1.15] mb-1 max-[680px]:text-[20px]">
            Preliminary Revenue Projections
          </h1>
          <p className="text-[12px] text-white/50 font-light tracking-[0.03em]">
            All figures in AED
          </p>
        </div>

        <ScopeToggle scope={scope} onScopeChange={onScopeChange} />
      </div>
    </header>
  )
}
