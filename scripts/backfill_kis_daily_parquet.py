#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIS REST API Incremental Daily Bar Backfill & Unified 15Y+ Parquet Builder
- Base dataset: /tmp/krx_daily_15y.parquet (full backfill mode; never touches local_market.db)
- Incremental mode (--incremental): discovers active KRX universe via krx_master + recent Parquet,
  fetches only recent bars (or paginated from max(listing_date, 20260615) for new IPOs),
  auto-detects post-20260825 splits/adjustments, and atomically updates touched year>=2026 partitions.
- Resumable SQLite state/raw cache: /home/freshjinyong/techcapitallab/.cache/kis_backfill_daily.db
- Output: /home/freshjinyong/quant_data/market_data_daily_parquet/year=YYYY/data.parquet (10 cols, SNAPPY, atomic swap)
"""

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import duckdb
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from kis_client import KISClient, _to_float  # noqa: E402
import krx_master  # noqa: E402

BASE_PARQUET_PATH = "/tmp/krx_daily_15y.parquet"
BACKFILL_DB_PATH = os.path.join(BASE_DIR, ".cache", "kis_backfill_daily.db")
QUANT_DATA_DIR = os.environ.get("QUANT_DATA_DIR", "/home/freshjinyong/quant_data")
OUTPUT_PARQUET_DIR = os.path.join(QUANT_DATA_DIR, "market_data_daily_parquet")

OVERLAP_START = "20260619"
OVERLAP_END = "20260825"
BACKFILL_START = "20260826"
KIS_QUERY_START = "20260615"
SPLIT_DIFF_THRESHOLD = 0.002


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


def normalize_to_full_code(code: str, master: dict | None = None) -> str:
    c = code.strip().upper()
    if len(c) == 7 and c[0] in ("A", "Q"):
        return c
    if master and c in master and master[c].get("group_code") == "EN":
        return f"Q{c}"
    return f"A{c}"


def _parse_kis_daily_rows(raw_rows: list, full_code: str, start_date: str, end_date: str) -> list:
    bars = []
    for row in raw_rows:
        dt = str(row.get("stck_bsop_date", "")).strip()
        c = _to_float(row.get("stck_clpr"))
        if not dt or c <= 0 or dt < start_date or dt > end_date:
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
    return bars


def fetch_kis_bars_paginated(
    kis: KISClient,
    kis_code: str,
    start_date: str,
    end_date: str,
    max_pages: int = 10,
    mrkt_div: str = "J",
    full_code: str | None = None,
) -> list:
    """
    Paginated backward fetch from KIS FHKST03010100 (returns max 100 rows newest-first per call).
    Walks backward from end_date to start_date up to max_pages, deduplicating by trade_date.
    Returns bars sorted ascending by trade_date.
    """
    if full_code is None:
        if len(kis_code) == 7 and kis_code[0] in ("A", "Q"):
            full_code = kis_code
            raw_kis_code = kis_code[1:]
        else:
            raw_kis_code = kis_code
            full_code = f"{'Q' if mrkt_div == 'Q' else 'A'}{kis_code}"
    else:
        raw_kis_code = kis_code[1:] if len(kis_code) == 7 and kis_code[0] in ("A", "Q") else kis_code

    if start_date > end_date:
        return []

    collected = {}
    cur_end = end_date
    for _ in range(max_pages):
        data = kis._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": mrkt_div,
                "FID_INPUT_ISCD": raw_kis_code,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": cur_end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        raw_rows = data.get("output2") or []
        if not raw_rows:
            break

        page_bars = _parse_kis_daily_rows(raw_rows, full_code, start_date, cur_end)
        for b in page_bars:
            collected[b["trade_date"]] = b

        if len(raw_rows) < 100 or not page_bars:
            break

        oldest_dt = min(b["trade_date"] for b in page_bars)
        if oldest_dt <= start_date:
            break
        try:
            prev_dt = (datetime.strptime(oldest_dt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        except ValueError:
            break
        if prev_dt < start_date or prev_dt >= cur_end:
            break
        cur_end = prev_dt

    return sorted(collected.values(), key=lambda b: b["trade_date"])


def fetch_kis_symbol_paginated(
    kis: KISClient,
    full_code: str,
    end_date: str,
    start_date: str = KIS_QUERY_START,
    etn_mrkt_div: str = "J",
    max_pages: int = 10,
) -> tuple:
    """Fetch paginated daily bars for full_code (A/Q-prefixed) and return (kis_code, mrkt_div, bars)."""
    is_etn = full_code.startswith("Q")
    kis_code = full_code[1:] if len(full_code) == 7 and full_code[0] in ("A", "Q") else full_code
    mrkt_div = etn_mrkt_div if is_etn else "J"
    bars = fetch_kis_bars_paginated(
        kis,
        kis_code,
        start_date=start_date,
        end_date=end_date,
        max_pages=max_pages,
        mrkt_div=mrkt_div,
        full_code=full_code,
    )
    return kis_code, mrkt_div, bars


def _upsert_bars_and_status(
    conn: sqlite3.Connection,
    full_code: str,
    kis_code: str,
    mrkt_div: str,
    status: str,
    bars: list,
    err_msg: str,
    replace_all_bars: bool = False,
) -> None:
    now_str = datetime.now().isoformat()
    if replace_all_bars:
        conn.execute("DELETE FROM backfill_bars WHERE code = ?", (full_code,))
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
    total_rows = conn.execute(
        "SELECT COUNT(*) FROM backfill_bars WHERE code = ?", (full_code,)
    ).fetchone()[0]
    conn.execute(
        """
        INSERT OR REPLACE INTO backfill_status
        (code, kis_code, mrkt_div_code, status, row_count, error_msg, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (full_code, kis_code, mrkt_div, status, total_rows, err_msg, now_str),
    )


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
            kis_code, mrkt_div, bars = fetch_kis_symbol_paginated(
                kis, full_code, end_date, start_date=KIS_QUERY_START, etn_mrkt_div=etn_mrkt_div
            )
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
            _upsert_bars_and_status(conn, full_code, kis_code, mrkt_div, status, bars, err_msg)
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


