#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unbiased Empirical Out-of-Sample (OOS) Backtest Verification for KRX Quant Advisor.

Evaluates zero-lookahead portfolio performance (t-day close signal -> t+1 day open entry;
t-day EOD stop / SMA20 exit signal -> t+1 day open exit) across 2012-01-01 .. 2026-11-13
using ``/home/freshjinyong/quant_data/market_data_daily_parquet/year=*/data.parquet``.

Strategies compared:
  (A) Strict Master Plan:
      - Macro gate: Breadth50 >= 55% AND KOSPI > SMA20 -> 100% exposure, else 0% (strict cash).
      - Track 1: Stage2 + ratio52W >= 90% + ID60 <= -0.05 + ret5d <= 12% + ret60d > 0.
      - Track 2: Above SMA50/SMA20 + ratio52W >= 90% + volRatio >= 2.5 + day_ret 4..15% + ret10d <= 12%.
  (B1) Live quant_advisor_engine.py (Graduated Exposure):
      - ATTACK (Breadth50 >= 55% & KOSPI > SMA50): 100% exposure (Track 1 + Track 2).
      - SELECTIVE (Breadth50 >= 40% | KOSPI > SMA50): 60% exposure (Track 1 + Track 2).
      - DEFENSIVE (Breadth50 < 40% & KOSPI <= SMA50): 25% exposure (Track 1 only).
      - Track 1: Stage2 + ratio52W >= 85% + ID60 <= 0.02 + -6% <= ret5d <= 12.5% + ret60d > 0.
      - Track 2: Above SMA50/SMA20 + ratio52W >= 80% + (recent3Surge | volRatio >= 1.7) + ret10d <= 25%.
  (B2) Live quant_advisor_engine.py (Strict Cash Cutoff):
      - Same Track 1 & Track 2 rules as B1, ATTACK 100% / SELECTIVE 60%,
        but DEFENSIVE enforces 0% new entries (strict cash cutoff).
  (C) Unfiltered No-Regime Baseline:
      - Same Track 1 & Track 2 rules as B1, but 100% exposure at all times (no macro gate).
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from typing import Any

import duckdb
import numpy as np
import polars as pl
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from krx_master import load_krx_master  # noqa: E402

PARQUET_GLOB = "/home/freshjinyong/quant_data/market_data_daily_parquet/year=*/data.parquet"
KOSPI_CACHE_PATH = os.path.join(BASE_DIR, ".cache", "kospi_daily_2011_2026.json")
REPORT_PATH = os.path.join(BASE_DIR, ".cache", "oos_backtest_report.json")

ROUND_TRIP_COST = 0.0035  # 0.35% round-trip friction (tax + commission + slippage)
MIN_PRICE_KRW = 2000.0
MIN_VAL_MA20_KRW = 5_000_000_000.0  # 50e8 KRW (50억)
MAX_SLOTS = 8
HOLD_DAYS_MAX = 40
EOD_STOP_PCT = 0.07

WINDOWS = [
    ("Full_2012_2026", "20120101", "20261113"),
    ("OOS_5Y_2022_2026", "20220101", "20261113"),
    ("Recent_Bear_2026", "20260826", "20261113"),
]


