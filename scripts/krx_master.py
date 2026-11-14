#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KRX Master Universe Loader using official KIS kospi_code.mst and kosdaq_code.mst.

Downloads and caches KIS master files into ``.cache/krx_master/`` with atomic
writes, a 6-hour default TTL, and automatic fallback to cached ``.mst`` files
if network requests fail.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import zipfile
from typing import Any

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from kis_client import is_common_stock_name
except ImportError:  # pragma: no cover
    from scripts.kis_client import is_common_stock_name

CACHE_DIR = os.path.join(BASE_DIR, ".cache", "krx_master")
DEFAULT_CACHE_TTL_SEC = 21600  # 6 hours

MASTER_SPECS: dict[str, dict[str, Any]] = {
    "KOSPI": {
        "url": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
        "mst_filename": "kospi_code.mst",
        "tail": 227,
        "spac_idx": 29,
        "halt_idx": 60,
        "liq_idx": 61,
        "mgmt_idx": 62,
        "listing_date_slice": (105, 113),
    },
    "KOSDAQ": {
        "url": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
        "mst_filename": "kosdaq_code.mst",
        "tail": 221,
        "spac_idx": 24,
        "halt_idx": 55,
        "liq_idx": 56,
        "mgmt_idx": 57,
        "listing_date_slice": (100, 108),
    },
}


def _is_cache_fresh(path: str, ttl_sec: int) -> bool:
    """Return True if ``path`` exists, is non-empty, and is within ``ttl_sec``."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    if ttl_sec <= 0:
        return False
    return (time.time() - os.path.getmtime(path)) < ttl_sec


def _atomic_write_bytes(target_path: str, data: bytes) -> None:
    """Atomically write ``data`` to ``target_path`` via temp file + fsync + replace."""
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=f".{os.path.basename(target_path)}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _ensure_mst_file(
    market: str,
    spec: dict[str, Any],
    force_refresh: bool = False,
    cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
    timeout_sec: int = 20,
) -> str:
    """Ensure the ``.mst`` file for ``market`` is available locally, falling back to cache on network error."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    mst_filename = spec["mst_filename"]
    mst_path = os.path.join(CACHE_DIR, mst_filename)

    if not force_refresh and _is_cache_fresh(mst_path, cache_ttl_sec):
        return mst_path

    try:
        resp = requests.get(spec["url"], timeout=timeout_sec)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            mst_bytes = zf.read(mst_filename)
        _atomic_write_bytes(mst_path, mst_bytes)
        return mst_path
    except Exception as exc:
        if os.path.exists(mst_path) and os.path.getsize(mst_path) > 0:
            print(
                f"[krx_master] Warning: failed to refresh {market} master ({exc}); using cached {mst_path}",
                file=sys.stderr,
            )
            return mst_path
        raise


def _parse_mst_file(mst_path: str, market: str, spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse a raw cp949 KIS ``.mst`` file into a dictionary keyed by 6-char security code."""
    tail = spec["tail"]
    spac_idx = spec["spac_idx"]
    halt_idx = spec["halt_idx"]
    liq_idx = spec["liq_idx"]
    mgmt_idx = spec["mgmt_idx"]
    ld_start, ld_end = spec["listing_date_slice"]

    records: dict[str, dict[str, Any]] = {}
    with open(mst_path, "rb") as f:
        for raw_line in f:
            line = raw_line.rstrip(b"\r\n")
            if len(line) <= tail:
                continue

            part1 = line[:-tail]
            part2 = line[-tail:]

            code = part1[0:9].decode("cp949", "ignore").strip()
            if not code:
                continue

            isin = part1[9:21].decode("ascii", "ignore").strip()
            name = part1[21:].decode("cp949", "ignore").strip()
            group_code = part2[0:2].decode("ascii", "ignore").strip()

            is_spac = part2[spac_idx : spac_idx + 1] == b"Y"
            is_halt = part2[halt_idx : halt_idx + 1] == b"Y"
            is_liq = part2[liq_idx : liq_idx + 1] == b"Y"
            is_mgmt = part2[mgmt_idx : mgmt_idx + 1] == b"Y"
            listing_date = part2[ld_start:ld_end].decode("ascii", "ignore").strip()

            is_pref = group_code == "ST" and code[-1] != "0"
            is_common = (
                group_code == "ST"
                and not is_spac
                and not is_pref
                and not is_halt
                and not is_liq
                and is_common_stock_name(code, name)
            )

            records[code] = {
                "code": code,
                "isin": isin,
                "name": name,
                "market": market,
                "group_code": group_code,
                "is_spac": is_spac,
                "is_halt": is_halt,
                "is_liq": is_liq,
                "is_mgmt": is_mgmt,
                "listing_date": listing_date,
                "is_pref": is_pref,
                "is_etf": group_code == "EF",
                "is_etn": group_code == "EN",
                "is_common": is_common,
            }
    return records


def load_krx_master(
    force_refresh: bool = False,
    cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
) -> dict[str, dict[str, Any]]:
    """Load KOSPI and KOSDAQ master metadata keyed by 6-char security code."""
    master: dict[str, dict[str, Any]] = {}
    for market, spec in MASTER_SPECS.items():
        mst_path = _ensure_mst_file(
            market=market,
            spec=spec,
            force_refresh=force_refresh,
            cache_ttl_sec=cache_ttl_sec,
        )
        master.update(_parse_mst_file(mst_path, market, spec))
    return master


def get_stock_name_map(
    force_refresh: bool = False,
    cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
) -> dict[str, str]:
    """Return a mapping of ``{code: name}`` for all securities in KRX master."""
    master = load_krx_master(force_refresh=force_refresh, cache_ttl_sec=cache_ttl_sec)
    return {code: info["name"] for code, info in master.items()}


def get_active_universe_codes(
    include_etf: bool = True,
    include_etn: bool = False,
    force_refresh: bool = False,
    cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
) -> list[str]:
    """Return sorted active (non-halted, non-liquidation) security codes.

    Filters out ETFs (``group_code == 'EF'``) when ``include_etf`` is False and
    ETNs (``group_code == 'EN'``) when ``include_etn`` is False.
    """
    master = load_krx_master(force_refresh=force_refresh, cache_ttl_sec=cache_ttl_sec)
    codes: list[str] = []
    for code, info in master.items():
        if info["is_halt"] or info["is_liq"]:
            continue
        if info["group_code"] == "EF" and not include_etf:
            continue
        if info["group_code"] == "EN" and not include_etn:
            continue
        codes.append(code)
    return sorted(codes)


if __name__ == "__main__":
    master_data = load_krx_master()
    sample_codes = ["005930", "000660", "000087", "0000D0", "0205H0"]
    for sample_code in sample_codes:
        print(sample_code, master_data.get(sample_code))

    active_common_count = sum(1 for info in master_data.values() if info["is_common"])
    active_non_en_universe = get_active_universe_codes(include_etf=True, include_etn=False)
    print(f"total_master={len(master_data)}")
    print(f"active_common_count={active_common_count}")
    print(f"active_non_en_universe_count={len(active_non_en_universe)}")
