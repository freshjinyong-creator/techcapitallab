#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIS REST API Incremental Daily Bar Backfill & Unified 15Y+ Parquet Builder
- Base dataset: /tmp/krx_daily_15y.parquet (never touches local_market.db)
- Incremental backfill: KIS FHKST03010100 (20260615 ~ latest, FID_ORG_ADJ_PRC=0)
- Resumable SQLite state/raw cache: /home/freshjinyong/techcapitallab/.cache/kis_backfill_daily.db
- Overlap (20260619~20260825) split/adjustment audit & correction (adj_ratio)
- Output: /home/freshjinyong/quant_data/market_data_daily_parquet/year=YYYY/data.parquet (10 cols, SNAPPY, atomic swap)
"""

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import duckdb
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from kis_client import KISClient, _to_float  # noqa: E402

BASE_PARQUET_PATH = "/tmp/krx_daily_15y.parquet"
BACKFILL_DB_PATH = os.path.join(BASE_DIR, ".cache", "kis_backfill_daily.db")
QUANT_DATA_DIR = os.environ.get("QUANT_DATA_DIR", "/home/freshjinyong/quant_data")
OUTPUT_PARQUET_DIR = os.path.join(QUANT_DATA_DIR, "market_data_daily_parquet")

OVERLAP_START = "20260619"
OVERLAP_END = "20260825"
BACKFILL_START = "20260826"
KIS_QUERY_START = "20260615"


def init_backfill_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backfill_status (
            code TEXT PRIMARY KEY,
            kis_code TEXT NOT NULL,
            mrkt_div_code TEXT NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER DEFAULT 0,
            error_msg TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backfill_bars (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            change_value REAL NOT NULL,
            volume REAL NOT NULL,
            trading_value REAL NOT NULL,
            mod_yn TEXT DEFAULT 'N',
            prtt_rate REAL DEFAULT 0.0,
            PRIMARY KEY (code, trade_date)
        )
        """
    )
    conn.commit()
    return conn