def load_kospi_index_series() -> pl.DataFrame:
    """Load daily KOSPI index closes (2011-01-01 .. 2026-11-13) with local JSON cache.

    Combines Naver fchart (last 3000 trading days, 2014-08-21 .. 2026-11-13) and
    Yahoo Finance ``^KS11`` (2011-01-01 .. 2014-08-20) so SMA20/SMA50 are valid
    from 2012-01-01 onward.
    """
    if os.path.exists(KOSPI_CACHE_PATH):
        with open(KOSPI_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached and cached[-1]["trade_date"] >= "20261113":
            df = pl.DataFrame(cached)
            return _add_kospi_smas(df)

    date_close: dict[str, float] = {}

    # 1) Yahoo ^KS11 for 2011-01-01 .. 2014-12-31
    y_url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11"
        "?period1=1293840000&period2=1420070400&interval=1d"
    )
    r_y = requests.get(y_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r_y.raise_for_status()
    res_y = r_y.json()["chart"]["result"][0]
    ts_list = res_y["timestamp"]
    closes_y = res_y["indicators"]["quote"][0]["close"]
    kst = dt.timezone(dt.timedelta(hours=9))
    for ts, cl in zip(ts_list, closes_y):
        if cl is not None and cl > 0:
            d_str = dt.datetime.fromtimestamp(ts, kst).strftime("%Y%m%d")
            date_close[d_str] = float(cl)

    # 2) Naver fchart KOSPI (latest 3000 trading days through 2026-11-13)
    n_url = "https://fchart.stock.naver.com/sise.nhn?symbol=KOSPI&timeframe=day&count=3000&requestType=0"
    r_n = requests.get(n_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r_n.encoding = "euc-kr"
    root = ET.fromstring(r_n.text)
    chartdata = root.find("chartdata")
    if chartdata is not None:
        for item in chartdata.findall("item"):
            parts = item.attrib.get("data", "").split("|")
            if len(parts) >= 5:
                d_str = parts[0].strip()
                cl = float(parts[4])
                if d_str <= "20261113" and cl > 0:
                    date_close[d_str] = cl

    rows = [{"trade_date": d, "kospi_close": c} for d, c in sorted(date_close.items()) if d <= "20261113"]
    os.makedirs(os.path.dirname(KOSPI_CACHE_PATH), exist_ok=True)
    with open(KOSPI_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f)

    return _add_kospi_smas(pl.DataFrame(rows))


def _add_kospi_smas(df: pl.DataFrame) -> pl.DataFrame:
    """Compute KOSPI SMA20 and SMA50 without lookahead."""
    return df.sort("trade_date").with_columns([
        pl.col("kospi_close").rolling_mean(20).alias("kospi_sma20"),
        pl.col("kospi_close").rolling_mean(50).alias("kospi_sma50"),
    ]).with_columns([
        (pl.col("kospi_close") > pl.col("kospi_sma20")).alias("kospi_above_sma20"),
        (pl.col("kospi_close") > pl.col("kospi_sma50")).alias("kospi_above_sma50"),
    ])


def build_common_stock_filter() -> tuple[list[str], dict[str, str]]:
    """Return allowed parquet ``Axxxxxx`` codes excluding SPAC, ETF, ETN, preferred, and halted issues.

    Uses ``krx_master.load_krx_master()`` for active issues plus strict 6-digit ``0``-suffix
    heuristics for historical delisted issues not present in the current master.
    """
    master = load_krx_master()
    con = duckdb.connect()
    try:
        pq_codes = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT code FROM read_parquet('{PARQUET_GLOB}')"
            ).fetchall()
        ]
    finally:
        con.close()

    allowed: list[str] = []
    code_names: dict[str, str] = {}
    for a_code in pq_codes:
        raw = a_code[1:] if a_code.startswith("A") else a_code
        info = master.get(raw)
        if info is not None:
            if info.get("is_common"):
                allowed.append(a_code)
                code_names[a_code] = info.get("name", raw)
        else:
            # Historical delisted fallback: 6 digits ending in '0' excludes preferred ('5','7','9','K','L') & alphanumeric SPAC/ETN
            if len(raw) == 6 and raw.isdigit() and raw[-1] == "0":
                allowed.append(a_code)
                code_names[a_code] = raw
    return sorted(allowed), code_names


