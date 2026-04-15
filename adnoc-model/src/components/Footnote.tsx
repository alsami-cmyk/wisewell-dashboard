export default function Footnote() {
  return (
    <footer className="text-[11px] text-[#7A8CAE] mt-8 pt-4 border-t border-black/10 leading-[1.9]">
      <strong className="text-[#3A4D7A] font-medium">Currency:</strong>{' '}
      All figures in UAE Dirhams (AED). USD/AED rate: 3.67.{' '}
      <strong className="text-[#3A4D7A] font-medium">Collections:</strong>{' '}
      ADNOC collects 100% of customer payments at POS — subscription bottle prices plus
      proprietary bottle unit revenue (AED 4.04/bottle).{' '}
      <strong className="text-[#3A4D7A] font-medium">ADNOC cash flow</strong>{' '}
      = 25% revenue share on subscription sales + 100% of bottle unit sales.{' '}
      <strong className="text-[#3A4D7A] font-medium">Zero cost obligation to ADNOC</strong>{' '}
      — machine procurement, installation, filter replacements, and all servicing are
      entirely borne by the Wisewell partnership. Model reflects steady-state monthly
      run-rate from month 1. Tier mix percentages must sum to 100%.
    </footer>
  )
}