def load_universe_from_parquet(parquet_path: str, include_etn: bool = False) -> list:
    if not os.path.exists(parquet_path):
        raise FileNotFoundError(f"Base parquet not found: {parquet_path}")
    con = duckdb.connect()
    try:
        where_clause = "WHERE code LIKE 'A%' OR code LIKE 'Q%'" if include_etn else "WHERE code LIKE 'A%'"
        rows = con.execute(
            f"""
            SELECT DISTINCT code
            FROM read_parquet('{parquet_path}')
            {where_clause}
            ORDER BY code
            """
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def fetch_kis_symbol_100d(
    kis: KISClient,
    full_code: str,
    end_date: str,
    etn_mrkt_div: str = "J",
) -> tuple:
    """
    Fetch single 100-trading-day page from KIS FHKST03010100 (20260615 ~ end_date).
    Returns (kis_code, mrkt_div, bars_list).
    """
    is_etn = full_code.startswith("Q")
    kis_code = full_code[1:] if len(full_code) == 7 and full_code[0] in ("A", "Q") else full_code
    mrkt_div = etn_mrkt_div if is_etn else "J"

    data = kis._request_get(
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        "FHKST03010100",
        {
            "FID_COND_MRKT_DIV_CODE": mrkt_div,
            "FID_INPUT_ISCD": kis_code,
            "FID_INPUT_DATE_1": KIS_QUERY_START,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        },
    )
    bars = []
    for row in data.get("output2") or []:
        dt = str(row.get("stck_bsop_date", "")).strip()
        c = _to_float(row.get("stck_clpr"))
        if not dt or c <= 0:
            continue
        o = _to_float(row.get("stck_oprc"), c) or c
        h = _to_float(row.get("stck_hgpr"), c) or c
        l = _to_float(row.get("stck_lwpr"), c) or c
        v = _to_float(row.get("acml_vol"))
        val = _to_float(row.get("acml_tr_pbmn"))
        if val <= 0 and v > 0:
            val = c * v
        prdy_vrss = _to_float(row.get("prdy_vrss"), 0.0)
        sign = str(row.get("prdy_vrss_sign", "3")).strip()
        if sign in ("4", "5") and prdy_vrss > 0:
            prdy_vrss = -prdy_vrss
        bars.append({
            "code": full_code,
            "trade_date": dt,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "change_value": prdy_vrss,
            "volume": v,
            "trading_value": val,
            "mod_yn": str(row.get("mod_yn", "N")).strip() or "N",
            "prtt_rate": _to_float(row.get("prtt_rate"), 0.0),
        })
    return kis_code, mrkt_div, bars


def run_backfill_fetch(
    kis: KISClient,
    conn: sqlite3.Connection,
    codes: list,
    workers: int = 1,
    etn_mrkt_div: str = "J",
):
    done_codes = {
        r[0]
        for r in conn.execute("SELECT code FROM backfill_status WHERE status IN ('ok', 'empty')").fetchall()
    }
    pending = [c for c in codes if c not in done_codes]
    print(f"[BACKFILL] Total target={len(codes):,}, already cached={len(done_codes):,}, pending={len(pending):,}")
    if not pending:
        return

    end_date = datetime.now().strftime("%Y%m%d")
    processed = 0
    ok_cnt = 0
    empty_cnt = 0
    err_cnt = 0
    t0 = time.monotonic()

    def task(full_code: str):
        try:
            kis_code, mrkt_div, bars = fetch_kis_symbol_100d(kis, full_code, end_date, etn_mrkt_div=etn_mrkt_div)
            status = "ok" if bars else "empty"
            return full_code, kis_code, mrkt_div, status, bars, ""
        except Exception as e:
            kis_code = full_code[1:] if len(full_code) == 7 else full_code
            mrkt_div = etn_mrkt_div if full_code.startswith("Q") else "J"
            return full_code, kis_code, mrkt_div, "error", [], str(e)[:300]

    with ThreadPoolExecutor(max_workers=max(1, min(6, workers))) as ex:
        fut_map = {ex.submit(task, c): c for c in pending}
        for fut in as_completed(fut_map):
            full_code, kis_code, mrkt_div, status, bars, err_msg = fut.result()
            now_str = datetime.now().isoformat()
            # Main-thread-only SQLite writes & commits (zero lock contention)
            if bars:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO backfill_bars
                    (code, trade_date, open, high, low, close, change_value, volume, trading_value, mod_yn, prtt_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            b["code"], b["trade_date"], b["open"], b["high"], b["low"],
                            b["close"], b["change_value"], b["volume"], b["trading_value"],
                            b["mod_yn"], b["prtt_rate"],
                        )
                        for b in bars
                    ],
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO backfill_status
                (code, kis_code, mrkt_div_code, status, row_count, error_msg, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (full_code, kis_code, mrkt_div, status, len(bars), err_msg, now_str),
            )
            processed += 1
            if status == "ok":
                ok_cnt += 1
            elif status == "empty":
                empty_cnt += 1
            else:
                err_cnt += 1

            if processed % 20 == 0 or processed == len(pending):
                conn.commit()
            if processed % 200 == 0 or processed == len(pending):
                elapsed = time.monotonic() - t0
                pct = (processed / len(pending)) * 100.0
                rate = processed / max(elapsed, 0.01)
                eta = (len(pending) - processed) / max(rate, 0.01)
                print(
                    f"  [{processed:,}/{len(pending):,} | {pct:.1f}%] ok={ok_cnt:,} empty={empty_cnt:,} err={err_cnt:,} "
                    f"| elapsed={elapsed/60:.1f}m | ETA={eta/60:.1f}m ({rate:.2f} sym/s)",
                    flush=True,
                )