def load_and_compute_features(allowed_codes: list[str], kospi_df: pl.DataFrame) -> pl.DataFrame:
    """Load parquet daily bars (2011..2026-11-13) and compute exact point-in-time indicators."""
    parquet_files = sorted(glob.glob(PARQUET_GLOB))
    con = duckdb.connect()
    try:
        arrow_tbl = con.execute(
            """
            SELECT
                code,
                trade_date,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume,
                CAST(trading_value AS DOUBLE) AS trading_value
            FROM read_parquet(?)
            WHERE trade_date >= '20110101'
              AND trade_date <= '20261113'
              AND close > 0
              AND code IN (SELECT unnest(?))
            ORDER BY code, trade_date ASC
            """,
            [parquet_files, allowed_codes],
        ).fetch_arrow_table()
    finally:
        con.close()

    df = pl.from_arrow(arrow_tbl)
    df = df.with_columns([
        pl.when(pl.col("open") <= 0).then(pl.col("close")).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("high") <= 0).then(pl.col("close")).otherwise(pl.col("high")).alias("high"),
        pl.when(pl.col("low") <= 0).then(pl.col("close")).otherwise(pl.col("low")).alias("low"),
        pl.when((pl.col("trading_value").is_null()) | (pl.col("trading_value") <= 0))
        .then(pl.col("close") * pl.col("volume"))
        .otherwise(pl.col("trading_value"))
        .alias("trading_value"),
    ])

    df = df.sort(["code", "trade_date"]).with_columns([
        pl.col("close").shift(1).over("code").alias("prev_close"),
        pl.col("volume").shift(1).rolling_mean(20).over("code").alias("vol_ma20"),
        pl.col("trading_value").shift(1).rolling_mean(20).over("code").alias("val_ma20"),
    ])

    df = df.with_columns([
        (pl.col("close") / pl.col("prev_close") - 1.0).alias("ret_1d"),
        (pl.col("close") / pl.col("close").shift(5).over("code") - 1.0).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(10).over("code") - 1.0).alias("ret_10d"),
        (pl.col("close") / pl.col("close").shift(60).over("code") - 1.0).alias("ret_60d"),
        (pl.col("close") > pl.col("prev_close")).cast(pl.Float64).alias("is_pos"),
        (pl.col("close") < pl.col("prev_close")).cast(pl.Float64).alias("is_neg"),
        pl.col("close").rolling_mean(20).over("code").alias("sma20"),
        pl.col("close").rolling_mean(50).over("code").alias("sma50"),
        pl.col("close").rolling_mean(150).over("code").alias("sma150"),
        pl.col("high").rolling_max(250).over("code").alias("high250"),
    ])

    df = df.with_columns([
        pl.col("sma50").shift(20).over("code").alias("sma50_prev20"),
        pl.col("ret_1d").rolling_std(60).over("code").alias("vol_60d"),
        pl.col("is_pos").rolling_sum(60).over("code").alias("pos_days_60"),
        pl.col("is_neg").rolling_sum(60).over("code").alias("neg_days_60"),
        (pl.col("close") / pl.col("high250") * 100.0).alias("ratio52w"),
        (pl.col("volume") / pl.col("vol_ma20")).alias("vol_ratio"),
    ])

    df = df.with_columns([
        (
            pl.when(pl.col("ret_60d") >= 0).then(1.0).otherwise(-1.0)
            * ((pl.col("neg_days_60") - pl.col("pos_days_60")) / 60.0)
        ).alias("fip_id60"),
        (
            (pl.col("close") > pl.col("sma50"))
            & (pl.col("sma50") > pl.col("sma150"))
            & (pl.col("sma50") >= pl.col("sma50_prev20") * 0.995)
            & (pl.col("close") > pl.col("sma20"))
        ).alias("stage2"),
        ((pl.col("close") > pl.col("open")) & (pl.col("ret_1d") >= 0.03) & (pl.col("vol_ratio") >= 1.8))
        .cast(pl.Int8)
        .alias("is_bull_surge"),
        pl.when((pl.col("close") > pl.col("open")) & (pl.col("ret_1d") >= 0.03) & (pl.col("vol_ratio") >= 1.8))
        .then(pl.col("vol_ratio"))
        .otherwise(0.0)
        .alias("bull_surge_vr"),
        (pl.col("close") > pl.col("sma50")).cast(pl.Float64).alias("above_sma50"),
    ])

    df = df.with_columns([
        (pl.col("is_bull_surge").rolling_max(3).over("code") == 1).alias("recent3_surge"),
        pl.max_horizontal(
            pl.col("vol_ratio"),
            pl.col("bull_surge_vr").rolling_max(3).over("code"),
        ).alias("recent3_max_vr"),
    ])

    # Market Breadth50: percentage of active common stocks (with >=150d history) above SMA50
    breadth_df = (
        df.filter(pl.col("sma150").is_not_null())
        .group_by("trade_date")
        .agg((pl.col("above_sma50").mean() * 100.0).alias("breadth50_pct"))
    )

    df = df.join(breadth_df, on="trade_date", how="left")
    df = df.join(
        kospi_df.select(["trade_date", "kospi_above_sma20", "kospi_above_sma50"]),
        on="trade_date",
        how="left",
    )
    return df


