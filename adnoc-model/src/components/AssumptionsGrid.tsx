import type { Assumptions } from '../types'
import AssumptionCard from './AssumptionCard'

interface Props {
  assumptions: Assumptions
  onChange:    (key: keyof Assumptions, value: number) => void
}

export default function AssumptionsGrid({ assumptions, onChange }: Props) {
  const {
    salesPerDay, salesPerDay1fill, locations,
    mix1m, mix3m, mix12m,
    price1fill, price1m, price3m, price12m,
    cannibalizationPct, profitPerFill,
  } = assumptions

  const ch = (k: keyof Assumptions) => (v: number) => onChange(k, v)

  return (
    <div className="grid grid-cols-4 gap-3 mb-9 max-[900px]:grid-cols-2 max-[460px]:grid-cols-1 animate-fade-up-2">

      {/* ── Row 1: Volume ── */}
      <AssumptionCard
        label="Subscription sales / day" id="sales-day"
        min={0.5} max={10} step={0.1}
        value={salesPerDay} unit="sales/day"
        onChange={ch('salesPerDay')}
      />
      <AssumptionCard
        label="Single-Fill sales / day" id="sales-day-1fill"
        min={0} max={10} step={0.5}
        value={salesPerDay1fill} unit="sales/day"
        onChange={ch('salesPerDay1fill')}
      />
      <AssumptionCard
        label="Total locations" id="locations"
        min={10} max={500} step={10}
        value={locations} unit="sites"
        onChange={ch('locations')}
      />
      <div />

      {/* ── Row 2: Cannibalization ── */}
      <AssumptionCard
        label="Cannibalization %" id="cannibal-pct"
        min={0} max={50} step={25}
        value={cannibalizationPct} unit="%"
        onChange={ch('cannibalizationPct')}
      />
      <AssumptionCard
        label="ADNOC Blended Margin per Bottle of Water" id="profit-fill"
        min={1.5} max={2.7} step={0.1}
        value={profitPerFill} unit="AED"
        onChange={ch('profitPerFill')} variant="pricing"
      />
      <div /><div />

      {/* ── Row 3: Pricing ── */}
      <AssumptionCard
        label="Single-Fill price" id="price-1fill"
        min={5} max={30} step={1}
        value={price1fill} unit="AED"
        onChange={ch('price1fill')} variant="pricing"
      />
      <AssumptionCard
        label="1-month price" id="price-1m"
        min={10} max={100} step={1}
        value={price1m} unit="AED"
        onChange={ch('price1m')} variant="pricing"
      />
      <AssumptionCard
        label="3-month price" id="price-3m"
        min={20} max={200} step={5}
        value={price3m} unit="AED"
        onChange={ch('price3m')} variant="pricing"
      />
      <AssumptionCard
        label="12-month price" id="price-12m"
        min={50} max={500} step={10}
        value={price12m} unit="AED"
        onChange={ch('price12m')} variant="pricing"
      />

      {/* ── Row 4: Subscription tier mix ── */}
      <AssumptionCard
        label="Tier mix — 1-month" id="mix-1m"
        min={5} max={80} step={5}
        value={mix1m} unit="%"
        onChange={ch('mix1m')}
      />
      <AssumptionCard
        label="Tier mix — 3-month" id="mix-3m"
        min={5} max={80} step={5}
        value={mix3m} unit="%"
        onChange={ch('mix3m')}
      />
      <AssumptionCard
        label="Tier mix — 12-month" id="mix-12m"
        min={5} max={60} step={5}
        value={mix12m} unit="%"
        onChange={ch('mix12m')}
      />
      <div />

    </div>
  )
}