def compute_adjustments_and_audit(conn: sqlite3.Connection, parquet_path: str, target_codes: list) -> tuple:
    """
    Compare overlap window (20260619~20260825) between historical parquet and KIS adjusted bars.
    Returns (adj_map: dict[code, float], audit_report: dict).
    """
    con = duckdb.connect()
    try:
        hist_rows = con.execute(
            f"""
            SELECT code, trade_date, close
            FROM read_parquet('{parquet_path}')
            WHERE trade_date >= '{OVERLAP_START}' AND trade_date <= '{OVERLAP_END}'
            """
        ).fetchall()
    finally:
        con.close()

    hist_map = {}
    for code, dt, cl in hist_rows:
        if cl and cl > 0:
            hist_map.setdefault(code, {})[dt] = float(cl)

    kis_rows = conn.execute(
        """
        SELECT code, trade_date, close
        FROM backfill_bars
        WHERE trade_date >= ? AND trade_date <= ?
        """,
        (OVERLAP_START, OVERLAP_END),
    ).fetchall()

    kis_map = {}
    for code, dt, cl in kis_rows:
        if cl and cl > 0:
            kis_map.setdefault(code, {})[dt] = float(cl)

    status_rows = conn.execute(
        "SELECT code, status, row_count, error_msg FROM backfill_status"
    ).fetchall()
    status_by_code = {r[0]: {"status": r[1], "row_count": r[2], "error_msg": r[3]} for r in status_rows}

    adj_map = {}
    adjusted_details = []
    inconsistent_details = []
    insufficient_overlap = []
    failed_symbols = []
    empty_symbols = []

    for code in target_codes:
        st = status_by_code.get(code)
        if not st or st["status"] == "error":
            failed_symbols.append({"code": code, "error": st["error_msg"] if st else "not_fetched"})
            continue
        if st["status"] == "empty" or st["row_count"] == 0:
            empty_symbols.append(code)
            continue

        h_dates = hist_map.get(code, {})
        k_dates = kis_map.get(code, {})
        common_dts = sorted(set(h_dates.keys()) & set(k_dates.keys()))
        if len(common_dts) < 5:
            insufficient_overlap.append({"code": code, "overlap_days": len(common_dts)})
            continue

        ratios = [
            k_dates[dt] / h_dates[dt]
            for dt in common_dts
            if h_dates.get(dt, 0) > 0 and k_dates.get(dt, 0) > 0
        ]
        if len(ratios) < 5:
            insufficient_overlap.append({"code": code, "overlap_days": len(ratios)})
            continue

        med_ratio = statistics.median(ratios)

        # Seam window right before 20260826 boundary (20260818~20260825, recent 5~6 trading days)
        seam_dts = [dt for dt in common_dts if dt >= "20260818"]
        seam_ratios = [
            k_dates[dt] / h_dates[dt]
            for dt in seam_dts
            if h_dates.get(dt, 0) > 0 and k_dates.get(dt, 0) > 0
        ]
        seam_ratio = statistics.median(seam_ratios) if len(seam_ratios) >= 3 else med_ratio

        # Prefer seam_ratio if boundary-adjacent split/bonus issue occurred
        use_seam = len(seam_ratios) >= 3 and abs(seam_ratio - 1.0) > 0.002 and abs(seam_ratio - med_ratio) > 0.02
        target_ratio = seam_ratio if use_seam else med_ratio
        target_series = seam_ratios if use_seam else ratios

        if abs(target_ratio - 1.0) > 0.002:
            within_band = sum(1 for r in target_series if abs(r / target_ratio - 1.0) <= 0.015)
            consistency = within_band / len(target_series)
            if consistency >= 0.80:
                adj_map[code] = round(target_ratio, 8)
                adjusted_details.append({
                    "code": code,
                    "adj_ratio": round(target_ratio, 8),
                    "med_ratio": round(med_ratio, 8),
                    "seam_ratio": round(seam_ratio, 8),
                    "source_window": "seam_20260818_20260825" if use_seam else "overlap_20260619_20260825",
                    "overlap_days": len(ratios),
                    "seam_days": len(seam_ratios),
                    "consistency": round(consistency, 3),
                })
            else:
                inconsistent_details.append({
                    "code": code,
                    "med_ratio": round(med_ratio, 8),
                    "seam_ratio": round(seam_ratio, 8),
                    "overlap_days": len(ratios),
                    "seam_days": len(seam_ratios),
                    "consistency": round(consistency, 3),
                })

    audit_report = {
        "generated_at": datetime.now().isoformat(),
        "base_parquet": parquet_path,
        "overlap_window": f"{OVERLAP_START}~{OVERLAP_END}",
        "backfill_from": BACKFILL_START,
        "summary": {
            "target_codes": len(target_codes),
            "adjusted_count": len(adjusted_details),
            "inconsistent_count": len(inconsistent_details),
            "insufficient_overlap_count": len(insufficient_overlap),
            "failed_count": len(failed_symbols),
            "empty_count": len(empty_symbols),
        },
        "adjusted_symbols": adjusted_details,
        "inconsistent_symbols": inconsistent_details,
        "insufficient_overlap_symbols": insufficient_overlap,
        "failed_symbols": failed_symbols,
        "empty_symbols": empty_symbols,
    }
    return adj_map, audit_report


