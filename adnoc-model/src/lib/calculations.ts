import type { Assumptions, CalcResult, Scope } from '../types'

const ADNOC_SHARE = 0.50

export function calculate(a: Assumptions, scope: Scope): CalcResult {
  const sc = scope === 'network' ? a.locations : 1

  // 1-fill: standalone SKU, own sales rate
  const salesYear1fill = a.salesPerDay1fill * 30 * 12
  const c1fill         = Math.round(salesYear1fill)

  // Subscriptions: separate sales rate, mix applies only to these
  const salesYear = a.salesPerDay * 30 * 12
  const c1        = Math.round(salesYear * (a.mix1m  / 100))
  const c3        = Math.round(salesYear * (a.mix3m  / 100))
  const c12       = Math.round(salesYear * (a.mix12m / 100))

  const sr1fill = c1fill * a.price1fill
  const sr1     = c1     * a.price1m
  const sr3     = c3     * a.price3m
  const sr12    = c12    * a.price12m
  const srTotal = sr1fill + sr1 + sr3 + sr12

  // Row 20 (CF sheet): total revenue = ADNOC topline
  // Row 34 (CF sheet): ADNOC 50% revenue share
  const adnocCashFlow    = srTotal * ADNOC_SHARE
  const totalCollections = srTotal

  // Fills:
  //   1-Fill: 1 fill per purchase
  //   Subscriptions: active pool × once-every-3-days cadence (360 days)
  const activeSubs   = (c1 / 12) + (c3 / 4) + (c12 / 2)
  const fillsPerYear = c1fill + activeSubs * (360 / 3)

  // Cannibalization: fills × cannibalization% × profit per fill
  const cannibalizationLoss = fillsPerYear * (a.cannibalizationPct / 100) * a.profitPerFill

  return {
    salesYear:                (salesYear + salesYear1fill) * sc,
    tiers: [
      {
        label:               'Single-Fill', variant: 't1fill',
        price:               a.price1fill,
        customers:           c1fill * sc,
        subscriptionRevenue: sr1fill * sc,
        adnocShare:          sr1fill * ADNOC_SHARE * sc,
        totalCollected:      sr1fill * ADNOC_SHARE * sc,
      },
      {
        label:               '1-month', variant: 't1',
        price:               a.price1m,
        customers:           c1  * sc,
        subscriptionRevenue: sr1  * sc,
        adnocShare:          sr1  * ADNOC_SHARE * sc,
        totalCollected:      sr1  * ADNOC_SHARE * sc,
      },
      {
        label:               '3-month', variant: 't3',
        price:               a.price3m,
        customers:           c3  * sc,
        subscriptionRevenue: sr3  * sc,
        adnocShare:          sr3  * ADNOC_SHARE * sc,
        totalCollected:      sr3  * ADNOC_SHARE * sc,
      },
      {
        label:               '12-month', variant: 't12',
        price:               a.price12m,
        customers:           c12 * sc,
        subscriptionRevenue: sr12 * sc,
        adnocShare:          sr12 * ADNOC_SHARE * sc,
        totalCollected:      sr12 * ADNOC_SHARE * sc,
      },
    ],
    totalSubscriptionRevenue: srTotal               * sc,
    adnocCashFlow:            adnocCashFlow         * sc,
    totalCollections:         totalCollections      * sc,
    totalCustomers:           (c1fill + c1 + c3 + c12) * sc,
    fillsPerYear:             fillsPerYear          * sc,
    cannibalizationLoss:      cannibalizationLoss   * sc,
    netCash:                  (adnocCashFlow - cannibalizationLoss) * sc,
  }
}

export function rebalanceMix(
  vals: Pick<Assumptions, 'mix1m' | 'mix3m' | 'mix12m'>,
  changed: 'mix1m' | 'mix3m' | 'mix12m',
): Pick<Assumptions, 'mix1m' | 'mix3m' | 'mix12m'> {
  let { mix1m, mix3m, mix12m } = vals
  let diff = mix1m + mix3m + mix12m - 100
  if (diff === 0) return vals

  if (changed === 'mix1m') {
    const a = Math.min(diff, mix3m  - 5); mix3m  -= a; diff -= a
    mix12m = Math.max(5, mix12m - diff)
  } else if (changed === 'mix3m') {
    const a = Math.min(diff, mix1m  - 5); mix1m  -= a; diff -= a
    mix12m = Math.max(5, mix12m - diff)
  } else {
    const a = Math.min(diff, mix1m  - 5); mix1m  -= a; diff -= a
    mix3m  = Math.max(5, mix3m  - diff)
  }

  return { mix1m, mix3m, mix12m }
}
