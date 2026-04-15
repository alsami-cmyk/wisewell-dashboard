import type { CalcResult, Scope, TierVariant } from '../types'
import { fAED, fAEDFull } from '../lib/format'

interface Props {
  result: CalcResult
  scope:  Scope
}


const badgeClass: Record<TierVariant, string> = {
  t1fill: 'bg-[#0047ba]/10 text-[#0047ba]',
  t1:     'bg-[#0047ba]/10 text-[#0047ba]',
  t3:     'bg-[#0047ba]/10 text-[#0047ba]',
  t12:    'bg-[#0047ba]/10 text-[#0047ba]',
}
const dotClass: Record<TierVariant, string> = {
  t1fill: 'bg-[#0047ba]',
  t1:     'bg-[#0047ba]',
  t3:     'bg-[#0047ba]',
  t12:    'bg-[#0047ba]',
}

export default function TierTable({ result, scope }: Props) {
  const scopeLabel = scope === 'network' ? 'Full network' : 'Per location'

  return (
    <div className="animate-fade-up-3">
      <div className="bg-white border border-black/10 rounded-xl overflow-hidden shadow-sm">
        {/* Card header */}
        <div className="flex items-center justify-between px-5 py-[15px] border-b border-black/10 bg-[rgba(13,27,62,0.025)]">
          <span className="text-[12px] font-semibold text-navy tracking-[0.03em]">
            Customer collections — AED, annual
          </span>
          <span className="text-[10px] text-[#7A8CAE] bg-[#F4F6FA] border border-black/10 rounded-md px-2.5 py-[3px] font-medium tracking-[0.04em] uppercase">
            {scopeLabel}
          </span>
        </div>

        {/* Responsive scroll wrapper */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse" style={{ minWidth: 640 }}>
            <thead>
              <tr className="bg-[#f8f9fc] border-b border-black/10">
                {['Tier', 'Bottle price', 'Customers / yr', 'Subscription revenue', 'ADNOC share (50%)', 'Total collected by ADNOC'].map((h, i) => (
                  <th
                    key={h}
                    className={[
                      'px-[18px] py-2.5 text-[9px] font-semibold tracking-[0.11em] uppercase text-[#7A8CAE]',
                      i === 5 ? 'text-right' : 'text-left',
                    ].join(' ')}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.tiers.map(t => (
                <tr key={t.label} className="border-b border-black/10 last:border-none">
                  <td className="px-[18px] py-[13px] text-[13px] text-[#3A4D7A] align-middle">
                    <span className={`inline-flex items-center gap-1.5 px-[11px] py-1 rounded-full text-[11px] font-medium ${badgeClass[t.variant]}`}>
                      <span className={`w-[5px] h-[5px] rounded-full ${dotClass[t.variant]}`} />
                      {t.label}
                    </span>
                  </td>
                  <td className="px-[18px] py-[13px] text-[13px] text-[#3A4D7A]">
                    AED {t.price}
                  </td>
                  <td className="px-[18px] py-[13px] text-[13px] text-[#3A4D7A]">
                    {Math.round(t.customers).toLocaleString()}
                  </td>
                  <td className="px-[18px] py-[13px] text-[13px] text-[#3A4D7A]">
                    {fAEDFull(t.subscriptionRevenue)}
                  </td>
                  <td className="px-[18px] py-[13px] text-[13px] text-navy-pale font-medium">
                    {fAEDFull(t.adnocShare)}
                  </td>
                  <td className="px-[18px] py-[13px] text-[13px] font-semibold text-navy text-right tabular-nums">
                    {fAEDFull(t.totalCollected)}
                  </td>
                </tr>
              ))}

            </tbody>
          </table>
        </div>

        {/* Total footer */}
        <div className="flex items-center justify-between px-5 py-[15px] bg-[#0047ba]">
          <span className="text-[13px] font-medium text-white/70">
            Total annual cash flow to ADNOC
          </span>
          <span className="text-[22px] font-light text-white tabular-nums tracking-[-0.5px]">
            {fAED(result.adnocCashFlow)}
          </span>
        </div>
      </div>
    </div>
  )
}