def build_unified_parquet(
    conn: sqlite3.Connection,
    parquet_path: str,
    output_dir: str,
    target_codes: list,
    adj_map: dict,
    audit_report: dict,
):
    parent_dir = os.path.dirname(output_dir)
    os.makedirs(parent_dir, exist_ok=True)
    tmp_dir = f"{output_dir}._tmp_{os.getpid()}"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    con = duckdb.connect()
    try:
        df_target = pd.DataFrame({"code": target_codes})
        con.register("target_codes", df_target)

        if adj_map:
            df_adj = pd.DataFrame(list(adj_map.items()), columns=["code", "adj_ratio"])
        else:
            df_adj = pd.DataFrame({"code": pd.Series(dtype="str"), "adj_ratio": pd.Series(dtype="float64")})
        con.register("adj_factors", df_adj)

        df_kis = pd.read_sql_query(
            """
            SELECT code, trade_date, open, high, low, close, change_value, volume, trading_value
            FROM backfill_bars
            WHERE trade_date >= ?
            """,
            conn,
            params=(BACKFILL_START,),
        )
        con.register("kis_backfill", df_kis)

        con.execute(
            f"""
            CREATE TEMP TABLE unified_bars AS
            WITH hist_adj AS (
                SELECT
                    h.code,
                    h.trade_date,
                    h.open * COALESCE(a.adj_ratio, 1.0) AS open,
                    h.high * COALESCE(a.adj_ratio, 1.0) AS high,
                    h.low * COALESCE(a.adj_ratio, 1.0) AS low,
                    h.close * COALESCE(a.adj_ratio, 1.0) AS close,
                    h.volume / COALESCE(a.adj_ratio, 1.0) AS volume,
                    h.trading_value,
                    'CYBOS_15Y' AS data_source
                FROM read_parquet('{parquet_path}') h
                INNER JOIN target_codes t ON h.code = t.code
                LEFT JOIN adj_factors a ON h.code = a.code
                WHERE h.trade_date <= '{OVERLAP_END}'
            ),
            hist_with_chg AS (
                SELECT
                    code,
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    COALESCE(close - LAG(close) OVER (PARTITION BY code ORDER BY trade_date), 0.0) AS change_value,
                    volume,
                    trading_value,
                    data_source
                FROM hist_adj
            ),
            kis_part AS (
                SELECT
                    k.code,
                    k.trade_date,
                    k.open,
                    k.high,
                    k.low,
                    k.close,
                    k.change_value,
                    k.volume,
                    k.trading_value,
                    'KIS_REST' AS data_source
                FROM kis_backfill k
                INNER JOIN target_codes t ON k.code = t.code
            )
            SELECT * FROM hist_with_chg
            UNION ALL
            SELECT * FROM kis_part
            """
        )

        years = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT SUBSTR(trade_date, 1, 4) AS yr FROM unified_bars ORDER BY yr"
            ).fetchall()
        ]
        total_rows = 0
        for yr in years:
            yr_dir = os.path.join(tmp_dir, f"year={yr}")
            os.makedirs(yr_dir, exist_ok=True)
            out_file = os.path.join(yr_dir, "data.parquet")
            con.execute(
                f"""
                COPY (
                    SELECT code, trade_date, open, high, low, close, change_value, volume, trading_value, data_source
                    FROM unified_bars
                    WHERE SUBSTR(trade_date, 1, 4) = '{yr}'
                    ORDER BY code, trade_date
                ) TO '{out_file}' (FORMAT PARQUET, COMPRESSION 'SNAPPY')
                """
            )
            yr_cnt = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_file}')").fetchone()[0]
            total_rows += yr_cnt
            print(f"  [PARQUET] year={yr}: {yr_cnt:,} rows -> {out_file}")

        audit_report["summary"]["unified_total_rows"] = total_rows
        audit_report["summary"]["years"] = years
    finally:
        con.close()

    audit_tmp = os.path.join(tmp_dir, "backfill_audit_report.json")
    with open(audit_tmp, "w", encoding="utf-8") as f:
        json.dump(audit_report, f, ensure_ascii=False, indent=2)

    backup_dir = f"{output_dir}._old_{os.getpid()}"
    if os.path.exists(output_dir):
        os.rename(output_dir, backup_dir)
    os.rename(tmp_dir, output_dir)
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)

    print(f"[DONE] Unified Parquet written to {output_dir} (total_rows={audit_report['summary']['unified_total_rows']:,})")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill 20260826+ daily bars via KIS REST API and build unified 15Y+ Hive Parquet"
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of target symbols (0 = all)")
    parser.add_argument("--include-etn", action="store_true", help="Include Q-prefixed ETN symbols")
    parser.add_argument("--rebuild-only", action="store_true", help="Skip API fetch and rebuild Parquet from cache")
    parser.add_argument("--workers", type=int, default=1, choices=[1, 2, 3, 4, 5, 6], help="Concurrent worker threads (1..6)")
    parser.add_argument("--etn-mrkt-div", type=str, default="J", choices=["J", "Q"], help="FID_COND_MRKT_DIV_CODE for Q-prefixed ETNs (default: J)")
    args = parser.parse_args()

    codes = load_universe_from_parquet(BASE_PARQUET_PATH, include_etn=args.include_etn)
    if args.limit and args.limit > 0:
        codes = codes[:args.limit]

    conn = init_backfill_db(BACKFILL_DB_PATH)
    try:
        if not args.rebuild_only:
            kis = KISClient(min_interval=0.14)
            run_backfill_fetch(kis, conn, codes, workers=args.workers, etn_mrkt_div=args.etn_mrkt_div)
        else:
            print("[REBUILD-ONLY] Skipping KIS API fetch; using cached backfill_bars.")

        adj_map, audit_report = compute_adjustments_and_audit(conn, BASE_PARQUET_PATH, codes)
        build_unified_parquet(conn, BASE_PARQUET_PATH, OUTPUT_PARQUET_DIR, codes, adj_map, audit_report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
