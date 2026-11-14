#!/usr/bin/env python3
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pyarrow.compute as pc

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import krx_master  # noqa: E402

PARQUET_GLOB = "/home/freshjinyong/quant_data/market_data_daily_parquet/year=*/data.parquet"
R2_STOCK_LIST_URL = "https://pub-88fccd299b1d47fe97d43f35f238d0ce.r2.dev/data/stock_list.json"
OUT_DIR = Path("/home/freshjinyong/quant_data/r2_chart_export")
DAILY_OUT_DIR = OUT_DIR / "daily"
EXTRA_NAMES_CACHE = Path(krx_master.CACHE_DIR) / "extra_names.json"
SAMPLE_CODES = ("000660", "005930", "012450", "0000D0")
OPTIONAL_SAMPLE_CODES = ("0205H0",)

# Forked worker globals (populated in main before Pool creation on POSIX)
_COL_TIME = None
_COL_OPEN = None
_COL_HIGH = None
_COL_LOW = None
_COL_CLOSE = None
_COL_VOL = None
_COL_VAL = None
_DAILY_OUT_STR = str(DAILY_OUT_DIR)


def _atomic_write_text(target_path: Path, text: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target_path.parent),
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(text)
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def load_r2_or_cached_stock_list(url: str, cached_path: Path) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"Warning: failed to fetch live R2 stock_list.json ({exc}); checking local cache...", file=sys.stderr)
        if cached_path.exists():
            with open(cached_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []


def load_extra_names(cache_path: Path) -> dict[str, str]:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if k and v}
    except Exception as exc:
        print(f"Warning: failed to load {cache_path} ({exc})", file=sys.stderr)
    return {}


def fetch_naver_stock_name(code: str) -> tuple[str, str | None]:
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        name = str(data.get("stockName") or "").strip()
        if name and name != code:
            return code, name
    except Exception:
        pass
    return code, None


def resolve_missing_names_naver(codes: list[str], extra_names: dict[str, str]) -> int:
    if not codes:
        return 0
    resolved = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_naver_stock_name, c) for c in codes]
        for fut in as_completed(futures):
            code, name = fut.result()
            if name:
                extra_names[code] = name
                resolved += 1
    if resolved > 0:
        _atomic_write_text(
            EXTRA_NAMES_CACHE,
            json.dumps(dict(sorted(extra_names.items())), ensure_ascii=False, indent=2) + "\n",
        )
    return resolved


def is_clean_stock_entry(entry: dict) -> bool:
    code = str(entry.get("code", "")).strip()
    name = str(entry.get("name", "")).strip()
    last_date = str(entry.get("lastDate", "")).strip()
    if len(code) != 6:
        return False
    if name == code and last_date == "2026-08-25":
        return False
    return True


def _export_worker_batch(batch: list[tuple[str, int, int]]) -> list[tuple[str, int, int, str]]:
    res: list[tuple[str, int, int, str]] = []
    out_dir = _DAILY_OUT_STR
    for code6, start, end in batch:
        length = end - start
        ts = _COL_TIME.slice(start, length).to_pylist()
        os_ = _COL_OPEN.slice(start, length).to_pylist()
        hs = _COL_HIGH.slice(start, length).to_pylist()
        ls = _COL_LOW.slice(start, length).to_pylist()
        cs = _COL_CLOSE.slice(start, length).to_pylist()
        vs = _COL_VOL.slice(start, length).to_pylist()
        vals = _COL_VAL.slice(start, length).to_pylist()

        parts: list[str] = []
        prev_t: str | None = None
        last_close = 0
        last_date = ""
        for t, o, h, l, c, v, val in zip(ts, os_, hs, ls, cs, vs, vals):
            bar_json = (
                f'{{"time":"{t}","open":{o},"high":{h},"low":{l},"close":{c},"volume":{v},"value":{val}}}'
            )
            if t == prev_t:
                parts[-1] = bar_json
            else:
                parts.append(bar_json)
                prev_t = t
            last_close = c
            last_date = t

        payload = "[" + ",".join(parts) + "]"
        out_path = os.path.join(out_dir, f"{code6}.json")
        with open(out_path, "wb") as f:
            f.write(payload.encode("utf-8"))
        res.append((code6, len(parts), last_close, last_date))
    return res


