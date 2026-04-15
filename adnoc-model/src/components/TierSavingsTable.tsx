import type { Assumptions } from '../types'

interface Props {
  assumptions: Assumptions
}

// Fixed cadence: 1 fill per 3 days
const DAYS_PER_FILL = 3
const FILLS: Record<string, number> = {
  '1fill': 1,
  '1m':    Math.round(30  / DAYS_PER_FILL), // 10
  '3m':    Math.round(90  / DAYS_PER_FILL), // 30
  '12m':   Math.round(360 / DAYS_PER_FILL), // 120
}

// ADNOC blended average bottle price per customer
const BLENDED_AVG = 2.9

interface Row {
  label:        string
  period:       string
  price:        number
  fills:        number
  costPerFill:  number
  vsBlendedPct: number | null   // % cheaper vs AED 2.9
  vsBlendedAED: number | null   // total AED saved vs AED 2.9 × fills
  isBase:       boolean
}

export default function TierSavingsTable({ assumptions }: Props) {
  const { price1m, price3m, price12m, price1fill } = assumptions

  const rows: Row[] = [
    {
      label:        'Single-Fill',
      period:       'Per fill',
      price:        price1fill,
      fills:        FILLS['1fill'],
      costPerFill:  price1fill,
      vsBlendedPct: null,
      vsBlendedAED: null,
      isBase:       true,
    },
    {
      label:        '1-Month',
      period:       '30 days',
      price:        price1m,
      fills:        FILLS['1m'],
      costPerFill:  price1m / FILLS['1m'],
      vsBlendedPct: ((BLENDED_AVG - price1m / FILLS['1m']) / BLENDED_AVG) * 100,
      vsBlendedAED: FILLS['1m'] * BLENDED_AVG - price1m,
      isBase:       false,
    },
    {
      label:        '3-Month',
      period:       '90 days',
      price:        price3m,
      fills:        FILLS['3m'],
      costPerFill:  price3m / FILLS['3m'],
      vsBlendedPct: ((BLENDED_AVG - price3m / FILLS['3m']) / BLENDED_AVG) * 100,
      vsBlendedAED: FILLS['3m'] * BLENDED_AVG - price3m,
      isBase:       false,
    },
    {
      label:        '12-Month',
      period:       '360 days',
      price:        price12m,
      fills:        FILLS['12m'],
      costPerFill:  price12m / FILLS['12m'],
      vsBlendedPct: ((BLENDED_AVG - price12m / FILLS['12m']) / BLENDED_AVG) * 100,
      vsBlendedAED: FILLS['12m'] * BLENDED_AVG - price12m,
      isBase:       false,
    },
  ]

  return (
    <div className="animate-fade-up-3 mb-9">
      <div className="bg-white border border-black/10 rounded-xl overflow-hidden shadow-sm">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-[15px] border-b border-black/10 bg-[rgba(13,27,62,0.025)]">
          <span className="text-[12px] font-semibold text-navy tracking-[0.03em]">
            Customer value — savings per tier vs. AED {BLENDED_AVG} blended avg. bottle price
          </span>
          <span className="text-[10px] text-[#7A8CAE] bg-[#F4F6FA] border border-black/10 rounded-md px-2.5 py-[3px] font-medium tracking-[0.04em] uppercase">
            1 fill per 3 days
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse" style={{ minWidth: 500 }}>
            <thead>
              <tr className="bg-[#f8f9fc] border-b border-black/10">
                {[
                  { label: 'Tier',              align: 'left'   },
                  { label: 'Price',             align: 'center' },
                  { label: 'Fills Projected',   align: 'center' },
                  { label: 'Cost per Fill',     align: 'center' },
                  { label: `vs. AED ${BLENDED_AVG} avg`, align: 'center' },
                ].map(h => (
                  <th
                    key={h.label}
                    className={[
                      'px-4 py-2.5 text-[9px] font-semibold tracking-[0.11em] uppercase text-[#7A8CAE]',
                      h.align === 'center' ? 'text-center' : 'text-left',
                    ].join(' ')}
                  >
                    {h.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.label} className={`border-b border-black/10 last:border-none ${row.isBase ? 'bg-[#fafbfd]' : ''}`}>

                  {/* Tier */}
                  <td className="px-4 py-[13px] align-middle">
                    <span className="inline-flex items-center gap-1.5 px-[11px] py-1 rounded-full text-[11px] font-medium bg-[#0047ba]/10 text-[#0047ba]">
                      <span className="w-[5px] h-[5px] rounded-full bg-[#0047ba]" />
                      {row.label}
                    </span>
                    <div className="text-[10px] text-[#7A8CAE] mt-0.5 pl-1">{row.period}</div>
                  </td>

                  {/* Price */}
                  <td className="px-4 py-[13px] text-[13px] text-[#3A4D7A] text-center">
                    AED {row.price}
                  </td>

                  {/* Fills Projected */}
                  <td className="px-4 py-[13px] text-[13px] text-[#3A4D7A] text-center">
                    {row.fills} {row.fills === 1 ? 'fill' : 'fills'}
                  </td>

                  {/* Cost per fill */}
                  <td className="px-4 py-[13px] text-[13px] font-medium text-[#0047ba] text-center">
                    AED {row.costPerFill.toFixed(2)}
                  </td>

                  {/* vs. AED 2.9 % */}
                  <td className="px-4 py-[13px] text-center">
                    {row.isBase ? (
                      <span className="text-[11px] text-[#7A8CAE] italic">N/A</span>
                    ) : Math.round(row.vsBlendedPct!) === 0 ? (
                      <span className="text-[11px] text-[#7A8CAE] italic">No change</span>
                    ) : row.vsBlendedPct! > 0 ? (
                      <span className="inline-block bg-emerald-50 text-emerald-700 border border-emerald-200 text-[11px] font-semibold px-2.5 py-0.5 rounded-full">
                        ↓ {row.vsBlendedPct!.toFixed(0)}% savings
                      </span>
                    ) : (
                      <span className="inline-block bg-red-50 text-red-500 border border-red-100 text-[11px] font-semibold px-2.5 py-0.5 rounded-full">
                        ↑ {Math.abs(row.vsBlendedPct!).toFixed(0)}% pricier
                      </span>
                    )}
                  </td>

                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div className="px-5 py-3.5 border-t border-black/10 bg-[rgba(13,27,62,0.025)]">
          <span className="text-[10px] text-[#7A8CAE]">
            💧 Savings calculated vs. AED {BLENDED_AVG} blended average bottled water price per ADNOC customer, applied across projected fills for the subscription period.
          </span>
        </div>
      </div>
    </div>
  )
}