def _compute_adj_ratio_for_series(h_dates: dict, k_dates: dict) -> tuple[float | None, dict]:
    common_dts = sorted(set(h_dates.keys()) & set(k_dates.keys()))
    if len(common_dts) < 5:
        return None, {"reason": "insufficient_overlap", "overlap_days": len(common_dts)}

    ratios = [
        k_dates[dt] / h_dates[dt]
        for dt in common_dts
        if h_dates.get(dt, 0) > 0 and k_dates.get(dt, 0) > 0
    ]
    if len(ratios) < 5:
        return None, {"reason": "insufficient_overlap", "overlap_days": len(ratios)}

    med_ratio = statistics.median(ratios)
    seam_dts = [dt for dt in common_dts if dt >= "20260818"]
    seam_ratios = [
        k_dates[dt] / h_dates[dt]
        for dt in seam_dts
        if h_dates.get(dt, 0) > 0 and k_dates.get(dt, 0) > 0
    ]
    seam_ratio = statistics.median(seam_ratios) if len(seam_ratios) >= 3 else med_ratio

    use_seam = len(seam_ratios) >= 3 and abs(seam_ratio - 1.0) > SPLIT_DIFF_THRESHOLD and abs(seam_ratio - med_ratio) > 0.02
    target_ratio = seam_ratio if use_seam else med_ratio
    target_series = seam_ratios if use_seam else ratios

    if abs(target_ratio - 1.0) <= SPLIT_DIFF_THRESHOLD:
        return 1.0, {"reason": "no_adjustment"}

    within_band = sum(1 for r in target_series if abs(r / target_ratio - 1.0) <= 0.015)
    consistency = within_band / len(target_series)
    meta = {
        "adj_ratio": round(target_ratio, 8),
        "med_ratio": round(med_ratio, 8),
        "seam_ratio": round(seam_ratio, 8),
        "source_window": "seam_20260818_20260825" if use_seam else "overlap_20260619_20260825",
        "overlap_days": len(ratios),
        "seam_days": len(seam_ratios),
        "consistency": round(consistency, 3),
    }
    if consistency >= 0.80:
        return round(target_ratio, 8), meta
    meta["reason"] = "inconsistent"
    return None, meta


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

        ratio, meta = _compute_adj_ratio_for_series(hist_map.get(code, {}), kis_map.get(code, {}))
        if ratio is None:
            reason = meta.get("reason")
            if reason == "insufficient_overlap":
                insufficient_overlap.append({"code": code, "overlap_days": meta["overlap_days"]})
            elif reason == "inconsistent":
                inconsistent_details.append({"code": code, **meta})
        elif ratio != 1.0:
            adj_map[code] = ratio
            adjusted_details.append({"code": code, **meta})

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


