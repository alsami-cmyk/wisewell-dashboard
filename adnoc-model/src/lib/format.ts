/** AED with compact suffix: AED 1.23M / AED 45.6K / AED 890 */
export function fAED(n: number): string {
  n = Math.round(n)
  if (n >= 1_000_000) return 'AED ' + (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000)     return 'AED ' + (n / 1_000).toFixed(1) + 'K'
  return 'AED ' + n.toLocaleString()
}

/** AED with full locale formatting: AED 123,456 */
export function fAEDFull(n: number): string {
  return 'AED ' + Math.round(n).toLocaleString()
}

/** Plain count with compact suffix: 1.23M / 45.6K / 890 */
export function fCount(n: number): string {
  n = Math.round(n)
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K'
  return n.toLocaleString()
}