def validate_samples(codes: tuple[str, ...], optional_codes: tuple[str, ...] = ()) -> None:
    expected_keys = {"time", "open", "high", "low", "close", "volume", "value"}
    print("\n=== Sample JSON Validation ===")
    check_list = list(codes)
    for opt in optional_codes:
        if (DAILY_OUT_DIR / f"{opt}.json").exists():
            check_list.append(opt)

    for code in check_list:
        p = DAILY_OUT_DIR / f"{code}.json"
        if not p.exists():
            raise RuntimeError(f"Missing sample output: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or len(data) == 0:
            raise RuntimeError(f"Sample {code}.json is empty or not a list")
        times = [row["time"] for row in data]
        if len(times) != len(set(times)):
            raise RuntimeError(f"Sample {code}.json has duplicate times")
        if times != sorted(times):
            raise RuntimeError(f"Sample {code}.json is not sorted ascending by time")
        for row in (data[0], data[-1]):
            if set(row.keys()) != expected_keys:
                raise RuntimeError(f"Sample {code}.json unexpected keys: {set(row.keys())}")
            for k in ("open", "high", "low", "close", "volume", "value"):
                if not isinstance(row[k], int):
                    raise RuntimeError(f"Sample {code}.json field {k} is not int: {type(row[k])}")
        print(f"  {code}.json: bars={len(data)}, first={data[0]}, last2={data[-2:]}")


def main() -> None:
    global _COL_TIME, _COL_OPEN, _COL_HIGH, _COL_LOW, _COL_CLOSE, _COL_VOL, _COL_VAL
    t0 = time.time()
    DAILY_OUT_DIR.mkdir(parents=True, exist_ok=True)
    stock_list_path = OUT_DIR / "stock_list.json"

    # 1. Load official KRX master names, extra cached names, and live/cached R2 stock_list.json
    krx_names = krx_master.get_stock_name_map()
    extra_names = load_extra_names(EXTRA_NAMES_CACHE)
    r2_stock_list = load_r2_or_cached_stock_list(R2_STOCK_LIST_URL, stock_list_path)
    r2_valid_names: dict[str, str] = {}
    for item in r2_stock_list:
        c = str(item.get("code", "")).strip()
        n = str(item.get("name", "")).strip()
        if len(c) == 6 and n and n != c:
            r2_valid_names[c] = n

    print(
        f"Loaded names: krx_master={len(krx_names)}, extra_cache={len(extra_names)}, "
        f"r2_valid={len(r2_valid_names)} (raw R2 entries={len(r2_stock_list)})"
    )

    # 2. Single full Parquet scan via DuckDB -> Arrow Table (sorted by code6, time)
    t_scan0 = time.time()
    con = duckdb.connect()
    tbl = con.execute(f"""
        SELECT
            CASE WHEN starts_with(code, 'A') AND length(code) = 7 THEN substr(code, 2) ELSE code END AS code6,
            CASE WHEN length(trim(CAST(trade_date AS VARCHAR))) = 8 AND NOT contains(CAST(trade_date AS VARCHAR), '-')
                 THEN substr(trim(CAST(trade_date AS VARCHAR)), 1, 4) || '-' || substr(trim(CAST(trade_date AS VARCHAR)), 5, 2) || '-' || substr(trim(CAST(trade_date AS VARCHAR)), 7, 2)
                 ELSE trim(CAST(trade_date AS VARCHAR)) END AS time,
            CAST(ROUND(COALESCE(open, 0)) AS BIGINT) AS open,
            CAST(ROUND(COALESCE(high, 0)) AS BIGINT) AS high,
            CAST(ROUND(COALESCE(low, 0)) AS BIGINT) AS low,
            CAST(ROUND(COALESCE(close, 0)) AS BIGINT) AS close,
            CAST(ROUND(COALESCE(volume, 0)) AS BIGINT) AS volume,
            CAST(ROUND(COALESCE(trading_value, 0)) AS BIGINT) AS value
        FROM read_parquet('{PARQUET_GLOB}', hive_partitioning=true)
        ORDER BY code6 ASC, time ASC
    """).to_arrow_table()
    con.close()
    t_scan1 = time.time()
    print(f"Parquet -> Arrow: {tbl.num_rows} rows in {t_scan1 - t_scan0:.2f}s")

    # 3. Group boundaries in C++ via run_end_encode
    ree = pc.run_end_encode(tbl["code6"].combine_chunks())
    run_ends = ree.run_ends.to_pylist()
    values = ree.values.to_pylist()
    groups: list[tuple[str, int, int]] = []
    start = 0
    for code6, end in zip(values, run_ends):
        groups.append((code6, start, end))
        start = end

    _COL_TIME = tbl["time"].combine_chunks()
    _COL_OPEN = tbl["open"].combine_chunks()
    _COL_HIGH = tbl["high"].combine_chunks()
    _COL_LOW = tbl["low"].combine_chunks()
    _COL_CLOSE = tbl["close"].combine_chunks()
    _COL_VOL = tbl["volume"].combine_chunks()
    _COL_VAL = tbl["value"].combine_chunks()
    del tbl, ree, run_ends, values

    # 4. Parallel serialize & write daily/{code}.json
    t_write0 = time.time()
    batch_size = 64
    batches = [groups[i : i + batch_size] for i in range(0, len(groups), batch_size)]
    n_workers = min(4, os.cpu_count() or 4)
    if os.name == "posix":
        ctx = mp.get_context("fork")
        with ctx.Pool(processes=n_workers) as pool:
            batch_results = pool.map(_export_worker_batch, batches)
        exported_stats = [item for sub in batch_results for item in sub]
    else:
        exported_stats = []
        for b in batches:
            exported_stats.extend(_export_worker_batch(b))
    t_write1 = time.time()
    print(f"Exported {len(exported_stats)} daily JSON files in {t_write1 - t_write0:.2f}s")

    # Release large Arrow column references
    _COL_TIME = _COL_OPEN = _COL_HIGH = _COL_LOW = _COL_CLOSE = _COL_VOL = _COL_VAL = None

    # 5. Resolve any remaining active 6-char codes via Naver basic API (cached to extra_names.json)
    def lookup_name(code6: str) -> str | None:
        return krx_names.get(code6) or extra_names.get(code6) or r2_valid_names.get(code6)

    unresolved_active = [
        code6
        for code6, _bars, _last_close, last_date in exported_stats
        if len(code6) == 6 and lookup_name(code6) is None and last_date != "2026-08-25"
    ]
    if unresolved_active:
        n_naver = resolve_missing_names_naver(unresolved_active, extra_names)
        print(f"Naver fallback resolved {n_naver}/{len(unresolved_active)} missing active names (cached to {EXTRA_NAMES_CACHE}).")

    # 6. Build clean stock_list.json (excluding 5-digit legacy codes and dead nameless 2026-08-25 stubs)
    parquet_entries: list[dict] = []
    parquet_code_set: set[str] = set()
    total_bars = 0
    excluded_parquet = 0
    fallback_nameless = 0

    for code6, n_bars, last_close, last_date in exported_stats:
        parquet_code_set.add(code6)
        total_bars += n_bars
        name = lookup_name(code6) or code6
        entry = {
            "code": code6,
            "name": name,
            "bars": n_bars,
            "lastClose": last_close,
            "lastDate": last_date,
        }
        if not is_clean_stock_entry(entry):
            excluded_parquet += 1
            continue
        if name == code6:
            fallback_nameless += 1
        parquet_entries.append(entry)

    preserved_entries: list[dict] = []
    excluded_preserved = 0
    for item in r2_stock_list:
        c = str(item.get("code", "")).strip()
        if c in parquet_code_set:
            continue
        resolved = lookup_name(c)
        entry = dict(item)
        if resolved:
            entry["name"] = resolved
        if not is_clean_stock_entry(entry):
            excluded_preserved += 1
            continue
        preserved_entries.append(entry)

    combined_stock_list = parquet_entries + preserved_entries
    combined_stock_list.sort(key=lambda x: (x.get("name", ""), x.get("code", "")))
    _atomic_write_text(
        stock_list_path,
        json.dumps(combined_stock_list, ensure_ascii=False, separators=(",", ":")),
    )

    validate_samples(SAMPLE_CODES, OPTIONAL_SAMPLE_CODES)

    elapsed = time.time() - t0
    print("\n=== Export Summary ===")
    print(f"Parquet stocks exported: {len(parquet_entries)} (excluded={excluded_parquet})")
    print(f"Preserved non-parquet R2 entries: {len(preserved_entries)} (excluded={excluded_preserved})")
    print(f"Total stock_list.json entries: {len(combined_stock_list)}")
    print(f"Total daily bars written: {total_bars}")
    print(f"Fallback code-as-name count: {fallback_nameless}")
    print(f"Elapsed: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