def _list_parquet_years(output_dir: str) -> list[str]:
    if not os.path.exists(output_dir):
        return []
    years = []
    for entry in sorted(os.listdir(output_dir)):
        if entry.startswith("year="):
            yr = entry.split("=", 1)[1]
            if os.path.exists(os.path.join(output_dir, entry, "data.parquet")):
                years.append(yr)
    return years


def get_parquet_max_trade_date(output_dir: str) -> str | None:
    years = _list_parquet_years(output_dir)
    if not years:
        return None
    con = duckdb.connect()
    try:
        for yr in reversed(years):
            pq_file = os.path.join(output_dir, f"year={yr}", "data.parquet")
            res = con.execute(f"SELECT MAX(trade_date) FROM read_parquet('{pq_file}')").fetchone()
            if res and res[0]:
                return str(res[0])
    finally:
        con.close()
    return None


def get_recent_parquet_codes(output_dir: str, include_etn: bool = False) -> list[str]:
    years = [yr for yr in _list_parquet_years(output_dir) if yr >= "2026"]
    if not years:
        return []
    files = [os.path.join(output_dir, f"year={yr}", "data.parquet") for yr in years]
    where_clause = "WHERE code LIKE 'A%' OR code LIKE 'Q%'" if include_etn else "WHERE code LIKE 'A%'"
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT code FROM read_parquet({files}) {where_clause} ORDER BY code"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def discover_incremental_universe(
    output_dir: str,
    include_etn: bool = False,
    master: dict | None = None,
) -> list[str]:
    if master is None:
        master = krx_master.load_krx_master()
    active_6 = krx_master.get_active_universe_codes(include_etf=True, include_etn=include_etn)
    master_full = {normalize_to_full_code(c, master) for c in active_6}
    recent_pq = set(get_recent_parquet_codes(output_dir, include_etn=include_etn))
    return sorted(master_full | recent_pq)


def get_latest_krx_trading_date(kis: KISClient) -> str:
    idx_bars = kis.get_index_daily_bars("0001", min_bars=5)
    if not idx_bars:
        raise RuntimeError("Failed to fetch KOSPI index daily bars (0001) to determine latest trading date.")
    return max(str(b["date"]).strip() for b in idx_bars if b.get("date"))


