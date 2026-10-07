# Presets

Presets are data files under `src/quantra_mcp/presets/` (also served as
`quantra://presets/{id}`). A preset supplies the index definition, the curve
day counter / interpolator / trait and one convention block per helper type;
the builder adds only the quote and the tenor. Every field has a provenance;
fields whose source is not an in-repo file are marked
`market standard (ISDA/CCP), not from an in-repo source` in `field_provenance`.

| Preset | Index | Helpers | Conventions | Provenance |
|---|---|---|---|---|
| `USD_SOFR_OIS` | `USD_SOFR` (Overnight, UnitedStatesGovernmentBond, Act/360, fixing 0) | ois | T+2, Annual, ModifiedFollowing, payment lag 2, Compound, no lookback/lockout; curve Act/365F LogLinear Discount | SOFR post conventions table + the byte-verified gold example (50Y DF 0.262755579831) |
| `EUR_ESTR_OIS` | `EUR_ESTR` (Overnight, TARGET, Act/360, fixing 0) | ois | T+2, Annual, ModifiedFollowing, payment lag 1, Compound | index: orchestrator `_KNOWN_OVERNIGHT_INDICES`; OIS block: market standard (ISDA/CCP), not from an in-repo source |
| `GBP_SONIA_OIS` | `GBP_SONIA` (Overnight, UnitedKingdom, Act/365F, fixing 0) | ois | T+0, Annual, ModifiedFollowing, payment lag 0, Compound | index: seeded SONIA; OIS block: market standard (ISDA/CCP), not from an in-repo source |
| `GBP_SONIA_SWAP` | `GBP_SONIA` | deposit, swap | deposit fixing 0 / UK / MF / Act/365F; swap Annual / MF / Act/365F on UK, float index SONIA | the platform's seeded "GBP SONIA OIS (BoE, daily public)" curve (`seed_demo_entities.py`) |
| `EUR_EURIBOR_6M` | `EURIBOR_6M` (Ibor 6M, TARGET, Act/360, MF, fixing 2) | deposit, fra, swap | deposits/FRAs with the Euribor conventions; swap Annual / 30/360 / MF on TARGET | index: orchestrator `_KNOWN_IBOR_INDICES`; swap fixed leg: market standard (ISDA/CCP), not from an in-repo source |
| `EUR_EURIBOR_3M` | `EURIBOR_3M` (Ibor 3M, TARGET, Act/360, MF, fixing 2) | deposit, fra, future, swap | as above plus 3M futures (`future_months` 3, convexity adjustment stated) | as above; `future_months`: market standard (ISDA/CCP), not from an in-repo source |

Bond presets (`UST_BOND`, `GILT_BOND`) are deferred: the `BondHelper`
conventions are not fully sourced from an in-repo file yet.

Presets also carry `trades` blocks, the conventions the pricing tools apply
(schedule rules, leg frequencies and day counters, vol-surface base
conventions, credit-curve helper conventions, ...). Every trade block is
sourced from a named engine fixture at the pin (`field_provenance`):

| Preset | Trade blocks | Source fixtures |
|---|---|---|
| `EUR_EURIBOR_6M` | `vanilla_swap`, `floating_rate_bond`, `fra`, `cap_floor`, `swaption` | `irs_eur_5y_payer_ois_discounted_multicurve`, `frn_eur_5y_euribor6m_flat_semiannual`, `fra_eur_6x12_long_euribor6m`, `cap_eur_10y_euribor6m_semiannual`, `swpt_eur_1y5y_payer_near_atm_physical` |
| `EUR_EURIBOR_3M` | `vanilla_swap`, `fra`, `cap_floor` | `irs_eur_4y_payer_vs_euribor3m_quarterly`, `fra_eur_3x6_long_at_forward`, `cap_eur_5y_itm_strike_2pct` |
| `USD_SOFR_OIS` | `ois_swap` (payment lag 2) | the SOFR post's OIS example; `ois_usd_3y_payer_sofr` with `payment_lag=0` |
| `EUR_ESTR_OIS` | `ois_swap` (payment lag 0) | `ois_eur_5y_payer_estr` |
| `EUR_FIXED_BOND` (trade-only) | `fixed_rate_bond`, `zero_coupon_bond`, `callable_fixed_rate_bond` | `frb_eur_5y_at_par_annual_30360`, `zcb_eur_10y_discount`, `cfrb_eur_8y_5pct_call_100_itm` |
| `EUR_CDS` (trade-only) | `cds` (quarterly TwentiethIMM, MidPoint, recovery 40%) | `cds_eur_5y_buyer_100bp_spread_curve` |
| `EUR_EQUITY` (trade-only) | `equity_option` | `eqopt_eur_call_atm_1y` |
| `EUR_HICP` (trade-only) | `zc_inflation_swap`, `yoy_inflation_swap`, `yoy_inflation_cap_floor` | `zciis_eur_5y_payer_linear_obs`, `yyiis_eur_5y_payer_annual`, `yoy_cf_eur_5y_cap_150bp_black_bites` |
