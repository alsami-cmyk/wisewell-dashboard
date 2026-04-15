export type Scope = 'single' | 'network'

export interface Assumptions {
  salesPerDay:        number  // subscription sales/day
  salesPerDay1fill:   number  // 1-fill sales/day (separate SKU)
  locations:          number
  mix1m:              number  // percentage 0–100, subscriptions only
  mix3m:              number
  mix12m:             number
  price1fill:         number  // AED
  price1m:            number
  price3m:            number
  price12m:           number
  cannibalizationPct: number  // 0 | 25 | 50 | 75
  profitPerFill:      number  // AED
}

export type TierVariant = 't1fill' | 't1' | 't3' | 't12'

export interface TierResult {
  label:               string
  variant:             TierVariant
  price:               number
  customers:           number
  subscriptionRevenue: number
  adnocShare:          number
  totalCollected:      number
}

export interface CalcResult {
  salesYear:                number
  tiers:                    TierResult[]
  totalSubscriptionRevenue: number
  adnocCashFlow:            number
  totalCollections:         number
  totalCustomers:           number
  fillsPerYear:             number
  cannibalizationLoss:      number
  netCash:                  number
}
