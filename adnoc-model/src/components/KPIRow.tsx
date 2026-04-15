import type { CalcResult, Scope } from '../types'
import KPICard from './KPICard'

interface Props {
  result: CalcResult
  scope:  Scope
}

export default function KPIRow({ result, scope }: Props) {
  const isNetwork = scope === 'network'

  return (
    <div className="grid grid-cols-4 gap-3.5 mb-9 max-[680px]:grid-cols-2 animate-fade-up-1">
      <KPICard
        label="ADNOC Annual Revenue"
        value={result.totalCollections}
        sub="AED · annual"
        format="aed"
      />
      <KPICard
        label="ADNOC Revenue Share"
        value={result.adnocCashFlow}
        sub="50% Revenue Share"
        format="aed"
      />
      <KPICard
        label="Yearly Sales"
        value={result.salesYear}
        sub={isNetwork ? 'bottle sales · full network' : 'bottle sales · per location'}
        format="count"
      />
      <KPICard
        label="Total Bottle Fills per Year"
        value={result.fillsPerYear}
        sub="Assumes 1 fill per 3 days"
        sub2={isNetwork ? 'full network' : 'per location'}
        format="count"
      />
    </div>
  )
}