def run_incremental_fetch(
    kis: KISClient,
    conn: sqlite3.Connection,
    codes: list[str],
    target_date: str,
    master: dict,
    workers: int = 1,
    etn_mrkt_div: str = "J",
) -> tuple[set[str], dict]:
    """
    Incremental fetch for Stage 2:
    - Existing symbols in backfill_bars: fetch [last_date - 10d .. target_date] (1 call),
      detect split/adjustment (>0.2% overlap close diff or mod_yn != 'N') and auto re-fetch to 20260615.
    - New IPOs / missing symbols: paginated fetch from max(listing_date, 20260615) to target_date.
    """
    existing_map: dict[str, dict[str, float]] = {}
    existing_max_dt: dict[str, str] = {}
    if codes:
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            f"SELECT code, trade_date, close FROM backfill_bars WHERE code IN ({placeholders})",
            codes,
        ).fetchall()
        for code, dt, cl in rows:
            existing_map.setdefault(code, {})[dt] = float(cl)
            if dt > existing_max_dt.get(code, ""):
                existing_max_dt[code] = dt

    print(
        f"[INCREMENTAL-FETCH] target_codes={len(codes):,}, target_date={target_date}, "
        f"in_cache={len(existing_max_dt):,}, new_or_missing={len(codes) - len(existing_max_dt):,}"
    )

    split_codes: set[str] = set()
    stats = {
        "total_target": len(codes),
        "incremental_1call": 0,
        "new_or_missing_paginated": 0,
        "split_refetched": 0,
        "skipped_prelisting": 0,
        "ok": 0,
        "empty": 0,
        "error": 0,
    }
    t0 = time.monotonic()
    processed = 0

    def task(full_code: str):
        is_etn = full_code.startswith("Q")
        kis_code = full_code[1:] if len(full_code) == 7 and full_code[0] in ("A", "Q") else full_code
        mrkt_div = etn_mrkt_div if is_etn else "J"

        listing_dt = str(master.get(kis_code, {}).get("listing_date", "") or "").strip()
        if len(listing_dt) == 8 and listing_dt.isdigit():
            ipo_start = max(listing_dt, KIS_QUERY_START)
        else:
            ipo_start = KIS_QUERY_START

        last_dt = existing_max_dt.get(full_code)
        prior_closes = existing_map.get(full_code, {})

        try:
            if last_dt and last_dt >= KIS_QUERY_START:
                try:
                    window_start = (datetime.strptime(last_dt, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
                except ValueError:
                    window_start = KIS_QUERY_START
                window_start = max(window_start, ipo_start)
                if window_start > target_date:
                    window_start = max(ipo_start, KIS_QUERY_START)

                recent_bars = fetch_kis_bars_paginated(
                    kis,
                    kis_code,
                    start_date=window_start,
                    end_date=target_date,
                    max_pages=2,
                    mrkt_div=mrkt_div,
                    full_code=full_code,
                )

                split_reason = None
                for b in recent_bars:
                    if str(b.get("mod_yn", "N")).strip().upper() != "N":
                        split_reason = f"mod_yn={b.get('mod_yn')} on {b['trade_date']}"
                        break
                    old_close = prior_closes.get(b["trade_date"])
                    if old_close and old_close > 0:
                        rel_diff = abs(b["close"] - old_close) / old_close
                        if rel_diff > SPLIT_DIFF_THRESHOLD:
                            split_reason = (
                                f"overlap_diff={rel_diff:.4f} on {b['trade_date']} "
                                f"(old={old_close}, new={b['close']})"
                            )
                            break

                if split_reason:
                    full_bars = fetch_kis_bars_paginated(
                        kis,
                        kis_code,
                        start_date=ipo_start,
                        end_date=target_date,
                        max_pages=10,
                        mrkt_div=mrkt_div,
                        full_code=full_code,
                    )
                    status = "ok" if full_bars else "empty"
                    return full_code, kis_code, mrkt_div, status, full_bars, "", "split", split_reason

                status = "ok" if (recent_bars or prior_closes) else "empty"
                return full_code, kis_code, mrkt_div, status, recent_bars, "", "inc", ""
            else:
                if ipo_start > target_date:
                    return full_code, kis_code, mrkt_div, "empty", [], "", "prelisting", ""
                bars = fetch_kis_bars_paginated(
                    kis,
                    kis_code,
                    start_date=ipo_start,
                    end_date=target_date,
                    max_pages=10,
                    mrkt_div=mrkt_div,
                    full_code=full_code,
                )
                status = "ok" if bars else "empty"
                return full_code, kis_code, mrkt_div, status, bars, "", "new", ""
        except Exception as e:
            return full_code, kis_code, mrkt_div, "error", [], str(e)[:300], "err", ""

    with ThreadPoolExecutor(max_workers=max(1, min(6, workers))) as ex:
        fut_map = {ex.submit(task, c): c for c in codes}
        for fut in as_completed(fut_map):
            full_code, kis_code, mrkt_div, status, bars, err_msg, mode, split_reason = fut.result()
            replace_all = mode == "split"
            if mode == "split":
                split_codes.add(full_code)
                stats["split_refetched"] += 1
                print(f"  [SPLIT-DETECTED] {full_code}: {split_reason} -> re-fetched {len(bars)} bars", flush=True)
            elif mode == "inc":
                stats["incremental_1call"] += 1
            elif mode == "new":
                stats["new_or_missing_paginated"] += 1
            elif mode == "prelisting":
                stats["skipped_prelisting"] += 1

            if mode != "prelisting":
                _upsert_bars_and_status(
                    conn, full_code, kis_code, mrkt_div, status, bars, err_msg, replace_all_bars=replace_all
                )

            if status == "ok":
                stats["ok"] += 1
            elif status == "empty":
                stats["empty"] += 1
            else:
                stats["error"] += 1

            processed += 1
            if processed % 20 == 0 or processed == len(codes):
                conn.commit()
            if processed % 200 == 0 or processed == len(codes):
                elapsed = time.monotonic() - t0
                pct = (processed / len(codes)) * 100.0
                rate = processed / max(elapsed, 0.01)
                eta = (len(codes) - processed) / max(rate, 0.01)
                print(
                    f"  [{processed:,}/{len(codes):,} | {pct:.1f}%] ok={stats['ok']:,} empty={stats['empty']:,} "
                    f"err={stats['error']:,} split={stats['split_refetched']:,} "
                    f"| elapsed={elapsed/60:.1f}m | ETA={eta/60:.1f}m ({rate:.2f} sym/s)",
                    flush=True,
                )

    return split_codes, stats


def compute_split_adj_ratios_from_parquet(
    conn: sqlite3.Connection,
    output_dir: str,
    split_codes: set[str],
) -> dict[str, float]:
    if not split_codes:
        return {}
    pq_2026 = os.path.join(output_dir, "year=2026", "data.parquet")
    if not os.path.exists(pq_2026):
        return {}

    codes_list = sorted(split_codes)
    con = duckdb.connect()
    try:
        con.register("split_codes", pd.DataFrame({"code": codes_list}))
        hist_rows = con.execute(
            f"""
            SELECT p.code, p.trade_date, p.close
            FROM read_parquet('{pq_2026}') p
            INNER JOIN split_codes s ON p.code = s.code
            WHERE p.trade_date >= '{OVERLAP_START}' AND p.trade_date <= '{OVERLAP_END}'
            """
        ).fetchall()
    finally:
        con.close()

    hist_map: dict[str, dict[str, float]] = {}
    for code, dt, cl in hist_rows:
        if cl and cl > 0:
            hist_map.setdefault(code, {})[dt] = float(cl)

    placeholders = ",".join("?" for _ in codes_list)
    kis_rows = conn.execute(
        f"""
        SELECT code, trade_date, close
        FROM backfill_bars
        WHERE code IN ({placeholders}) AND trade_date >= ? AND trade_date <= ?
        """,
        (*codes_list, OVERLAP_START, OVERLAP_END),
    ).fetchall()

    kis_map: dict[str, dict[str, float]] = {}
    for code, dt, cl in kis_rows:
        if cl and cl > 0:
            kis_map.setdefault(code, {})[dt] = float(cl)

    adj_map: dict[str, float] = {}
    for code in codes_list:
        ratio, meta = _compute_adj_ratio_for_series(hist_map.get(code, {}), kis_map.get(code, {}))
        if ratio and ratio != 1.0:
            adj_map[code] = ratio
            print(f"  [RESCALE] {code}: historical <=20260825 adj_ratio={ratio} ({meta.get('source_window')})", flush=True)
    return adj_map


def _atomic_write_parquet(con: duckdb.DuckDBPyConnection, select_sql: str, out_file: str) -> int:
    yr_dir = os.path.dirname(out_file)
    os.makedirs(yr_dir, exist_ok=True)
    fd, tmp_file = tempfile.mkstemp(prefix=".data.", suffix=".parquet.tmp", dir=yr_dir)
    os.close(fd)
    try:
        con.execute(f"COPY ({select_sql}) TO '{tmp_file}' (FORMAT PARQUET, COMPRESSION 'SNAPPY')")
        cnt = con.execute(f"SELECT COUNT(*) FROM read_parquet('{tmp_file}')").fetchone()[0]
        if cnt <= 0:
            raise RuntimeError(f"Refusing to overwrite {out_file} with 0 rows")
        os.replace(tmp_file, out_file)
        return int(cnt)
    except Exception:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        raise


def update_parquet_incremental(
    conn: sqlite3.Connection,
    output_dir: str,
    target_codes: list[str],
    split_adj_map: dict[str, float],
    audit_info: dict,
) -> None:
    """
    Incrementally update market_data_daily_parquet:
    - Preserves <=20260825 CYBOS_15Y rows (only rescaling & rewriting affected <2026 years if split_adj_map non-empty).
    - Rewrites ONLY touched year>=2026 partition(s) atomically, computing change_value across year boundaries via LAG(close).
    - Preserves existing KIS_REST rows for non-target codes when --codes / --max-symbols is used.
    """
    os.makedirs(output_dir, exist_ok=True)
    existing_years = _list_parquet_years(output_dir)
    years_ge_2026 = sorted({yr for yr in existing_years if yr >= "2026"})

    placeholders = ",".join("?" for _ in target_codes) if target_codes else "''"
    if target_codes:
        df_kis_target = pd.read_sql_query(
            f"""
            SELECT code, trade_date, open, high, low, close, change_value, volume, trading_value
            FROM backfill_bars
            WHERE trade_date >= ? AND code IN ({placeholders})
            """,
            conn,
            params=(BACKFILL_START, *target_codes),
        )
    else:
        df_kis_target = pd.DataFrame(
            columns=["code", "trade_date", "open", "high", "low", "close", "change_value", "volume", "trading_value"]
        )

    kis_years = sorted({str(dt)[:4] for dt in df_kis_target["trade_date"] if str(dt)[:4] >= "2026"})
    touched_ge_2026 = sorted(set(years_ge_2026) | set(kis_years))
    if not touched_ge_2026:
        print("[INCREMENTAL-PARQUET] No year>=2026 data to write.")
        return

    con = duckdb.connect()
    try:
        con.register("target_codes", pd.DataFrame({"code": target_codes}))
        if split_adj_map:
            df_adj = pd.DataFrame(list(split_adj_map.items()), columns=["code", "adj_ratio"])
        else:
            df_adj = pd.DataFrame({"code": pd.Series(dtype="str"), "adj_ratio": pd.Series(dtype="float64")})
        con.register("split_adj", df_adj)
        con.register("kis_target_bars", df_kis_target)

        # 1. If any split occurred, rescale & atomically rewrite ONLY affected <2026 partitions
        rescaled_pre2026_years = []
        if split_adj_map:
            pre_2026_years = [yr for yr in existing_years if yr < "2026"]
            affected_pre_years = []
            for yr in pre_2026_years:
                pq_file = os.path.join(output_dir, f"year={yr}", "data.parquet")
                hit = con.execute(
                    f"""
                    SELECT COUNT(*) FROM read_parquet('{pq_file}') p
                    INNER JOIN split_adj s ON p.code = s.code
                    """
                ).fetchone()[0]
                if hit > 0:
                    affected_pre_years.append(yr)

            if affected_pre_years:
                affected_files = [os.path.join(output_dir, f"year={yr}", "data.parquet") for yr in affected_pre_years]
                con.execute(
                    f"""
                    CREATE TEMP TABLE rescaled_split_pre2026 AS
                    WITH scaled AS (
                        SELECT
                            p.code,
                            p.trade_date,
                            p.open * s.adj_ratio AS open,
                            p.high * s.adj_ratio AS high,
                            p.low * s.adj_ratio AS low,
                            p.close * s.adj_ratio AS close,
                            p.volume / s.adj_ratio AS volume,
                            p.trading_value,
                            p.data_source
                        FROM read_parquet({affected_files}) p
                        INNER JOIN split_adj s ON p.code = s.code
                    )
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
                    FROM scaled
                    """
                )
                for yr in affected_pre_years:
                    pq_file = os.path.join(output_dir, f"year={yr}", "data.parquet")
                    cnt = _atomic_write_parquet(
                        con,
                        f"""
                        SELECT code, trade_date, open, high, low, close, change_value, volume, trading_value, data_source
                        FROM (
                            SELECT p.*
                            FROM read_parquet('{pq_file}') p
                            LEFT JOIN split_adj s ON p.code = s.code
                            WHERE s.code IS NULL
                            UNION ALL
                            SELECT * FROM rescaled_split_pre2026 WHERE SUBSTR(trade_date, 1, 4) = '{yr}'
                        )
                        ORDER BY code, trade_date
                        """,
                        pq_file,
                    )
                    rescaled_pre2026_years.append(yr)
                    print(f"  [PARQUET-RESCALE] year={yr}: {cnt:,} rows -> {pq_file}")

        # 2. Build touched year>=2026 partitions using year=2025 last close as LAG(close) boundary anchor
        pq_2025 = os.path.join(output_dir, "year=2025", "data.parquet")
        if os.path.exists(pq_2025):
            con.execute(
                f"""
                CREATE TEMP TABLE prev_year_anchor AS
                SELECT
                    code,
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    change_value AS orig_change_value,
                    volume,
                    trading_value,
                    data_source,
                    TRUE AS is_anchor
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
                    FROM read_parquet('{pq_2025}')
                )
                WHERE rn = 1
                """
            )
        else:
            con.execute(
                """
                CREATE TEMP TABLE prev_year_anchor (
                    code VARCHAR, trade_date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
                    close DOUBLE, orig_change_value DOUBLE, volume DOUBLE, trading_value DOUBLE,
                    data_source VARCHAR, is_anchor BOOLEAN
                )
                """
            )

        existing_ge_2026_files = [
            os.path.join(output_dir, f"year={yr}", "data.parquet")
            for yr in years_ge_2026
            if os.path.exists(os.path.join(output_dir, f"year={yr}", "data.parquet"))
        ]

        if existing_ge_2026_files:
            con.execute(
                f"""
                CREATE TEMP TABLE existing_cybos_2026 AS
                SELECT
                    p.code,
                    p.trade_date,
                    p.open * COALESCE(s.adj_ratio, 1.0) AS open,
                    p.high * COALESCE(s.adj_ratio, 1.0) AS high,
                    p.low * COALESCE(s.adj_ratio, 1.0) AS low,
                    p.close * COALESCE(s.adj_ratio, 1.0) AS close,
                    p.change_value AS orig_change_value,
                    p.volume / COALESCE(s.adj_ratio, 1.0) AS volume,
                    p.trading_value,
                    p.data_source,
                    FALSE AS is_anchor
                FROM read_parquet({existing_ge_2026_files}) p
                LEFT JOIN split_adj s ON p.code = s.code
                WHERE p.data_source = 'CYBOS_15Y' AND p.trade_date <= '{OVERLAP_END}'
                """
            )
            con.execute(
                f"""
                CREATE TEMP TABLE existing_kis_preserved AS
                SELECT
                    p.code,
                    p.trade_date,
                    p.open,
                    p.high,
                    p.low,
                    p.close,
                    p.change_value AS orig_change_value,
                    p.volume,
                    p.trading_value,
                    p.data_source,
                    FALSE AS is_anchor
                FROM read_parquet({existing_ge_2026_files}) p
                LEFT JOIN target_codes t ON p.code = t.code
                WHERE p.data_source = 'KIS_REST' AND p.trade_date >= '{BACKFILL_START}' AND t.code IS NULL
                """
            )
        else:
            for tbl in ("existing_cybos_2026", "existing_kis_preserved"):
                con.execute(
                    f"""
                    CREATE TEMP TABLE {tbl} (
                        code VARCHAR, trade_date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
                        close DOUBLE, orig_change_value DOUBLE, volume DOUBLE, trading_value DOUBLE,
                        data_source VARCHAR, is_anchor BOOLEAN
                    )
                    """
                )

        con.execute(
            """
            CREATE TEMP TABLE unified_ge_2026 AS
            WITH combined AS (
                SELECT * FROM prev_year_anchor
                UNION ALL
                SELECT * FROM existing_cybos_2026
                UNION ALL
                SELECT * FROM existing_kis_preserved
                UNION ALL
                SELECT
                    k.code,
                    k.trade_date,
                    k.open,
                    k.high,
                    k.low,
                    k.close,
                    k.change_value AS orig_change_value,
                    k.volume,
                    k.trading_value,
                    'KIS_REST' AS data_source,
                    FALSE AS is_anchor
                FROM kis_target_bars k
            ),
            with_chg AS (
                SELECT
                    c.code,
                    c.trade_date,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    CASE
                        WHEN c.data_source = 'CYBOS_15Y' AND s.code IS NULL THEN c.orig_change_value
                        ELSE COALESCE(c.close - LAG(c.close) OVER (PARTITION BY c.code ORDER BY c.trade_date), 0.0)
                    END AS change_value,
                    c.volume,
                    c.trading_value,
                    c.data_source,
                    c.is_anchor
                FROM combined c
                LEFT JOIN split_adj s ON c.code = s.code
            )
            SELECT
                code, trade_date, open, high, low, close, change_value, volume, trading_value, data_source
            FROM with_chg
            WHERE NOT is_anchor
            """
        )

        written_counts = {}
        for yr in touched_ge_2026:
            out_file = os.path.join(output_dir, f"year={yr}", "data.parquet")
            cnt = _atomic_write_parquet(
                con,
                f"""
                SELECT code, trade_date, open, high, low, close, change_value, volume, trading_value, data_source
                FROM unified_ge_2026
                WHERE SUBSTR(trade_date, 1, 4) = '{yr}'
                ORDER BY code, trade_date
                """,
                out_file,
            )
            written_counts[yr] = cnt
            print(f"  [PARQUET-INC] year={yr}: {cnt:,} rows -> {out_file}")

        audit_info["written_years"] = written_counts
        audit_info["rescaled_pre2026_years"] = rescaled_pre2026_years
    finally:
        con.close()

    audit_path = os.path.join(output_dir, "backfill_audit_report.json")
    fd, tmp_audit = tempfile.mkstemp(prefix=".audit.", suffix=".json.tmp", dir=output_dir)
    os.close(fd)
    try:
        with open(tmp_audit, "w", encoding="utf-8") as f:
            json.dump(audit_info, f, ensure_ascii=False, indent=2)
        os.replace(tmp_audit, audit_path)
    except Exception:
        if os.path.exists(tmp_audit):
            try:
                os.remove(tmp_audit)
            except OSError:
                pass
        raise

    print(f"[DONE-INCREMENTAL] Updated {output_dir} (touched_years={touched_ge_2026})")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill 20260826+ daily bars via KIS REST API and build unified 15Y+ Hive Parquet"
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of target symbols (0 = all)")
    parser.add_argument("--max-symbols", type=int, default=0, help="Alias for --limit in incremental/backfill mode")
    parser.add_argument("--codes", type=str, default="", help="Comma-separated symbol codes for targeted testing (e.g. 005930,000660,0205H0)")
    parser.add_argument("--include-etn", action="store_true", help="Include Q-prefixed ETN symbols")
    parser.add_argument("--rebuild-only", action="store_true", help="Skip API fetch and rebuild Parquet from cache")
    parser.add_argument("--incremental", action="store_true", help="Stage 2 incremental daily update & IPO discovery")
    parser.add_argument("--force", action="store_true", help="Force fetch/update even if Parquet max trade_date >= latest KOSPI date")
    parser.add_argument("--workers", type=int, default=1, choices=[1, 2, 3, 4, 5, 6], help="Concurrent worker threads (1..6)")
    parser.add_argument("--etn-mrkt-div", type=str, default="J", choices=["J", "Q"], help="FID_COND_MRKT_DIV_CODE for Q-prefixed ETNs (default: J)")
    args = parser.parse_args()

    max_syms = args.max_symbols if args.max_symbols > 0 else args.limit
    conn = init_backfill_db(BACKFILL_DB_PATH)
    try:
        if args.incremental:
            kis = None if args.rebuild_only else KISClient(min_interval=0.14)
            pq_max_dt = get_parquet_max_trade_date(OUTPUT_PARQUET_DIR)
            if kis is not None:
                latest_kospi_dt = get_latest_krx_trading_date(kis)
            else:
                latest_kospi_dt = pq_max_dt or datetime.now().strftime("%Y%m%d")

            print(
                f"[INCREMENTAL] parquet_max_date={pq_max_dt}, latest_kospi_date={latest_kospi_dt}, "
                f"force={args.force}, rebuild_only={args.rebuild_only}"
            )
            if pq_max_dt and pq_max_dt >= latest_kospi_dt and not args.force and not args.rebuild_only:
                print(
                    f"[INCREMENTAL] Parquet already up-to-date ({pq_max_dt} >= {latest_kospi_dt}); "
                    f"skipping fetch & rebuild (pass --force to override)."
                )
                return

            master = krx_master.load_krx_master()
            if args.codes.strip():
                raw_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
                codes = sorted({normalize_to_full_code(c, master) for c in raw_codes})
            else:
                codes = discover_incremental_universe(
                    OUTPUT_PARQUET_DIR, include_etn=args.include_etn, master=master
                )
            if max_syms > 0:
                codes = codes[:max_syms]

            if not args.rebuild_only:
                split_codes, fetch_stats = run_incremental_fetch(
                    kis,
                    conn,
                    codes,
                    target_date=latest_kospi_dt,
                    master=master,
                    workers=args.workers,
                    etn_mrkt_div=args.etn_mrkt_div,
                )
            else:
                print("[REBUILD-ONLY] Skipping KIS API fetch; using cached backfill_bars.")
                split_codes = set()
                fetch_stats = {"rebuild_only": True, "total_target": len(codes)}

            split_adj_map = compute_split_adj_ratios_from_parquet(conn, OUTPUT_PARQUET_DIR, split_codes)
            audit_info = {
                "mode": "incremental",
                "generated_at": datetime.now().isoformat(),
                "latest_kospi_date": latest_kospi_dt,
                "previous_parquet_max_date": pq_max_dt,
                "fetch_stats": fetch_stats,
                "split_codes": sorted(split_codes),
                "split_adj_map": split_adj_map,
            }
            update_parquet_incremental(conn, OUTPUT_PARQUET_DIR, codes, split_adj_map, audit_info)
            return

        # Full backfill mode (Stage 1)
        if args.codes.strip():
            raw_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
            codes = sorted({normalize_to_full_code(c) for c in raw_codes})
        else:
            codes = load_universe_from_parquet(BASE_PARQUET_PATH, include_etn=args.include_etn)
        if max_syms > 0:
            codes = codes[:max_syms]

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
