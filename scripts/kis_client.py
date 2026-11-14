#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Official Korea Investment & Securities (KIS) REST API Client
- Reads KIS_APP_KEY, KIS_APP_SECRET, KIS_MODE (prod/vps) from .env
- Thread-safe token caching (.kis_token_cache.json, chmod 600, expires_at_ts check)
- Thread-safe global rate limiter (>= 0.12s/req) + exponential backoff on EGW00201/429/500
- SQLite incremental daily bar cache (.cache/kis_daily_cache.db) with split/mod_yn re-fetch
"""

import json
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_PATH = os.path.join(BASE_DIR, ".env")
DEFAULT_TOKEN_CACHE_PATH = os.path.join(BASE_DIR, ".kis_token_cache.json")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, ".cache", "kis_daily_cache.db")

KIS_URLS = {
    "prod": "https://openapi.koreainvestment.com:9443",
    "vps": "https://openapivts.koreainvestment.com:29443",
}

EXCLUDE_COMMON_KEYWORDS = (
    "스팩", "우B", "우C", "리츠", "채권", "선물", "인버스", "레버리지",
    "ETN", "ETF", "KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL",
    "HANARO", "PLUS", "KIWOOM", "ARIRANG", "KOSEF", "WOORI", "TIMEFOLIO",
    "BNK", "KTOP", "FOCUS", "1Q", "TREX", "마이티", "히어로즈",
)


def is_common_stock_name(code: str, name: str) -> bool:
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if not name:
        return False
    if any(kw in name for kw in EXCLUDE_COMMON_KEYWORDS):
        return False
    if name.endswith("우") or name.endswith("우(전환)"):
        return False
    # Preferred shares in KRX often end with 5, 7, 8, 9, K, L, M etc. while common ends with 0
    if code[-1] != "0" and ("우" in name or code[-1] in ("5", "7", "8", "9")):
        return False
    return True


def _to_float(val, default=0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default


def _to_int(val, default=0) -> int:
    try:
        if val is None or val == "":
            return default
        return int(float(str(val).replace(",", "").strip()))
    except Exception:
        return default


class KISClient:
    def __init__(
        self,
        env_path: str = DEFAULT_ENV_PATH,
        token_cache_path: str = DEFAULT_TOKEN_CACHE_PATH,
        db_path: str = DEFAULT_DB_PATH,
        min_interval: float = 0.125,
        max_retries: int = 4,
    ):
        self.env_path = env_path
        self.token_cache_path = token_cache_path
        self.db_path = db_path
        self.min_interval = max(0.12, float(min_interval))
        self.max_retries = max_retries

        env = self._load_env(env_path)
        self.app_key = env.get("KIS_APP_KEY", "")
        self.app_secret = env.get("KIS_APP_SECRET", "")
        mode = env.get("KIS_MODE", "prod").lower()
        self.mode = mode if mode in KIS_URLS else "prod"
        self.base_url = KIS_URLS[self.mode]

        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET not configured in .env")

        self._token_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._last_req_ts = 0.0

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        self._session.mount("https://", adapter)

        self._access_token = None
        self._expires_at_ts = 0.0

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @staticmethod
    def _load_env(path: str) -> dict:
        env = {}
        if not os.path.exists(path):
            return env
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip()
                if "#" in v and not (v.startswith('"') or v.startswith("'")):
                    v = v.split("#", 1)[0].strip()
                v = v.strip('"').strip("'")
                env[k] = v
        return env

    def _init_db(self):
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_bars (
                        kind TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        date TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        value REAL NOT NULL,
                        mod_yn TEXT DEFAULT 'N',
                        prtt_rate REAL DEFAULT 0.0,
                        flng_cls_code TEXT DEFAULT '00',
                        PRIMARY KEY (kind, symbol, date)
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def _acquire_rate_slot(self):
        with self._rate_lock:
            now = time.monotonic()
            slot = max(now, self._last_req_ts + self.min_interval)
            self._last_req_ts = slot
        wait = slot - now
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _parse_expired_ts(cache_obj: dict) -> float:
        if cache_obj.get("expires_at_ts"):
            return float(cache_obj["expires_at_ts"])
        exp_str = cache_obj.get("access_token_token_expired", "")
        if exp_str:
            try:
                return datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                pass
        cached_at = cache_obj.get("cached_at", "")
        expires_in = _to_float(cache_obj.get("expires_in"), 86400.0)
        if cached_at:
            try:
                return datetime.fromisoformat(cached_at).timestamp() + expires_in
            except Exception:
                pass
        return 0.0

    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = time.time()
            if not force_refresh and self._access_token and now < (self._expires_at_ts - 60):
                return self._access_token

            if not force_refresh and os.path.exists(self.token_cache_path):
                try:
                    with open(self.token_cache_path, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if cached.get("mode") == self.mode and cached.get("access_token"):
                        exp_ts = self._parse_expired_ts(cached)
                        if now < (exp_ts - 60):
                            self._access_token = cached["access_token"]
                            self._expires_at_ts = exp_ts
                            return self._access_token
                except Exception:
                    pass

            self._acquire_rate_slot()
            resp = self._session.post(
                f"{self.base_url}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"KIS token issuance failed: {data.get('error_code')} {data.get('error_description')}")

            exp_ts = self._parse_expired_ts(data)
            if exp_ts <= now:
                exp_ts = now + _to_float(data.get("expires_in"), 86400.0)

            cache_obj = {
                "mode": self.mode,
                "base_url": self.base_url,
                "token_type": data.get("token_type", "Bearer"),
                "access_token": token,
                "expires_in": data.get("expires_in", 86400),
                "access_token_token_expired": data.get("access_token_token_expired", ""),
                "expires_at_ts": exp_ts,
                "cached_at": datetime.now().isoformat(),
            }
            with open(self.token_cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_obj, f, ensure_ascii=False, indent=2)
            os.chmod(self.token_cache_path, 0o600)

            self._access_token = token
            self._expires_at_ts = exp_ts
            return self._access_token

    def _request_get(self, path: str, tr_id: str, params: dict) -> dict:
        token_refreshed = False
        last_err = None
        for attempt in range(self.max_retries + 1):
            token = self.get_access_token(force_refresh=False)
            self._acquire_rate_slot()
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            try:
                resp = self._session.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    params=params,
                    timeout=10,
                )
                if resp.status_code == 401 and not token_refreshed:
                    token_refreshed = True
                    self.get_access_token(force_refresh=True)
                    continue

                data = resp.json() if "json" in resp.headers.get("content-type", "").lower() else {}
                msg_cd = str(data.get("msg_cd", ""))

                if msg_cd in ("EGW00123", "EGW00121") and not token_refreshed:
                    token_refreshed = True
                    self.get_access_token(force_refresh=True)
                    continue

                if resp.status_code in (429, 500) or msg_cd == "EGW00201":
                    last_err = RuntimeError(f"KIS transient error HTTP={resp.status_code} msg_cd={msg_cd} msg1={data.get('msg1')}")
                    if attempt < self.max_retries:
                        backoff = 0.5 * (2 ** attempt) + random.uniform(0.0, 0.15)
                        time.sleep(backoff)
                        continue
                    raise last_err

                resp.raise_for_status()
                if str(data.get("rt_cd", "0")) != "0":
                    raise RuntimeError(f"KIS API error rt_cd={data.get('rt_cd')} msg_cd={msg_cd} msg1={data.get('msg1')}")
                return data
            except (requests.RequestException, ValueError) as e:
                last_err = e
                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt) + random.uniform(0.0, 0.15)
                    time.sleep(backoff)
                    continue
                raise
        raise last_err or RuntimeError("KIS request failed")

    # ------------------------------------------------------------------
    # Public API Methods
    # ------------------------------------------------------------------

    def get_price(self, code: str) -> dict:
        """FHKST01010100: Current stock price & valuation summary."""
        data = self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
        )
        return data.get("output") or {}

    def get_investor_5d(self, code: str) -> dict:
        """FHKST01010900: Latest 5 trading days net buy (qty & trading value)."""
        data = self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
        )
        rows = (data.get("output") or [])[:5]
        return {
            "foreign5dNet": sum(_to_int(r.get("frgn_ntby_qty")) for r in rows),
            "inst5dNet": sum(_to_int(r.get("orgn_ntby_qty")) for r in rows),
            "indiv5dNet": sum(_to_int(r.get("prsn_ntby_qty")) for r in rows),
            "foreign5dTrPbmn": sum(_to_int(r.get("frgn_ntby_tr_pbmn")) for r in rows),
            "inst5dTrPbmn": sum(_to_int(r.get("orgn_ntby_tr_pbmn")) for r in rows),
            "days": len(rows),
        }

    def get_trading_value_rank(self, limit: int = 100) -> list:
        """FHPST01710000: Trading value rank (FID_BLNG_CLS_CODE=3, FID_DIV_CLS_CODE=1, common stocks only)."""
        if self.mode != "prod":
            return []
        data = self._request_get(
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_BLNG_CLS_CODE": "3",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )
        result = []
        for row in data.get("output") or []:
            code = str(row.get("mksc_shrn_iscd", "")).strip()
            name = str(row.get("hts_kor_isnm", "")).strip()
            if not is_common_stock_name(code, name):
                continue
            result.append({
                "code": code,
                "name": name,
                "rank": _to_int(row.get("data_rank")),
                "close": _to_float(row.get("stck_prpr")),
                "volume": _to_float(row.get("acml_vol")),
                "value": _to_float(row.get("acml_tr_pbmn")),
            })
            if len(result) >= limit:
                break
        return result

    # ------------------------------------------------------------------
    # SQLite Incremental Cache Helpers & Bar Fetchers
    # ------------------------------------------------------------------

    def _read_cached_bars(self, kind: str, symbol: str) -> list:
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15)
            try:
                cur = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, value, mod_yn, prtt_rate, flng_cls_code
                    FROM daily_bars
                    WHERE kind = ? AND symbol = ?
                    ORDER BY date ASC
                    """,
                    (kind, symbol),
                )
                rows = cur.fetchall()
            finally:
                conn.close()
        return [
            {
                "date": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
                "value": r[6],
                "value_est": r[6],
                "mod_yn": r[7],
                "prtt_rate": r[8],
                "flng_cls_code": r[9],
            }
            for r in rows
        ]

    def _upsert_bars(self, kind: str, symbol: str, bars: list):
        if not bars:
            return
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15)
            try:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO daily_bars
                    (kind, symbol, date, open, high, low, close, volume, value, mod_yn, prtt_rate, flng_cls_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            kind,
                            symbol,
                            b["date"],
                            b["open"],
                            b["high"],
                            b["low"],
                            b["close"],
                            b["volume"],
                            b["value"],
                            b.get("mod_yn", "N"),
                            b.get("prtt_rate", 0.0),
                            b.get("flng_cls_code", "00"),
                        )
                        for b in bars
                    ],
                )
                conn.commit()
            finally:
                conn.close()

    def _delete_cached_bars(self, kind: str, symbol: str):
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15)
            try:
                conn.execute("DELETE FROM daily_bars WHERE kind = ? AND symbol = ?", (kind, symbol))
                conn.commit()
            finally:
                conn.close()

    def _fetch_stock_page(self, code: str, start_date: str, end_date: str) -> list:
        data = self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start_date,
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
            bars.append({
                "date": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
                "value": val,
                "value_est": val,
                "mod_yn": str(row.get("mod_yn", "N")).strip() or "N",
                "prtt_rate": _to_float(row.get("prtt_rate"), 0.0),
                "flng_cls_code": str(row.get("flng_cls_code", "00")).strip() or "00",
            })
        return bars

    def _fetch_index_page(self, inds_cd: str, start_date: str, end_date: str) -> list:
        data = self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            "FHKUP03500100",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": inds_cd,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        bars = []
        for row in data.get("output2") or []:
            dt = str(row.get("stck_bsop_date", "")).strip()
            c = _to_float(row.get("bstp_nmix_prpr"))
            if not dt or c <= 0:
                continue
            o = _to_float(row.get("bstp_nmix_oprc"), c) or c
            h = _to_float(row.get("bstp_nmix_hgpr"), c) or c
            l = _to_float(row.get("bstp_nmix_lwpr"), c) or c
            v = _to_float(row.get("acml_vol"))
            val = _to_float(row.get("acml_tr_pbmn"))
            bars.append({
                "date": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
                "value": val,
                "value_est": val,
                "mod_yn": str(row.get("mod_yn", "N")).strip() or "N",
                "prtt_rate": 0.0,
                "flng_cls_code": "00",
            })
        return bars

    def _get_bars_incremental(
        self,
        kind: str,
        symbol: str,
        fetch_page_fn,
        min_bars: int,
        max_pages: int = 3,
    ) -> list:
        today = datetime.now()
        end_str = today.strftime("%Y%m%d")
        full_start_str = (today - timedelta(days=460)).strftime("%Y%m%d")
        recent_start_str = (today - timedelta(days=30)).strftime("%Y%m%d")

        cached = self._read_cached_bars(kind, symbol)
        cached_map = {b["date"]: b for b in cached}

        if len(cached) >= min_bars:
            # 1-call incremental check on recent 30 days
            recent_bars = fetch_page_fn(symbol, recent_start_str, end_str)
            needs_full_refetch = False
            for rb in recent_bars:
                if rb.get("mod_yn", "N") != "N" or rb.get("prtt_rate", 0.0) != 0.0:
                    needs_full_refetch = True
                    break
                cb = cached_map.get(rb["date"])
                if cb and abs(cb["close"] - rb["close"]) > max(1.0, cb["close"] * 0.001):
                    # Split / capital event / adjustment mismatch detected
                    needs_full_refetch = True
                    break

            if not needs_full_refetch:
                self._upsert_bars(kind, symbol, recent_bars)
                for rb in recent_bars:
                    cached_map[rb["date"]] = rb
                merged = sorted(cached_map.values(), key=lambda x: x["date"])
                return merged[-max(min_bars, len(merged)):]

            # Split or adjustment sign detected -> wipe and full refetch
            self._delete_cached_bars(kind, symbol)
            cached_map = {}

        # Full / multi-page fetch (up to max_pages = 3 -> ~300 trading days)
        cur_end = end_str
        collected = {}
        for _ in range(max_pages):
            page_bars = fetch_page_fn(symbol, full_start_str, cur_end)
            if not page_bars:
                break
            for b in page_bars:
                collected[b["date"]] = b
            if len(collected) >= min_bars:
                break
            oldest_dt = min(b["date"] for b in page_bars)
            try:
                prev_dt = (datetime.strptime(oldest_dt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            except Exception:
                break
            if prev_dt <= full_start_str or prev_dt >= cur_end:
                break
            cur_end = prev_dt

        bars_list = sorted(collected.values(), key=lambda x: x["date"])
        self._upsert_bars(kind, symbol, bars_list)
        return bars_list

    def get_daily_bars(self, code: str, min_bars: int = 260) -> list:
        """FHKST03010100: Daily OHLCV + actual acml_tr_pbmn (up to 3 pages -> 260+ trading days)."""
        return self._get_bars_incremental("stock", code, self._fetch_stock_page, min_bars=min_bars, max_pages=3)

    def get_index_daily_bars(self, inds_cd: str, min_bars: int = 220) -> list:
        """FHKUP03500100: Daily index OHLCV (0001=KOSPI, 1001=KOSDAQ, up to 3 pages -> 220+ days)."""
        return self._get_bars_incremental("index", inds_cd, self._fetch_index_page, min_bars=min_bars, max_pages=3)
