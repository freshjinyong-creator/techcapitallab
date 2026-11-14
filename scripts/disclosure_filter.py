#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layer 2 Disclosure Kill-Switch & OpenDART-Ready Catalyst Filter.

Evaluates trailing disclosures (default 60 days) for KRX equities using:
1. OpenDART ``api/list.json`` (with ``CORPCODE.xml`` cached in ``.cache/dart_corp_codes.json``)
   when ``OPENDART_API_KEY`` or ``DART_API_KEY`` is configured in ``os.environ`` or ``.env``.
2. Zero-key fallback via Naver Mobile Stock Disclosure API
   (``https://m.stock.naver.com/api/stock/{code}/disclosure``) with a 1-hour atomic
   local JSON cache in ``.cache/disclosure_cache/``.

Combines Layer 1 KIS ``.mst`` risk flags (when ``master_map`` is supplied) with
Layer 2 disclosure triggers to produce per-code ``blocked``, ``block_reasons``,
``warnings``, ``catalysts``, ``source``, and ``recent_titles``.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

CACHE_DIR = os.path.join(BASE_DIR, ".cache", "disclosure_cache")
DART_CORP_CODE_CACHE = os.path.join(BASE_DIR, ".cache", "dart_corp_codes.json")
DISCLOSURE_CACHE_TTL_SEC = 3600  # 1 hour
DART_CORP_CODE_TTL_SEC = 7 * 86400  # 7 days

NAVER_DISCLOSURE_URL = "https://m.stock.naver.com/api/stock/{code}/disclosure"
OPENDART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
OPENDART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

_DOTENV_LOCK = threading.Lock()
_DART_CORP_LOCK = threading.Lock()


def _read_env_key(keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty key from ``os.environ`` or ``BASE_DIR/.env`` without logging values."""
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val

    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return None

    with _DOTENV_LOCK:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :].strip()
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k in keys:
                        v = v.strip().strip('"').strip("'").strip()
                        if v:
                            return v
        except OSError:
            return None
    return None


def _get_opendart_api_key() -> str | None:
    """Return configured OpenDART API key from environment or ``.env``, or ``None``."""
    return _read_env_key(("OPENDART_API_KEY", "DART_API_KEY"))


def _is_cache_fresh(path: str, ttl_sec: int) -> bool:
    """Return True if ``path`` exists, is non-empty, and is within ``ttl_sec``."""
    if not os.path.exists(path) or os.path.getsize(path) == 0 or ttl_sec <= 0:
        return False
    return (time.time() - os.path.getmtime(path)) < ttl_sec


def _atomic_write_json(target_path: str, payload: Any) -> None:
    """Atomically serialize ``payload`` as JSON to ``target_path``."""
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_dir,
            prefix=f".{os.path.basename(target_path)}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(payload, tmp_file, ensure_ascii=False)
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


def _load_dart_corp_codes(api_key: str, timeout_sec: int = 30) -> dict[str, str]:
    """Load ``{stock_code: corp_code}`` mapping from cache or OpenDART ``CORPCODE.xml``."""
    with _DART_CORP_LOCK:
        if _is_cache_fresh(DART_CORP_CODE_CACHE, DART_CORP_CODE_TTL_SEC):
            try:
                with open(DART_CORP_CODE_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
            except (OSError, json.JSONDecodeError):
                pass

        try:
            resp = requests.get(
                OPENDART_CORPCODE_URL,
                params={"crtfc_key": api_key},
                timeout=timeout_sec,
            )
            resp.raise_for_status()
            mapping: dict[str, str] = {}
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
                if not xml_name:
                    raise ValueError("CORPCODE.xml missing in OpenDART archive")
                with zf.open(xml_name) as xml_file:
                    root = ET.parse(xml_file).getroot()
                    for item in root.findall("list"):
                        stock_code = (item.findtext("stock_code") or "").strip()
                        corp_code = (item.findtext("corp_code") or "").strip()
                        if stock_code and corp_code:
                            mapping[stock_code] = corp_code
            if mapping:
                _atomic_write_json(DART_CORP_CODE_CACHE, mapping)
            return mapping
        except Exception:
            if os.path.exists(DART_CORP_CODE_CACHE):
                try:
                    with open(DART_CORP_CODE_CACHE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        return data
                except (OSError, json.JSONDecodeError):
                    pass
            return {}


def _parse_disclosure_date(raw_dt: str | None) -> datetime | None:
    """Parse ISO-8601 datetime or ``YYYYMMDD`` date strings into a naive ``datetime``."""
    if not raw_dt:
        return None
    s = raw_dt.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            pass
    digits = re.sub(r"[^0-9]", "", s)
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d")
        except ValueError:
            pass
    return None


def _normalize_title(title: str) -> str:
    """Normalize middle dots and repeated whitespace in disclosure titles."""
    normalized = title.replace("ㆍ", "·").replace("•", "·")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _fetch_opendart_disclosures(
    code: str,
    corp_code: str,
    api_key: str,
    lookback_days: int,
    timeout_sec: int = 12,
) -> list[dict[str, str]]:
    """Fetch disclosures for ``corp_code`` from OpenDART ``api/list.json``."""
    end_dt = datetime.now()
    bgn_dt = end_dt - timedelta(days=max(lookback_days, 1))
    resp = requests.get(
        OPENDART_LIST_URL,
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_dt.strftime("%Y%m%d"),
            "end_de": end_dt.strftime("%Y%m%d"),
            "page_no": 1,
            "page_count": 100,
        },
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    payload = resp.json()
    status = str(payload.get("status", ""))
    if status == "013":  # No disclosures in date range
        return []
    if status != "000":
        raise RuntimeError(f"OpenDART error status={status} for {code}")

    items: list[dict[str, str]] = []
    for row in payload.get("list") or []:
        title = str(row.get("report_nm") or "").strip()
        dt_str = str(row.get("rcept_dt") or "").strip()
        if title:
            items.append({"title": title, "datetime": dt_str})
    return items


def _fetch_naver_disclosures(
    code: str,
    page_size: int = 30,
    timeout_sec: int = 12,
) -> list[dict[str, str]]:
    """Fetch recent disclosures for ``code`` from Naver mobile stock disclosure API."""
    url = NAVER_DISCLOSURE_URL.format(code=code)
    resp = requests.get(
        url,
        params={"page": 1, "pageSize": page_size},
        headers=DEFAULT_HEADERS,
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []

    items: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        dt_str = str(row.get("datetime") or "").strip()
        if title:
            items.append({"title": title, "datetime": dt_str})
    return items


def _get_cached_or_fetch_disclosures(
    code: str,
    lookback_days: int,
    api_key: str | None,
    corp_codes: dict[str, str],
    cache_ttl_sec: int = DISCLOSURE_CACHE_TTL_SEC,
) -> tuple[list[dict[str, str]], str]:
    """Return ``(disclosures, source)`` using 1h cache, OpenDART if available, or Naver zero-key."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{code}.json")

    if _is_cache_fresh(cache_path, cache_ttl_sec):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and isinstance(cached.get("items"), list):
                cached_src = str(cached.get("source") or "naver")
                return cached["items"], f"cache:{cached_src}"
        except (OSError, json.JSONDecodeError):
            pass

    items: list[dict[str, str]] | None = None
    source = "naver"

    if api_key and code in corp_codes:
        try:
            items = _fetch_opendart_disclosures(
                code=code,
                corp_code=corp_codes[code],
                api_key=api_key,
                lookback_days=lookback_days,
            )
            source = "opendart"
        except Exception:
            items = None

    if items is None:
        try:
            items = _fetch_naver_disclosures(code=code, page_size=30)
            source = "naver"
        except Exception:
            # Stale cache fallback if network fails
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
                        return cached["items"], f"stale_cache:{cached.get('source', 'naver')}"
                except (OSError, json.JSONDecodeError):
                    pass
            return [], "error"

    try:
        _atomic_write_json(
            cache_path,
            {"code": code, "source": source, "fetched_at": int(time.time()), "items": items},
        )
    except OSError:
        pass

    return items, source


def _classify_disclosure_title(title: str) -> tuple[list[str], list[str], list[str]]:
    """Classify a single normalized disclosure title into ``(block_reasons, warnings, catalysts)``."""
    t = _normalize_title(title)
    t_compact = t.replace(" ", "")
    t_upper = t.upper()

    blocks: list[str] = []
    warnings: list[str] = []
    catalysts: list[str] = []

    # 1. Toxic CB / BW / EB issuance (exclude acquisition, cancellation, early redemption, conversion)
    has_mezzanine = (
        "전환사채" in t_compact
        or "신주인수권부사채" in t_compact
        or "교환사채" in t_compact
        or bool(re.search(r"\b(CB|BW|EB)\b", t_upper))
    )
    mezzanine_exclusions = (
        "취득",
        "소각",
        "만기전취득",
        "전환청구",
        "전환가액",
        "조정",
        "행사",
    )
    if has_mezzanine and ("발행" in t_compact or "결정" in t_compact):
        if not any(excl in t_compact for excl in mezzanine_exclusions):
            blocks.append(f"CB/BW/EB발행:{t}")

    # 2. Dilutive 유상증자 결정 / 권리락(유상증자) / 감자
    if "유상증자" in t_compact and ("결정" in t_compact or "권리락" in t_compact):
        blocks.append(f"유상증자:{t}")
    elif "권리락" in t_compact and "무상증자" not in t_compact and "유상" in t_compact:
        blocks.append(f"권리락(유상증자):{t}")

    if any(k in t_compact for k in ("감자결정", "자본감소", "유상감자", "무상감자")) or (
        "감자" in t_compact and ("권리락" in t_compact or "주권매매거래정지" in t_compact)
    ):
        blocks.append(f"감자:{t}")

    # 3. Audit opinion issues
    if any(k in t_compact for k in ("의견거절", "부적정")) or (
        any(k in t_compact for k in ("감사의견", "감사보고서", "검토의견"))
        and any(k in t_compact for k in ("한정", "부적정", "의견거절", "범위제한"))
    ):
        blocks.append(f"감사의견이상:{t}")

    # 4. Administrative issue (관리종목 지정 / 지정우려)
    if "관리종목" in t_compact and "지정해제" not in t_compact and "해제" not in t_compact:
        if any(k in t_compact for k in ("지정", "우려", "사유발생")):
            blocks.append(f"관리종목:{t}")

    # 5. Delisting / Substantive listing eligibility review
    if "상장폐지" in t_compact or "상장적격성실질심사" in t_compact:
        blocks.append(f"상장폐지/실질심사:{t}")

    # 6. Embezzlement / Breach of trust
    if "횡령" in t_compact or "배임" in t_compact:
        blocks.append(f"횡령/배임:{t}")

    # 7. Rehabilitation / Bankruptcy
    if any(k in t_compact for k in ("회생절차", "기업회생", "회생계획", "파산")):
        blocks.append(f"회생/파산:{t}")

    # 8. Unfaithful disclosure designation (designation -> block, advance notice -> warning)
    if "불성실공시" in t_compact:
        if "지정예고" in t_compact or "예고" in t_compact:
            warnings.append(f"불성실공시예고:{t}")
        elif "지정" in t_compact:
            blocks.append(f"불성실공시지정:{t}")

    # Positive catalysts
    if "단일판매·공급계약" in t_compact or "단일판매공급계약" in t_compact:
        if "해지" in t_compact:
            warnings.append(f"공급계약해지:{t}")
        elif "정정" not in t_compact:
            catalysts.append(f"단일판매·공급계약:{t}")

    if any(k in t_compact for k in ("영업(잠정)실적", "영업잠정실적", "잠정실적")):
        if "정정" not in t_compact:
            catalysts.append(f"영업(잠정)실적:{t}")

    if "신규시설투자" in t_compact:
        if not any(k in t_compact for k in ("철회", "취소", "해지")):
            catalysts.append(f"신규시설투자:{t}")

    if ("자기주식" in t_compact or "자사주" in t_compact) and (
        "취득" in t_compact or "소각" in t_compact
    ):
        if "처분" not in t_compact and "해지" not in t_compact:
            catalysts.append(f"자기주식취득/소각:{t}")

    return blocks, warnings, catalysts


def _extract_master_flags(info: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Extract Layer 1 KIS ``.mst`` hard-risk blocks and soft-risk warnings."""
    if not info:
        return [], []

    blocks: list[str] = []
    warnings: list[str] = []

    if info.get("is_halt"):
        blocks.append("KIS_MST:거래정지")
    if info.get("is_liq"):
        blocks.append("KIS_MST:정리매매")
    if info.get("is_mgmt"):
        blocks.append("KIS_MST:관리종목")
    if info.get("is_inv_caution"):
        blocks.append("KIS_MST:투자주의환기")
    if info.get("is_unfaithful"):
        blocks.append("KIS_MST:불성실공시법인")

    mkt_warn = str(info.get("mkt_warn") or "00")
    if mkt_warn in ("02", "03"):
        label = "투자경고" if mkt_warn == "02" else "투자위험"
        blocks.append(f"KIS_MST:{label}({mkt_warn})")
    elif mkt_warn == "01":
        warnings.append("KIS_MST:투자주의(01)")

    if info.get("warn_notice"):
        warnings.append("KIS_MST:시장경보지정예고")
    if info.get("is_short_overheat") or str(info.get("short_overheat") or "0") not in ("0", "N", ""):
        warnings.append(f"KIS_MST:단기과열({info.get('short_overheat', '')})")
    if info.get("low_liq"):
        warnings.append("KIS_MST:저유동성")

    return blocks, warnings


def _evaluate_single_code(
    code: str,
    master_info: dict[str, Any] | None,
    lookback_days: int,
    api_key: str | None,
    corp_codes: dict[str, str],
) -> dict[str, Any]:
    """Evaluate Layer 1 + Layer 2 disclosures for a single security ``code``."""
    block_reasons, warnings = _extract_master_flags(master_info)
    catalysts: list[str] = []

    items, source = _get_cached_or_fetch_disclosures(
        code=code,
        lookback_days=lookback_days,
        api_key=api_key,
        corp_codes=corp_codes,
    )

    cutoff = datetime.now() - timedelta(days=max(lookback_days, 1))
    recent_titles: list[str] = []

    for item in items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        parsed_dt = _parse_disclosure_date(item.get("datetime"))
        if parsed_dt is not None and parsed_dt < cutoff:
            continue

        recent_titles.append(_normalize_title(title))
        b, w, c = _classify_disclosure_title(title)
        block_reasons.extend(b)
        warnings.extend(w)
        catalysts.extend(c)

    # Deduplicate while preserving order
    block_reasons = list(dict.fromkeys(block_reasons))
    warnings = list(dict.fromkeys(warnings))
    catalysts = list(dict.fromkeys(catalysts))

    return {
        "blocked": len(block_reasons) > 0,
        "block_reasons": block_reasons,
        "warnings": warnings,
        "catalysts": catalysts,
        "source": source,
        "recent_titles": recent_titles,
    }


def filter_candidates_disclosures(
    codes: list[str],
    master_map: dict[str, dict[str, Any]] | None = None,
    lookback_days: int = 60,
    max_workers: int = 6,
) -> dict[str, dict[str, Any]]:
    """Evaluate Layer 1 master flags and Layer 2 trailing disclosures for candidate ``codes``.

    Args:
        codes: List of 6-character KRX security codes.
        master_map: Optional dictionary from ``krx_master.load_krx_master()``.
        lookback_days: Trailing calendar-day window for disclosure evaluation (default 60).
        max_workers: Thread pool concurrency for fetching disclosures (default 6).

    Returns:
        Mapping ``{code: {blocked, block_reasons, warnings, catalysts, source, recent_titles}}``.
    """
    unique_codes = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
    if not unique_codes:
        return {}

    api_key = _get_opendart_api_key()
    corp_codes = _load_dart_corp_codes(api_key) if api_key else {}
    master_lookup = master_map or {}

    results: dict[str, dict[str, Any]] = {}
    worker_count = max(1, min(max_workers, len(unique_codes)))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                _evaluate_single_code,
                code,
                master_lookup.get(code),
                lookback_days,
                api_key,
                corp_codes,
            ): code
            for code in unique_codes
        }
        for fut in as_completed(future_map):
            code = future_map[fut]
            try:
                results[code] = fut.result()
            except Exception as exc:
                b, w = _extract_master_flags(master_lookup.get(code))
                results[code] = {
                    "blocked": len(b) > 0,
                    "block_reasons": b,
                    "warnings": w + [f"DISCLOSURE_FETCH_ERROR:{type(exc).__name__}"],
                    "catalysts": [],
                    "source": "error",
                    "recent_titles": [],
                }

    return {code: results[code] for code in unique_codes if code in results}


if __name__ == "__main__":
    from krx_master import load_krx_master

    master = load_krx_master()
    sample = ["005930", "000660", "000040", "009730", "001000", "065150"]
    out = filter_candidates_disclosures(sample, master_map=master, lookback_days=60, max_workers=6)
    for c, res in out.items():
        name = master.get(c, {}).get("name", "?")
        print(
            f"{c} ({name}) | blocked={res['blocked']} | src={res['source']} | "
            f"blocks={res['block_reasons']} | warn={res['warnings']} | cat={res['catalysts']} | "
            f"titles={len(res['recent_titles'])}"
        )