def build_candidate_schedules(df: pl.DataFrame, all_dates: list[str]) -> dict[str, dict[str, list[tuple[str, float, str]]]]:
    """Pre-compute t-day close signal -> t+1 day open candidate queues for each strategy.

    Returns ``schedules[strategy_name][entry_date] = [(code, score, track_tag), ...]``.
    """
    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    base_liq = (
        (pl.col("trade_date") >= "20120101")
        & (pl.col("close") >= MIN_PRICE_KRW)
        & (pl.col("val_ma20") >= MIN_VAL_MA20_KRW)
        & pl.col("sma150").is_not_null()
        & pl.col("high250").is_not_null()
        & pl.col("fip_id60").is_not_null()
    )
    liq_df = df.filter(base_liq)

    # --- (A) Strict Master Plan Candidates ---
    a_t1 = (
        liq_df.filter(
            pl.col("stage2")
            & (pl.col("ratio52w") >= 90.0)
            & (pl.col("fip_id60") <= -0.05)
            & (pl.col("ret_5d") <= 0.12)
            & (pl.col("ret_60d") > 0.0)
        )
        .with_columns(
            (
                (pl.col("ratio52w") - 80.0) * 2.2
                + (-pl.col("fip_id60") * 100.0) * 1.5
                + pl.max_horizontal(pl.lit(0.0), (4.0 - pl.col("vol_60d") * 100.0) * 8.0)
            ).alias("score"),
            pl.lit("T1").alias("track"),
        )
        .select(["trade_date", "code", "score", "track"])
    )
    a_t2 = (
        liq_df.filter(
            (pl.col("close") > pl.col("sma50"))
            & (pl.col("close") > pl.col("sma20"))
            & (pl.col("ratio52w") >= 90.0)
            & (pl.col("vol_ratio") >= 2.5)
            & (pl.col("ret_1d") >= 0.04)
            & (pl.col("ret_1d") <= 0.15)
            & (pl.col("ret_10d") <= 0.12)
        )
        .with_columns(
            (
                pl.col("vol_ratio") * 18.0
                + (pl.col("ratio52w") - 75.0) * 1.5
                + (-pl.col("fip_id60") * 50.0)
            ).alias("score"),
            pl.lit("T2").alias("track"),
        )
        .select(["trade_date", "code", "score", "track"])
    )
    a_all = pl.concat([a_t1, a_t2]).sort(["trade_date", "score"], descending=[False, True]).unique(
        subset=["trade_date", "code"], keep="first"
    )

    # --- (B/C) Live quant_advisor_engine.py Candidates ---
    b_t1 = (
        liq_df.filter(
            pl.col("stage2")
            & (pl.col("ratio52w") >= 85.0)
            & (pl.col("fip_id60") <= 0.02)
            & (pl.col("ret_5d") >= -0.06)
            & (pl.col("ret_5d") <= 0.125)
            & (pl.col("ret_60d") > 0.0)
        )
        .with_columns(
            (
                (pl.col("ratio52w") - 80.0) * 2.2
                + (-pl.col("fip_id60") * 100.0) * 1.5
                + pl.max_horizontal(pl.lit(0.0), (4.0 - pl.col("vol_60d") * 100.0) * 8.0)
            ).alias("score"),
            pl.lit("T1").alias("track"),
        )
        .select(["trade_date", "code", "score", "track"])
    )
    b_t2 = (
        liq_df.filter(
            (pl.col("close") > pl.col("sma50"))
            & (pl.col("close") > pl.col("sma20"))
            & (pl.col("ratio52w") >= 80.0)
            & (pl.col("recent3_surge") | (pl.col("vol_ratio") >= 1.7))
            & (pl.col("ret_10d") <= 0.25)
        )
        .with_columns(
            (
                pl.col("recent3_max_vr") * 18.0
                + (pl.col("ratio52w") - 75.0) * 1.5
                + (-pl.col("fip_id60") * 50.0)
            ).alias("score"),
            pl.lit("T2").alias("track"),
        )
        .select(["trade_date", "code", "score", "track"])
    )
    b_all = pl.concat([b_t1, b_t2]).sort(["trade_date", "score"], descending=[False, True]).unique(
        subset=["trade_date", "code"], keep="first"
    )

    def _to_entry_map(sig_df: pl.DataFrame) -> dict[str, list[tuple[str, float, str]]]:
        out: dict[str, list[tuple[str, float, str]]] = {}
        sorted_df = sig_df.sort(["trade_date", "score"], descending=[False, True])
        for t_date, code, score, track in sorted_df.iter_rows():
            idx = date_to_idx.get(t_date)
            if idx is None or idx + 1 >= len(all_dates):
                continue
            e_date = all_dates[idx + 1]
            lst = out.setdefault(e_date, [])
            if len(lst) < 16:  # top 8 T1 + top 8 T2 max
                lst.append((code, float(score), track))
        return out

    return {
        "A_cands": _to_entry_map(a_all),
        "B_cands": _to_entry_map(b_all),
    }


