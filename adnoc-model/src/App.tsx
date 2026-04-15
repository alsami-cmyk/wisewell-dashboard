import { useState, useMemo } from 'react'
import type { Assumptions, Scope } from './types'
import { calculate, rebalanceMix } from './lib/calculations'
import Header from './components/Header'
import KPIRow from './components/KPIRow'
import CashFlowBridge from './components/CashFlowBridge'
import TierSavingsTable from './components/TierSavingsTable'
import AssumptionsGrid from './components/AssumptionsGrid'

const DEFAULTS: Assumptions = {
  salesPerDay:        2.1,
  salesPerDay1fill:   1.5,
  locations:          384,
  mix1m:              30,
  mix3m:              65,
  mix12m:             5,
  price1fill:         12,
  price1m:            29,
  price3m:            75,
  price12m:           229,
  cannibalizationPct: 50,
  profitPerFill:      2.3,
}

export default function App() {
  const [scope, setScope] = useState<Scope>('network')
  const [assumptions, setAssumptions] = useState<Assumptions>(DEFAULTS)

  const result = useMemo(
    () => calculate(assumptions, scope),
    [assumptions, scope],
  )

  function handleChange(key: keyof Assumptions, value: number) {
    setAssumptions(prev => {
      const next = { ...prev, [key]: value }
      if (key === 'mix1m' || key === 'mix3m' || key === 'mix12m') {
        return { ...next, ...rebalanceMix(next, key) }
      }
      return next
    })
  }

  return (
    <div className="relative z-10 max-w-[1080px] mx-auto px-6 pb-20">
      <Header scope={scope} onScopeChange={setScope} />

      <SectionLabel>Summary</SectionLabel>
      <KPIRow result={result} scope={scope} />

      <SectionLabel>Cash Flow Analysis</SectionLabel>
      <CashFlowBridge result={result} scope={scope} cannibalizationPct={assumptions.cannibalizationPct} />

      <SectionLabel>Assumptions</SectionLabel>
      <AssumptionsGrid assumptions={assumptions} onChange={handleChange} />

      <SectionLabel>Customer Savings by Tier</SectionLabel>
      <TierSavingsTable assumptions={assumptions} />
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 mb-3.5 text-[10px] font-semibold tracking-[0.14em] uppercase text-[#0047ba]/60">
      {children}
      <span className="flex-1 h-px bg-black/10" />
    </div>
  )
}
