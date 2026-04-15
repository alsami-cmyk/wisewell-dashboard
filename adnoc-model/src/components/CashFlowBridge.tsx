import type { CalcResult, Scope } from '../types'
import { useAnimatedNumber } from '../hooks/useAnimatedNumber'
import { fAED } from '../lib/format'

interface Props {
  result:             CalcResult
  scope:              Scope
  cannibalizationPct: number
}

function AnimatedAED({ value }: { value: number }) {
  const animated = useAnimatedNumber(value)
  return <>{fAED(animated)}</>
}

export default function CashFlowBridge({ result, scope, cannibalizationPct }: Props) {
  const isNetwork  = scope === 'network'
  const scopeLabel = isNetwork ? 'full network · annual' : 'per location · annual'

  const { totalCollections, adnocCashFlow, cannibalizationLoss, netCash } = result

  return (
    <div className="animate-fade-up-2 mb-9 space-y-3">

      {/* ── Hero: ADNOC Net Cash Flow ── */}
      <div className="relative overflow-hidden rounded-xl bg-[#0047ba] shadow-lg px-8 py-8">
        <div className="absolute -top-12 -right-12 w-56 h-56 rounded-full bg-white/5 pointer-events-none" />
        <div className="absolute -bottom-10 -left-10 w-44 h-44 rounded-full bg-white/5 pointer-events-none" />

        <div className="relative">
          <div className="text-[10px] font-bold tracking-[0.15em] uppercase text-white/50 mb-3">
            ADNOC Net Cash Flow · {scopeLabel}
          </div>
          <div className="text-[64px] font-extralight tabular-nums tracking-[-2px] text-white leading-none mb-3">
            <AnimatedAED value={adnocCashFlow} />
          </div>
          <div className="flex items-center gap-2 text-[12px] text-white/60">
            <span className="w-1.5 h-1.5 rounded-full bg-white/40 inline-block" />
            50% revenue share of AED <AnimatedAED value={totalCollections} /> total Oasis collections
          </div>
        </div>
      </div>

      {/* ── Comparison row: Cannibalization + Net Spread ── */}
      <div className="grid grid-cols-2 gap-3">

        {/* Cannibalization */}
        <div className="bg-white border border-black/10 rounded-xl px-6 py-5 shadow-sm">
          <div className="text-[9px] font-bold tracking-[0.14em] uppercase text-[#7A8CAE] mb-3">
            Cannibalization Assumption
          </div>
          <div className="text-[32px] font-extralight tabular-nums tracking-[-1px] text-[#c0392b] leading-none mb-2">
            <AnimatedAED value={cannibalizationLoss} />
          </div>
          <div className="text-[11px] text-[#7A8CAE] leading-snug">
            Assuming {cannibalizationPct}% of bottle fills directly displaces an existing bottled water sales
          </div>
        </div>

        {/* Net Spread */}
        <div className="bg-white border border-emerald-200 rounded-xl px-6 py-5 shadow-sm">
          <div className="text-[9px] font-bold tracking-[0.14em] uppercase text-emerald-500 mb-3">
            ✦ Net Spread with Wisewell
          </div>
          <div className="text-[32px] font-extralight tabular-nums tracking-[-1px] text-emerald-600 leading-none mb-2">
            <AnimatedAED value={netCash} />
          </div>
          <div className="text-[11px] text-[#7A8CAE] leading-snug">
            Oasis cash flow exceeds worst-case cannibalization — before any upsell or cross-sell benefit
          </div>
        </div>
      </div>

    </div>
  )
}