def simulate_portfolio(
    all_dates: list[str],
    bar_dict: dict[tuple[str, str], tuple[float, float, float, float]],
    regime_by_entry_date: dict[str, tuple[float, bool]],
    cands_by_entry: dict[str, list[tuple[str, float, str]]],
) -> tuple[list[tuple[str, float, float]], list[dict[str, Any]]]:
    """Simulate zero-lookahead portfolio execution on ``all_dates``.

    ``regime_by_entry_date[e_date] = (target_exposure_0_to_1, track1_only_flag)``
    determined at ``t`` close for ``t+1`` open execution.
    """
    cash = 1.0
    positions: list[dict[str, Any]] = []
    daily_eq: list[tuple[str, float, float]] = []
    trades: list[dict[str, Any]] = []

    for d in all_dates:
        # 1) Execute pending exits at today's open (t-day EOD signal -> t+1 open exit)
        still_open: list[dict[str, Any]] = []
        for pos in positions:
            key = (d, pos["code"])
            if key not in bar_dict:
                pos["hold_days"] += 1
                still_open.append(pos)
                continue
            o, cl, pc, s20 = bar_dict[key]
            if pos["exit_next_open"] and o > 0:
                proceeds = pos["shares"] * o * (1.0 - ROUND_TRIP_COST)
                cash += proceeds
                net_ret = (o / pos["entry_px"]) * (1.0 - ROUND_TRIP_COST) - 1.0
                trades.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": d,
                    "code": pos["code"],
                    "track": pos["track"],
                    "net_ret": net_ret,
                })
            else:
                still_open.append(pos)
        positions = still_open

        # 2) New entries at today's open subject to t-day macro exposure cap
        target_exp, t1_only = regime_by_entry_date.get(d, (0.0, False))
        invested_est = sum(p["shares"] * p["last_close"] for p in positions)
        curr_eq = cash + invested_est
        allowed_invested = curr_eq * target_exp
        avail_budget = max(0.0, allowed_invested - invested_est)
        avail_slots = MAX_SLOTS - len(positions)

        if avail_slots > 0 and avail_budget > 1e-6 and cash > 1e-6 and d in cands_by_entry:
            held_codes = {p["code"] for p in positions}
            slot_cap = curr_eq / MAX_SLOTS
            for code, _score, track in cands_by_entry[d]:
                if avail_slots <= 0 or avail_budget <= 1e-6 or cash <= 1e-6:
                    break
                if t1_only and track != "T1":
                    continue
                if code in held_codes:
                    continue
                key = (d, code)
                if key not in bar_dict:
                    continue
                o, cl, pc, s20 = bar_dict[key]
                if o <= 0:
                    continue
                # Skip limit-up / limit-down open gaps
                if pc is not None and pc > 0 and (o >= pc * 1.295 or o <= pc * 0.705):
                    continue
                alloc = min(cash, avail_budget, slot_cap)
                if alloc <= 1e-6:
                    continue
                shares = alloc / o
                cash -= alloc
                avail_budget -= alloc
                positions.append({
                    "code": code,
                    "track": track,
                    "entry_date": d,
                    "entry_px": o,
                    "shares": shares,
                    "hold_days": 0,
                    "exit_next_open": False,
                    "last_close": cl,
                })
                held_codes.add(code)
                avail_slots -= 1

        # 3) EOD mark-to-market & exit signal check (executed at t+1 open)
        for pos in positions:
            key = (d, pos["code"])
            if key not in bar_dict:
                pos["hold_days"] += 1
                continue
            o, cl, pc, s20 = bar_dict[key]
            pos["hold_days"] += 1
            pos["last_close"] = cl

            if cl <= pos["entry_px"] * (1.0 - EOD_STOP_PCT):
                pos["exit_next_open"] = True
            elif pos["hold_days"] >= HOLD_DAYS_MAX:
                pos["exit_next_open"] = True
            elif pos["hold_days"] >= 2 and s20 is not None and not math.isnan(s20) and cl < s20:
                pos["exit_next_open"] = True

        eod_eq = cash + sum(p["shares"] * p["last_close"] for p in positions)
        cash_weight = cash / eod_eq if eod_eq > 0 else 1.0
        daily_eq.append((d, eod_eq, cash_weight))

    return daily_eq, trades


