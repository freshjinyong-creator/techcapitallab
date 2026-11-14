# OOS Backtest Report (2012-01-02 ~ 2026-11-13)

Source: `.cache/oos_backtest_report.json`

## 1. Test Universe & Execution Assumptions

| Parameter | Value |
| --- | --- |
| Data Range | `20120102..20261113` |
| Trading Days | `3,652` |
| Universe Codes | `2,416` |
| Round-Trip Cost | `0.35%` |
| Max Concurrent Slots | `8` |
| EOD Hard Stop | `-7.0%` |
| Max Hold Days | `40` |

---

## 2. Full Period (`Full_2012_2026`: 2012-01-02 ~ 2026-11-13)

| Strategy | N | WinRate (%) | AvgWin (%) | AvgLoss (%) | Payoff | PF | NetEV (%) | Return (%) | CAGR (%) | MDD (%) | CashTimePct (%) | AvgCashPct (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `A_Strict_Master_Plan` | 891 | 32.21 | 13.52 | -4.95 | 2.73 | 1.30 | 1.00 | 122.22 | 5.57 | -47.16 | 44.28 | 61.07 |
| `B1_Live_Graduated_25pct` | 2,068 | 25.44 | 17.56 | -6.70 | 2.62 | 0.89 | -0.53 | -81.27 | -10.75 | -83.36 | 0.14 | 38.38 |
| `B2_Live_StrictCash_0pct` | 1,764 | 26.19 | 19.36 | -6.92 | 2.80 | 0.99 | -0.03 | -49.79 | -4.57 | -64.49 | 12.10 | 41.75 |
| `C_Unfiltered_NoRegime` | 2,705 | 25.77 | 16.18 | -7.01 | 2.31 | 0.80 | -1.04 | -98.74 | -25.68 | -98.80 | 0.05 | 1.16 |

---

## 3. 5-Year Out-of-Sample (`OOS_5Y_2022_2026`: 2022 ~ 2026-11-13)

| Strategy | N | WinRate (%) | AvgWin (%) | AvgLoss (%) | Payoff | PF | NetEV (%) | Return (%) | CAGR (%) | MDD (%) | CashTimePct (%) | AvgCashPct (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `A_Strict_Master_Plan` | 208 | 35.58 | 17.26 | -4.98 | 3.46 | 1.91 | 2.93 | 85.30 | 13.73 | -16.18 | 53.41 | 69.76 |
| `B1_Live_Graduated_25pct` | 726 | 22.73 | 17.68 | -7.16 | 2.47 | 0.73 | -1.52 | -74.35 | -24.71 | -75.66 | 0.00 | 42.59 |
| `B2_Live_StrictCash_0pct` | 593 | 25.13 | 19.11 | -7.71 | 2.48 | 0.83 | -0.97 | -62.20 | -18.37 | -62.38 | 16.15 | 46.63 |
| `C_Unfiltered_NoRegime` | 981 | 21.92 | 15.88 | -7.54 | 2.11 | 0.59 | -2.41 | -95.59 | -47.85 | -95.82 | 0.00 | 0.78 |

---

## 4. Recent Bear Regime (`Recent_Bear_2026`: 2026-08-26 ~ 2026-11-13)

| Strategy | N | WinRate (%) | AvgWin (%) | AvgLoss (%) | Payoff | PF | NetEV (%) | Return (%) | CAGR (%) | MDD (%) | CashTimePct (%) | AvgCashPct (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `A_Strict_Master_Plan` | 24 | 20.83 | 5.39 | -5.99 | 0.90 | 0.24 | -3.62 | -10.72 | -40.59 | -12.52 | 29.63 | 44.02 |
| `B1_Live_Graduated_25pct` | 33 | 18.18 | 6.66 | -7.53 | 0.88 | 0.20 | -4.95 | -13.08 | -47.46 | -19.75 | 0.00 | 41.62 |
| `B2_Live_StrictCash_0pct` | 24 | 20.83 | 6.00 | -6.76 | 0.89 | 0.23 | -4.10 | -11.03 | -41.52 | -13.72 | 24.07 | 49.57 |
| `C_Unfiltered_NoRegime` | 39 | 23.08 | 8.57 | -7.02 | 1.22 | 0.37 | -3.42 | -10.17 | -38.90 | -16.91 | 0.00 | 0.21 |

---

## 5. Cold Quantitative Findings (Zero Optimism Bias)

1. **Cash Gate Is the Primary Edge Driver**: `A_Strict_Master_Plan` is the only variant with positive net expectancy over both Full (`PF 1.30`, `NetEV +1.00%`, `+122.22%`) and 5Y OOS (`PF 1.91`, `NetEV +2.93%`, `+85.30%`, `CAGR +13.73%`). It achieves this by staying 100% in cash `44.28%` of the time in Full and `53.41%` of the time in 5Y OOS (`AvgCashPct` `61.07%` / `69.76%`).
2. **Relaxing Breadth < 55% Destroys Expectancy**: Allowing graduated `25%` exposure during weak breadth (`B1_Live_Graduated_25pct`) drops `CashTimePct` to `0.14%` (Full) and `0.00%` (5Y OOS), turning PF negative (`0.89` Full, `0.73` 5Y OOS) and producing `-81.27%` (Full) and `-74.35%` (5Y OOS) cumulative returns with `-83.36%` / `-75.66%` MDD.
3. **Unfiltered Entries Are Catastrophic**: `C_Unfiltered_NoRegime` suffers `-98.74%` Full return (`MDD -98.80%`, `PF 0.80`) and `-95.59%` 5Y OOS return (`PF 0.59`, `NetEV -2.41%`).
4. **Severe Bear Regime Vulnerability Remains**: Even `A_Strict_Master_Plan` lost `-10.72%` (`PF 0.24`, `WinRate 20.83%`, `Payoff 0.90`, `MDD -12.52%`) during `Recent_Bear_2026` (`2026-08-26 ~ 2026-11-13`), and carries a `-47.16%` Full-period MDD (`CAGR +5.57%`). Strict `0%` equity exposure below `Breadth < 55%` and pre-entry KIS `.mst` + public disclosure Kill-Switch filtering are mandatory risk controls, not optional enhancements.
