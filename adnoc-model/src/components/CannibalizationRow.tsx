import type { CalcResult, Scope } from '../types'
import { useAnimatedNumber } from '../hooks/useAnimatedNumber'
import { fAED } from '../lib/format'

interface Props {
  result: CalcResult
  scope:  Scope
}

function CannibalizationCard({
  label, value, sub, isNet,
}: {
  label: string
  value: number
  sub:   string
  isNet?: boolean
}) {
  const animated = useAnimatedNumber(value)

  return (
    <div
      className={[
        'relative overflow-hidden border rounded-xl px-[18px] py-5 shadow-sm',
        'hover:shadow-md transition-[box-shadow,border-color] duration-200',
        isNet
          ? 'bg-[#0047ba] border-[#0047ba]'
          : 'bg-white border-black/10 hover:border-black/[0.18]',
      ].join(' ')}
    >
      <div className={`absolute top-0 left-0 right-0 h-[3px] rounded-t-xl ${isNet ? 'bg-white/30' : 'bg-[#0047ba]'}`} />
      <div className={`text-[10px] font-semibold tracking-[0.1em] uppercase mb-2 ${isNet ? 'text-white/70' : 'text-[#7A8CAE]'}`}>
        {label}
      </div>
      <div className={`text-[26px] font-light leading-none mb-1 tabular-nums tracking-[-0.5px] ${isNet ? 'text-white' : 'text-[#0047ba]'}`}>
        {fAED(animated)}
      </div>
      <div className={`text-[11px] ${isNet ? 'text-white/60' : 'text-[#7A8CAE]'}`}>{sub}</div>
    </div>
  )
}

export default function CannibalizationRow({ result, scope }: Props) {
  const isNetwork = scope === 'network'
  const scopeSub  = isNetwork ? 'AED · full network · annual' : 'AED · per location · annual'

  return (
    <div className="grid grid-cols-2 gap-3.5 mb-9 max-[680px]:grid-cols-1 animate-fade-up-2">
      <CannibalizationCard
        label="Cannibalized Profit"
        value={result.cannibalizationLoss}
        sub={scopeSub}
      />
      <CannibalizationCard
        label="Net Cash to ADNOC"
        value={result.netCash}
        sub={`${scopeSub} · after cannibalization`}
        isNet
      />
    </div>
  )
}