def compute_window_stats(
    daily_eq: list[tuple[str, float, float]],
    trades: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Compute exact trade & equity-curve metrics for ``[start_date, end_date]``."""
    sub_eq = [(d, eq, cw) for d, eq, cw in daily_eq if start_date <= d <= end_date]
    prev_eqs = [eq for d, eq, _ in daily_eq if d < start_date]
    base_eq = prev_eqs[-1] if prev_eqs else (sub_eq[0][1] if sub_eq else 1.0)

    eq_arr = np.array([eq for _, eq, _ in sub_eq], dtype=np.float64)
    cw_arr = np.array([cw for _, _, cw in sub_eq], dtype=np.float64)

    if len(eq_arr) > 0:
        total_ret_pct = (eq_arr[-1] / base_eq - 1.0) * 100.0
        years = len(eq_arr) / 248.0
        cagr_pct = ((eq_arr[-1] / base_eq) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
        curve = np.insert(eq_arr, 0, base_eq)
        peaks = np.maximum.accumulate(curve)[1:]
        mdd_pct = float(np.min(eq_arr / peaks - 1.0) * 100.0)
        cash_time_pct = float(np.mean(cw_arr >= 0.99) * 100.0)
        avg_cash_pct = float(np.mean(cw_arr) * 100.0)
    else:
        total_ret_pct = cagr_pct = mdd_pct = cash_time_pct = avg_cash_pct = 0.0

    w_trades = [t["net_ret"] for t in trades if start_date <= t["entry_date"] <= end_date]
    r_arr = np.array(w_trades, dtype=np.float64) * 100.0  # in %
    n_trades = int(len(r_arr))
    wins = r_arr[r_arr > 0.0]
    losses = r_arr[r_arr <= 0.0]

    win_rate = float(len(wins) / n_trades * 100.0) if n_trades > 0 else 0.0
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    payoff = float(avg_win / abs(avg_loss)) if len(wins) > 0 and len(losses) > 0 and avg_loss != 0 else 0.0
    pf = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else (99.9 if len(wins) > 0 else 0.0)
    net_ev = float(np.mean(r_arr)) if n_trades > 0 else 0.0

    return {
        "N": n_trades,
        "WinRate": round(win_rate, 2),
        "AvgWin": round(avg_win, 2),
        "AvgLoss": round(avg_loss, 2),
        "Payoff": round(payoff, 2),
        "PF": round(pf, 2),
        "NetEV": round(net_ev, 2),
        "Return": round(float(total_ret_pct), 2),
        "CAGR": round(float(cagr_pct), 2),
        "MDD": round(mdd_pct, 2),
        "CashTimePct": round(cash_time_pct, 2),
        "AvgCashPct": round(avg_cash_pct, 2),
    }


def main() -> None:
    print("[1/5] Loading KOSPI index & KRX common stock universe...")
    kospi_df = load_kospi_index_series()
    allowed_codes, _ = build_common_stock_filter()
    print(f"  -> Common stock universe: {len(allowed_codes)} codes")

    print("[2/5] Loading parquet daily bars & computing point-in-time features...")
    df = load_and_compute_features(allowed_codes, kospi_df)
    all_dates = (
        df.filter((pl.col("trade_date") >= "20120101") & (pl.col("trade_date") <= "20261113"))["trade_date"]
        .unique()
        .sort()
        .to_list()
    )
    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    print(f"  -> Trading days (2012-01-01 .. 2026-11-13): {len(all_dates)}")

    print("[3/5] Building zero-lookahead bar lookup & signal schedules...")
    bar_sub = df.filter(pl.col("trade_date") >= "20120101").select([
        "code", "trade_date", "open", "close", "prev_close", "sma20"
    ])
    bar_dict: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    for code, d, o, cl, pc, s20 in bar_sub.iter_rows():
        bar_dict[(d, code)] = (float(o), float(cl), float(pc) if pc is not None else 0.0, float(s20) if s20 is not None else float("nan"))

    macro_df = (
        df.filter(pl.col("trade_date") >= "20120101")
        .select(["trade_date", "breadth50_pct", "kospi_above_sma20", "kospi_above_sma50"])
        .unique(subset=["trade_date"])
        .sort("trade_date")
    )

    reg_a: dict[str, tuple[float, bool]] = {}
    reg_b_grad: dict[str, tuple[float, bool]] = {}
    reg_b_strict: dict[str, tuple[float, bool]] = {}
    reg_c_none: dict[str, tuple[float, bool]] = {}

    for t_date, b50, kp20, kp50 in macro_df.iter_rows():
        idx = date_to_idx.get(t_date)
        if idx is None or idx + 1 >= len(all_dates):
            continue
        e_date = all_dates[idx + 1]
        b50_v = float(b50) if b50 is not None else 0.0
        kp20_b = bool(kp20) if kp20 is not None else False
        kp50_b = bool(kp50) if kp50 is not None else False

        # (A) Strict Master Plan: Breadth50 >= 55% & KOSPI > SMA20
        reg_a[e_date] = (1.0, False) if (b50_v >= 55.0 and kp20_b) else (0.0, False)

        # (B1/B2) Live quant_advisor_engine.py thresholds
        if b50_v >= 55.0 and kp50_b:
            reg_b_grad[e_date] = (1.0, False)
            reg_b_strict[e_date] = (1.0, False)
        elif b50_v >= 40.0 or kp50_b:
            reg_b_grad[e_date] = (0.60, False)
            reg_b_strict[e_date] = (0.60, False)
        else:
            reg_b_grad[e_date] = (0.25, True)   # DEFENSIVE: 25% Track 1 only
            reg_b_strict[e_date] = (0.0, False)  # Strict cash cutoff

        # (C) Unfiltered No-Regime Baseline
        reg_c_none[e_date] = (1.0, False)

    schedules = build_candidate_schedules(df, all_dates)

    strategies = [
        ("A_Strict_Master_Plan", reg_a, schedules["A_cands"]),
        ("B1_Live_Graduated_25pct", reg_b_grad, schedules["B_cands"]),
        ("B2_Live_StrictCash_0pct", reg_b_strict, schedules["B_cands"]),
        ("C_Unfiltered_NoRegime", reg_c_none, schedules["B_cands"]),
    ]

    print("[4/5] Running portfolio simulations...")
    report: dict[str, Any] = {
        "metadata": {
            "data_range": f"{all_dates[0]}..{all_dates[-1]}",
            "trading_days": len(all_dates),
            "universe_codes": len(allowed_codes),
            "round_trip_cost_pct": ROUND_TRIP_COST * 100.0,
            "max_slots": MAX_SLOTS,
            "eod_stop_pct": EOD_STOP_PCT * 100.0,
            "hold_days_max": HOLD_DAYS_MAX,
        },
        "results": {},
    }

    for name, reg_map, cands in strategies:
        daily_eq, trades = simulate_portfolio(all_dates, bar_dict, reg_map, cands)
        win_stats = {}
        for w_name, w_start, w_end in WINDOWS:
            win_stats[w_name] = compute_window_stats(daily_eq, trades, w_start, w_end)
        report["results"][name] = win_stats

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"[5/5] Saved JSON report to {REPORT_PATH}\n")
    for w_name, w_start, w_end in WINDOWS:
        print(f"=== {w_name} ({w_start} .. {w_end}) ===")
        header = (
            f"{'Strategy':<26} {'N':>5} {'Win%':>6} {'AvgW%':>6} {'AvgL%':>6} "
            f"{'Payoff':>6} {'PF':>5} {'NetEV%':>7} {'Ret%':>9} {'CAGR%':>7} {'MDD%':>7} {'Cash0%':>7}"
        )
        print(header)
        print("-" * len(header))
        for s_name, _, _ in strategies:
            st = report["results"][s_name][w_name]
            print(
                f"{s_name:<26} {st['N']:>5d} {st['WinRate']:>6.2f} {st['AvgWin']:>6.2f} {st['AvgLoss']:>6.2f} "
                f"{st['Payoff']:>6.2f} {st['PF']:>5.2f} {st['NetEV']:>7.2f} {st['Return']:>9.2f} "
                f"{st['CAGR']:>7.2f} {st['MDD']:>7.2f} {st['CashTimePct']:>7.2f}"
            )
        print()


if __name__ == "__main__":
    main()
