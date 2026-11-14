#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KRX Quant Stock Recommendation & Portfolio Advisor Engine
- Data source: Official Korea Investment & Securities (KIS) REST API (via kis_client.py)
  - Price, OHLCV, actual trading value (acml_tr_pbmn), index bars, 5d investor flow, trading value rank
  - Naver Finance API is restricted to news, broker reports, consensus targetPrice, and KIS fallback
- Computes verified 15-Year KRX Quant Strategies:
  1) Track 1 (E2_S7_FIP_52W_EOD): Stage 2 + 52W High Proximity + FIP Continuity + Low-Vol/Overheat filter
  2) Track 2 (E1_S8_VolBreakout_EOD): Stage 2 + 52W High + Volume Surge Anchor Breakout
  3) Track 3 (Pullback_Rebreak_Watch): Anchor Candle within 15d + Volume Dry-up near SMA20 Support
- Evaluates user's held portfolio (my_portfolio.json) and dispatches Telegram alerts on Stop/Trailing signals.
"""

import os
import sys
import glob
import json
import math
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kis_client import KISClient, is_common_stock_name  # noqa: E402
from krx_master import load_krx_master  # noqa: E402
from disclosure_filter import filter_candidates_disclosures  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "public", "data")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "quant_advisor_snapshot.json")
PORTFOLIO_PATH = os.path.join(DATA_DIR, "my_portfolio.json")
LOCAL_PORTFOLIO_PATH = os.path.join(BASE_DIR, "my_portfolio.local.json")
LOCAL_PORTFOLIO_CACHE_PATH = os.path.join(BASE_DIR, ".cache", "quant_advisor_local_portfolio.json")
PARQUET_GLOB = os.environ.get(
    "QUANT_PARQUET_GLOB",
    "/home/freshjinyong/quant_data/market_data_daily_parquet/year=*/data.parquet",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/"
}


def load_telegram_credentials():
    env_paths = [
        os.path.join(BASE_DIR, ".env"),
        "/home/freshjinyong/financial_report_bot/.env",
    ]
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("ADMIN_ID", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
    for p in env_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k == "TELEGRAM_TOKEN" and not token:
                                token = v
                            elif k in ("ADMIN_ID", "TELEGRAM_CHAT_ID") and not chat_id:
                                chat_id = v
            except Exception:
                pass
    return token, chat_id


def send_telegram_message(text: str) -> bool:
    token, chat_id = load_telegram_credentials()
    if not token or not chat_id:
        print("[WARN] Telegram credentials not found.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")
        return False


def fetch_fchart_daily(symbol: str, count: int = 260):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}&timeframe=day&count={count}&requestType=0"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.encoding = "euc-kr"
        root = ET.fromstring(r.text)
        chartdata = root.find("chartdata")
        if chartdata is None:
            return None, []
        name = chartdata.attrib.get("name", symbol)
        bars = []
        for item in chartdata.findall("item"):
            raw = item.attrib.get("data", "")
            parts = raw.split("|")
            if len(parts) < 6:
                continue
            dt, o, h, l, c, v = parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
            if c <= 0:
                continue
            if o <= 0:
                o = c
            if h <= 0:
                h = c
            if l <= 0:
                l = c
            bars.append({
                "date": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
                "value_est": c * v
            })
        return name, bars
    except Exception:
        return None, []


def fetch_liquid_universe(kis: KISClient = None, kospi_pages: int = 2, kosdaq_pages: int = 2, page_size: int = 60, max_universe: int = 240):
    """Build liquid common-stock universe (~180-240 stocks) from KOSPI/KOSDAQ market cap top + KIS trading value rank."""
    universe = []
    seen = set()

    for market, pages in [("KOSPI", kospi_pages), ("KOSDAQ", kosdaq_pages)]:
        for page in range(1, pages + 1):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize={page_size}"
            try:
                r = requests.get(url, headers=HEADERS, timeout=8)
                data = r.json()
                stocks = data.get("stocks", [])
                for s in stocks:
                    code = str(s.get("itemCode", "")).strip()
                    name = str(s.get("stockName", "")).strip()
                    end_type = s.get("stockEndType", "stock")
                    if end_type != "stock" or not is_common_stock_name(code, name):
                        continue
                    if code in seen:
                        continue
                    seen.add(code)
                    universe.append({
                        "code": code,
                        "name": name,
                        "market": market,
                        "marketValueStr": s.get("marketValue", ""),
                    })
            except Exception as e:
                print(f"[WARN] Universe fetch failed {market} p{page}: {e}")

    if kis is not None:
        try:
            rank_rows = kis.get_trading_value_rank(limit=60)
            for row in rank_rows:
                code = row["code"]
                if code in seen:
                    continue
                seen.add(code)
                universe.append({
                    "code": code,
                    "name": row["name"],
                    "market": "KRX",
                    "marketValueStr": "",
                })
                if len(universe) >= max_universe:
                    break
        except Exception as e:
            print(f"[WARN] KIS trading value rank failed: {e}")

    return universe[:max_universe]


def fetch_stock_news_only(code: str, limit: int = 3) -> list:
    news = []
    try:
        rn = requests.get(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={limit + 1}", headers=HEADERS, timeout=5)
        if rn.status_code == 200:
            nd = rn.json()
            if isinstance(nd, list):
                for group in nd:
                    for item in (group.get("items") or []):
                        title = item.get("titleFull") or item.get("title") or ""
                        title = title.replace("&quot;", '"').replace("&amp;", "&")
                        dt_raw = item.get("datetime", "")
                        dt_fmt = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]}" if len(dt_raw) >= 8 else dt_raw
                        news.append({
                            "title": title,
                            "press": item.get("officeName", ""),
                            "date": dt_fmt,
                            "url": item.get("mobileNewsUrl", f"https://m.stock.naver.com/domestic/stock/{code}/news")
                        })
                        if len(news) >= limit:
                            break
                    if len(news) >= limit:
                        break
    except Exception:
        pass
    return news


def fetch_stock_enrichment(code: str, kis: KISClient = None):
    """Fetch KIS 5d investor flow & valuation, plus Naver consensus targetPrice, broker reports, and news."""
    info = {
        "per": "-",
        "cnsPer": "-",
        "pbr": "-",
        "foreignRate": "-",
        "marketCap": "-",
        "targetPrice": None,
        "recommMean": None,
        "foreign5dNet": 0,
        "inst5dNet": 0,
        "indiv5dNet": 0,
        "foreign5dTrPbmn": 0,
        "inst5dTrPbmn": 0,
        "reports": [],
        "news": [],
    }
    kis_flow_ok = False
    kis_price_ok = False

    if kis is not None:
        try:
            inv = kis.get_investor_5d(code)
            info["foreign5dNet"] = inv.get("foreign5dNet", 0)
            info["inst5dNet"] = inv.get("inst5dNet", 0)
            info["indiv5dNet"] = inv.get("indiv5dNet", 0)
            info["foreign5dTrPbmn"] = inv.get("foreign5dTrPbmn", 0)
            info["inst5dTrPbmn"] = inv.get("inst5dTrPbmn", 0)
            kis_flow_ok = True
        except Exception:
            pass

        try:
            px = kis.get_price(code)
            if px:
                if px.get("per"):
                    info["per"] = str(px.get("per"))
                if px.get("pbr"):
                    info["pbr"] = str(px.get("pbr"))
                if px.get("hts_frgn_ehrt"):
                    info["foreignRate"] = f"{px.get('hts_frgn_ehrt')}%"
                if px.get("hts_avls"):
                    try:
                        info["marketCap"] = f"{int(float(px['hts_avls'])):,}억원"
                    except Exception:
                        info["marketCap"] = str(px.get("hts_avls"))
                kis_price_ok = True
        except Exception:
            pass

    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration", headers=HEADERS, timeout=6)
        if r.status_code == 200:
            d = r.json()
            if not kis_price_ok:
                for ti in d.get("totalInfos", []) or []:
                    k = ti.get("code")
                    val = ti.get("value", "-")
                    if k == "per":
                        info["per"] = val
                    elif k == "cnsPer":
                        info["cnsPer"] = val
                    elif k == "pbr":
                        info["pbr"] = val
                    elif k == "foreignRate":
                        info["foreignRate"] = val
                    elif k == "marketValue":
                        info["marketCap"] = val
            else:
                for ti in d.get("totalInfos", []) or []:
                    if ti.get("code") == "cnsPer":
                        info["cnsPer"] = ti.get("value", "-")
                        break

            cns = d.get("consensusInfo")
            if cns and isinstance(cns, dict):
                tp_raw = str(cns.get("priceTargetMean", "")).replace(",", "").strip()
                if tp_raw.isdigit():
                    info["targetPrice"] = int(tp_raw)
                info["recommMean"] = cns.get("recommMean")

            if not kis_flow_ok:
                f_sum, o_sum, i_sum = 0, 0, 0
                for dt_row in (d.get("dealTrendInfos") or [])[:5]:
                    def parse_int(s):
                        if not s:
                            return 0
                        s = str(s).replace(",", "").replace("+", "").strip()
                        try:
                            return int(s)
                        except Exception:
                            return 0
                    f_sum += parse_int(dt_row.get("foreignerPureBuyQuant"))
                    o_sum += parse_int(dt_row.get("organPureBuyQuant"))
                    i_sum += parse_int(dt_row.get("individualPureBuyQuant"))
                info["foreign5dNet"] = f_sum
                info["inst5dNet"] = o_sum
                info["indiv5dNet"] = i_sum

            for rep in (d.get("researches") or [])[:3]:
                info["reports"].append({
                    "broker": rep.get("bnm", ""),
                    "title": rep.get("tit", ""),
                    "date": rep.get("wdt", "")
                })
    except Exception:
        pass

    info["news"] = fetch_stock_news_only(code, limit=3)
    return info


def load_parquet_universe_metrics(lookback_days: int = 450):
    """Load trailing daily bars for all active KRX common stocks via one DuckDB query and compute metrics."""
    import duckdb

    parquet_files = sorted(glob.glob(PARQUET_GLOB))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found matching {PARQUET_GLOB}")

    master = load_krx_master()
    common_map = {
        f"A{code}": info
        for code, info in master.items()
        if info.get("is_common")
    }
    if not common_map:
        return [], 0, ""

    cutoff_dt = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    cutoff_year = int(cutoff_dt[:4])
    a_codes = list(common_map.keys())

    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT code, trade_date, open, high, low, close, volume, trading_value
            FROM read_parquet(?)
            WHERE year >= ?
              AND trade_date >= ?
              AND code IN (SELECT unnest(?))
            ORDER BY code, trade_date ASC
            """,
            [parquet_files, cutoff_year, cutoff_dt, a_codes],
        ).fetchall()
    finally:
        con.close()

    all_metrics = []
    max_trade_date = ""
    cur_code = None
    cur_bars = []

    def _flush_stock(a_code: str, bars: list):
        nonlocal max_trade_date
        if not a_code or len(bars) < 160:
            return
        info = common_map.get(a_code)
        if not info:
            return
        m = compute_metrics(info["code"], info["name"], info["market"], bars)
        if m:
            all_metrics.append(m)
            last_dt = bars[-1]["date"]
            if last_dt > max_trade_date:
                max_trade_date = last_dt

    for a_code, dt, o, h, l, c, v, tv in rows:
        if c is None or c <= 0 or not dt:
            continue
        if a_code != cur_code:
            _flush_stock(cur_code, cur_bars)
            cur_code = a_code
            cur_bars = []

        c_f = float(c)
        o_f = float(o) if (o is not None and o > 0) else c_f
        h_f = float(h) if (h is not None and h > 0) else c_f
        l_f = float(l) if (l is not None and l > 0) else c_f
        v_f = float(v) if (v is not None and v > 0) else 0.0
        val_f = float(tv) if (tv is not None and tv > 0) else (c_f * v_f if v_f > 0 else 0.0)
        cur_bars.append({
            "date": str(dt),
            "open": o_f,
            "high": h_f,
            "low": l_f,
            "close": c_f,
            "volume": v_f,
            "value": val_f,
            "value_est": val_f,
        })

    _flush_stock(cur_code, cur_bars)
    return all_metrics, len(common_map), max_trade_date


def compute_metrics(code: str, name: str, market: str, bars: list):
    if len(bars) < 160:
        return None

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["volume"] for b in bars]
    vals = [b.get("value", b.get("value_est", b["close"] * b["volume"])) for b in bars]

    c = closes[-1]
    prev_c = closes[-2]
    ret_1d = (c / prev_c - 1.0) * 100.0 if prev_c > 0 else 0.0
    ret_5d = (c / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 and closes[-6] > 0 else 0.0
    ret_10d = (c / closes[-11] - 1.0) * 100.0 if len(closes) >= 11 and closes[-11] > 0 else 0.0
    ret_20d = (c / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 and closes[-21] > 0 else 0.0
    ret_60d = (c / closes[-61] - 1.0) * 100.0 if len(closes) >= 61 and closes[-61] > 0 else 0.0

    sma5 = sum(closes[-5:]) / 5.0
    sma10 = sum(closes[-10:]) / 10.0
    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0
    sma50_prev20 = sum(closes[-70:-20]) / 50.0 if len(closes) >= 70 else sma50
    sma150 = sum(closes[-150:]) / 150.0
    sma200 = sum(closes[-200:]) / 200.0 if len(closes) >= 200 else sma150

    high250 = max(highs[-250:]) if len(highs) >= 250 else max(highs)
    low250 = min(lows[-250:]) if len(lows) >= 250 else min(lows)
    ratio_52w_high = (c / high250) * 100.0 if high250 > 0 else 0.0

    # Daily returns over last 60 trading days for FIP & Volatility
    rets_60 = []
    pos_days = 0
    neg_days = 0
    for i in range(len(closes) - 60, len(closes)):
        r = (closes[i] / closes[i - 1]) - 1.0
        rets_60.append(r)
        if r > 0:
            pos_days += 1
        elif r < 0:
            neg_days += 1

    mean_r60 = sum(rets_60) / len(rets_60)
    var_r60 = sum((x - mean_r60) ** 2 for x in rets_60) / len(rets_60)
    vol_60d_pct = math.sqrt(var_r60) * 100.0

    # Da, Gurun, Warachka (2014) FIP Information Discreteness (ID)
    sign_pret = 1.0 if ret_60d >= 0 else -1.0
    fip_id60 = sign_pret * ((neg_days - pos_days) / 60.0)
    pos_ratio_60d = (pos_days / 60.0) * 100.0

    # ATR(20) calculation
    trs = []
    for i in range(len(bars) - 20, len(bars)):
        h_i = bars[i]["high"]
        l_i = bars[i]["low"]
        pc_i = bars[i - 1]["close"]
        tr = max(h_i - l_i, abs(h_i - pc_i), abs(l_i - pc_i))
        trs.append(tr)
    atr20 = sum(trs) / len(trs)
    atr20_pct = (atr20 / c) * 100.0 if c > 0 else 0.0

    # Volume & Trading Value averages (prior 20 days excluding today)
    vol_ma20 = sum(vols[-21:-1]) / 20.0 if len(vols) >= 21 else sum(vols[-20:]) / 20.0
    val_ma20 = sum(vals[-21:-1]) / 20.0 if len(vals) >= 21 else sum(vals[-20:]) / 20.0
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
    val_ratio = vals[-1] / val_ma20 if val_ma20 > 0 else 1.0

    # Check if there was a Volume Surge Anchor Candle within the last 3 days (for Track 2, strict A_Strict_Master_Plan)
    recent3_surge = False
    recent3_max_vol_ratio = vol_ratio
    recent3_anchor_date = bars[-1]["date"]
    for idx in range(len(bars) - 3, len(bars)):
        v_ma = sum(vols[idx - 20:idx]) / 20.0
        vr = vols[idx] / v_ma if v_ma > 0 else 1.0
        day_ret = (closes[idx] / closes[idx - 1] - 1.0) * 100.0
        is_bull = bars[idx]["close"] > bars[idx]["open"] and day_ret >= 4.0
        if is_bull and vr >= 2.5:
            recent3_surge = True
            if vr >= recent3_max_vol_ratio:
                recent3_max_vol_ratio = vr
                recent3_anchor_date = bars[idx]["date"]

    # Check if there was an Anchor Candle within last 4~15 days followed by Volume Dry-Up near SMA20 (for Track 3)
    anchor_15d = False
    anchor_15d_date = None
    anchor_15d_vol_ratio = 0.0
    anchor_15d_mid = 0.0
    for idx in range(len(bars) - 15, len(bars) - 2):
        v_ma = sum(vols[idx - 20:idx]) / 20.0
        vr = vols[idx] / v_ma if v_ma > 0 else 1.0
        day_ret = (closes[idx] / closes[idx - 1] - 1.0) * 100.0
        if day_ret >= 5.0 and vr >= 2.2 and bars[idx]["close"] > bars[idx]["open"]:
            anchor_15d = True
            anchor_15d_date = bars[idx]["date"]
            anchor_15d_vol_ratio = vr
            anchor_15d_mid = (bars[idx]["open"] + bars[idx]["close"]) / 2.0

    # Minervini Stage 2 check
    stage2 = (c > sma50) and (sma50 > sma150) and (sma50 >= sma50_prev20 * 0.995) and (c > sma20)

    # Stop & Sizing calculations
    eod_stop_7pct = round(c * 0.93)
    atr_stop_2x = round(c - 2.0 * atr20)
    recommended_stop = max(eod_stop_7pct, atr_stop_2x)
    stop_dist_pct = ((c - recommended_stop) / c) * 100.0 if c > 0 else 7.0
    rec_weight_pct = min(12.5, round(1.0 / max(stop_dist_pct / 100.0, 0.03), 1))

    return {
        "code": code,
        "name": name,
        "market": market,
        "lastDate": bars[-1]["date"],
        "close": int(c),
        "ret1d": round(ret_1d, 2),
        "ret5d": round(ret_5d, 2),
        "ret10d": round(ret_10d, 2),
        "ret20d": round(ret_20d, 2),
        "ret60d": round(ret_60d, 2),
        "sma5": int(round(sma5)),
        "sma10": int(round(sma10)),
        "sma20": int(round(sma20)),
        "sma50": int(round(sma50)),
        "sma150": int(round(sma150)),
        "sma200": int(round(sma200)),
        "high250": int(round(high250)),
        "low250": int(round(low250)),
        "ratio52wHigh": round(ratio_52w_high, 1),
        "fipId60": round(fip_id60, 3),
        "posDays60": pos_days,
        "negDays60": neg_days,
        "posRatio60": round(pos_ratio_60d, 1),
        "vol60dPct": round(vol_60d_pct, 2),
        "atr20": int(round(atr20)),
        "atr20Pct": round(atr20_pct, 2),
        "volRatio": round(vol_ratio, 2),
        "valRatio": round(val_ratio, 2),
        "valTodayEok": round(vals[-1] / 1e8, 1),
        "valMa20Eok": round(val_ma20 / 1e8, 1),
        "stage2": stage2,
        "aboveSma50": c > sma50,
        "aboveSma20": c > sma20,
        "recent3Surge": recent3_surge,
        "recent3MaxVolRatio": round(recent3_max_vol_ratio, 2),
        "recent3AnchorDate": recent3_anchor_date,
        "anchor15d": anchor_15d,
        "anchor15dDate": anchor_15d_date,
        "anchor15dVolRatio": round(anchor_15d_vol_ratio, 2),
        "anchor15dMid": int(round(anchor_15d_mid)),
        "eodStop7pct": int(eod_stop_7pct),
        "atrStop2x": int(atr_stop_2x),
        "recommendedStop": int(recommended_stop),
        "stopDistPct": round(stop_dist_pct, 2),
        "recWeightPct": rec_weight_pct,
    }


def build_recommendations(
    all_metrics: list,
    breadth_pct: float,
    kis: KISClient = None,
    master_map: dict = None,
    rec_exposure: int = 100,
):
    liquid = [m for m in all_metrics if m["valMa20Eok"] >= 50.0]

    # --- TRACK 1: E2_S7_FIP_52W_EOD (A_Strict_Master_Plan: 52주 신고가 90%+ & FIP <= -0.05) ---
    t1_candidates = []
    for m in liquid:
        if not m["stage2"]:
            continue
        if m["close"] < 2000:
            continue
        if m["ratio52wHigh"] < 90.0:
            continue
        if m["fipId60"] > -0.05:
            continue
        if m["ret5d"] > 12.0 or m["ret5d"] < -6.0:
            continue
        if m["ret60d"] <= 0:
            continue
        score = (
            (m["ratio52wHigh"] - 80.0) * 2.2
            + (-m["fipId60"] * 100.0) * 1.5
            + max(0.0, (4.0 - m["vol60dPct"]) * 8.0)
        )
        m_copy = dict(m)
        m_copy["track"] = "TRACK_1_FIP_52W"
        m_copy["trackName"] = "Track 1: 52주 신고가 + FIP 연속추세 (E2_S7_EOD)"
        m_copy["quantScore"] = round(score, 1)
        t1_candidates.append(m_copy)

    t1_candidates.sort(key=lambda x: x["quantScore"], reverse=True)
    t1_top20 = t1_candidates[:20]

    # --- TRACK 2: E1_S8_VolBreakout_EOD (A_Strict_Master_Plan: 52주 신고가 90%+ & 거래량 2.5배+ & +4~15%) ---
    t2_candidates = []
    for m in liquid:
        if not (m["aboveSma50"] and m["aboveSma20"]):
            continue
        if m["close"] < 2000:
            continue
        if m["ratio52wHigh"] < 90.0:
            continue
        today_surge = (m["volRatio"] >= 2.5 and 4.0 <= m["ret1d"] <= 15.0)
        if not (today_surge or m["recent3Surge"]):
            continue
        if m["ret10d"] > 12.0:
            continue
        max_vr = max(m["volRatio"], m["recent3MaxVolRatio"])
        score = max_vr * 18.0 + (m["ratio52wHigh"] - 75.0) * 1.5 + (-m["fipId60"] * 50.0)
        m_copy = dict(m)
        m_copy["track"] = "TRACK_2_VOL_BREAKOUT"
        m_copy["trackName"] = "Track 2: 거래대금 폭발 기준봉 돌파 (E1_S8_EOD)"
        m_copy["quantScore"] = round(score, 1)
        t2_candidates.append(m_copy)

    t2_candidates.sort(key=lambda x: x["quantScore"], reverse=True)
    t2_top20 = t2_candidates[:20]

    # --- TRACK 3: Pullback_Rebreak_Watchlist (기준봉 출현 후 거래절벽 + 20일선 눌림목 대기) ---
    t3_candidates = []
    for m in liquid:
        if not (m["aboveSma50"] and m["anchor15d"]):
            continue
        if m["ratio52wHigh"] < 85.0:
            continue
        dist_sma20 = (m["close"] / m["sma20"] - 1.0) * 100.0
        if not (-3.5 <= dist_sma20 <= 8.5):
            continue
        if m["volRatio"] > 0.90:
            continue
        if m["close"] < m["anchor15dMid"] * 0.97:
            continue
        score = (1.0 - m["volRatio"]) * 45.0 + m["anchor15dVolRatio"] * 10.0 + (m["ratio52wHigh"] - 70.0)
        m_copy = dict(m)
        m_copy["track"] = "TRACK_3_PULLBACK_WATCH"
        m_copy["trackName"] = "Track 3: 기준봉 후 거래절벽 20일선 눌림목 (Type B 대기)"
        m_copy["quantScore"] = round(score, 1)
        t3_candidates.append(m_copy)

    t3_candidates.sort(key=lambda x: x["quantScore"], reverse=True)
    t3_top20 = t3_candidates[:20]

    # --- Layer 1/2 Disclosure Kill-Switch on top-20 candidates per track BEFORE slicing 8/8/6 ---
    pre_screen = t1_top20 + t2_top20 + t3_top20
    candidate_codes = list(dict.fromkeys(c["code"] for c in pre_screen))
    code_to_name = {c["code"]: c["name"] for c in pre_screen}
    disc_map = (
        filter_candidates_disclosures(
            candidate_codes,
            master_map=master_map,
            lookback_days=60,
            max_workers=6,
        )
        if candidate_codes
        else {}
    )

    track1_top = [c for c in t1_top20 if not disc_map.get(c["code"], {}).get("blocked", False)][:8]
    track2_top = [c for c in t2_top20 if not disc_map.get(c["code"], {}).get("blocked", False)][:8]
    track3_top = [c for c in t3_top20 if not disc_map.get(c["code"], {}).get("blocked", False)][:6]

    sources = [res.get("source", "") for res in disc_map.values() if res.get("source")]
    if not disc_map:
        summary_source = "none"
    elif any(s.startswith("opendart") for s in sources):
        summary_source = "opendart"
    elif any(s.startswith("naver") for s in sources):
        summary_source = "naver"
    else:
        summary_source = sources[0] if sources else "none"

    blocked_samples = []
    for code, res in disc_map.items():
        if res.get("blocked"):
            name = code_to_name.get(code) or (master_map or {}).get(code, {}).get("name", code)
            blocked_samples.append({
                "code": code,
                "name": name,
                "reasons": res.get("block_reasons", []),
            })

    disclosure_summary = {
        "source": summary_source,
        "checkedCount": len(disc_map),
        "blockedCount": len(blocked_samples),
        "blockedSamples": blocked_samples[:10],
    }

    all_picks = track1_top + track2_top + track3_top
    unique_codes = list({p["code"] for p in all_picks})
    enrichment_map = {}
    if unique_codes:
        with ThreadPoolExecutor(max_workers=min(6, len(unique_codes))) as ex:
            fut_map = {ex.submit(fetch_stock_enrichment, c, kis): c for c in unique_codes}
            for fut in as_completed(fut_map):
                c = fut_map[fut]
                try:
                    enrichment_map[c] = fut.result()
                except Exception:
                    enrichment_map[c] = {}

    for p in all_picks:
        enr = enrichment_map.get(p["code"], {})
        p["enrichment"] = enr
        reasons = synthesize_reasons(p, enr, breadth_pct)
        disc = disc_map.get(p["code"], {})
        for cat in disc.get("catalysts", []):
            reasons["catalysts"].append(f"[공시 촉매] {cat}")
        for warn in disc.get("warnings", []):
            reasons["catalysts"].append(f"[공시/시장경보 유의] {warn}")
        if rec_exposure == 0:
            p["recWeightPct"] = 0.0
            reasons["catalysts"].insert(0, "레짐 차단 (Breadth < 55% 또는 지수 < SMA20): 신규 진입 중단 · 관찰 전용")
        p["reasons"] = reasons

    return track1_top, track2_top, track3_top, disclosure_summary


def synthesize_reasons(p: dict, enr: dict, breadth_pct: float):
    formulas = []
    if p["stage2"]:
        formulas.append({
            "source": "Mark Minervini (초수익 성장주 투자, SEPA Stage 2)",
            "metric": f"현재가({p['close']:,}원) > 20일선({p['sma20']:,}원) > 50일선({p['sma50']:,}원) > 150일선({p['sma150']:,}원)",
            "desc": "단기·중기·장기 이동평균선이 완전 정배열된 제2단계(기관 매집 추세 국면) 조건을 충족합니다."
        })
    else:
        formulas.append({
            "source": "이동평균 수급선 지지 (50일선 추세 필터)",
            "metric": f"현재가({p['close']:,}원) > 50일선({p['sma50']:,}원)",
            "desc": "중기 수급선 위에서 상승 추세 구조를 유지하고 있습니다."
        })

    formulas.append({
        "source": "George & Hwang (2004, Journal of Finance 52주 신고가 모멘텀)",
        "metric": f"52주 최고가({p['high250']:,}원) 대비 {p['ratio52wHigh']:.1f}% 근접",
        "desc": "52주 최고가 대비 90~100%(관찰군 85%+) 구간에 위치하여 상단 악성 매물대(Overhead Supply) 저항이 최소화된 신고가 탄력 구간입니다."
    })

    formulas.append({
        "source": "Da, Gurun & Warachka (2014, RFS Frog-in-the-Pan 연속정보 가설)",
        "metric": f"60일 FIP ID = {p['fipId60']:+.3f} (양봉 {p['posDays60']}일 / 음봉 {p['negDays60']}일, 승률 {p['posRatio60']:.1f}%)",
        "desc": "단발성 작전 급등이 아니라 60거래일 동안 양봉 빈도가 음봉을 압도하며 기관성 자금이 점진적으로 유입된 고품질 추세입니다."
    })

    if p["track"] == "TRACK_1_FIP_52W":
        formulas.append({
            "source": "고봉찬·김진우 (2014, 증권학회지 저변동성 결합) & 5일 과열 필터",
            "metric": f"60일 일간변동성 {p['vol60dPct']:.2f}% / 최근 5일 수익률 {p['ret5d']:+.2f}%",
            "desc": "단기 급등 과열(+12% 초과) 없이 변동성이 통제된 상태에서 신고가 권역을 돌파 중이므로 추격매수 물림 위험이 낮습니다."
        })
    elif p["track"] == "TRACK_2_VOL_BREAKOUT":
        max_vr = max(p["volRatio"], p["recent3MaxVolRatio"])
        formulas.append({
            "source": "Nicolas Darvas & 한국 실전 기준봉 돌파 (거래대금 폭발 공식)",
            "metric": f"최근 기준봉({p['recent3AnchorDate']}) 거래량 20일 평균 대비 {max_vr:.2f}배 폭발 (일평균 대금 {p['valMa20Eok']:,.0f}억)",
            "desc": "박스권 상단에서 평소 대비 2.5배 이상의 대량 거래대금이 유입되며 새로운 가격 발견(Price Discovery)이 시작된 기준봉 타점입니다."
        })
    elif p["track"] == "TRACK_3_PULLBACK_WATCH":
        formulas.append({
            "source": "성필규·한봉호 눌림목 & 실전 Type B (기준봉 후 거래절벽 20일선 지지)",
            "metric": f"기준봉({p['anchor15dDate']}, 거래량 {p['anchor15dVolRatio']:.1f}배) 후 당일 거래량 {p['volRatio']:.2f}배로 감소, 기준봉 중심값({p['anchor15dMid']:,}원) 지지",
            "desc": "강력한 기준봉 출현 이후 매도 거래량이 마르는 거래절벽(Volume Dry-Up)이 나타나며 20일 이동평균선 부근에서 재돌파를 준비 중입니다."
        })

    catalysts = []
    f_net = enr.get("foreign5dNet", 0)
    i_net = enr.get("inst5dNet", 0)
    if f_net > 0 and i_net > 0:
        catalysts.append(f"[양매수 수급] 최근 5거래일 외국인 {f_net:+,}주, 기관 {i_net:+,}주 동반 순매수 유입 (추종모멘텀 논문 부합)")
    elif f_net > 0:
        catalysts.append(f"[외국인 수급] 최근 5거래일 외국인 {f_net:+,}주 순매수 유입 (외인 지분율 {enr.get('foreignRate', '-')})")
    elif i_net > 0:
        catalysts.append(f"[기관 수급] 최근 5거래일 기관 {i_net:+,}주 순매수 유입 (기관 추세 형성)")
    else:
        catalysts.append(f"[수급 현황] 최근 5거래일 외국인 {f_net:+,}주 / 기관 {i_net:+,}주 (가격 추세 우선 모니터링)")

    tp = enr.get("targetPrice")
    if tp and p["close"] > 0:
        upside = (tp / p["close"] - 1.0) * 100.0
        catalysts.append(f"[컨센서스 & 밸류] 증권사 평균 목표주가 {tp:,}원 (괴리율 {upside:+.1f}%), PER {enr.get('per', '-')} / 추정PER {enr.get('cnsPer', '-')}, PBR {enr.get('pbr', '-')}")
    else:
        catalysts.append(f"[밸류에이션] 시가총액 {enr.get('marketCap', '-')}, PER {enr.get('per', '-')} / 추정PER {enr.get('cnsPer', '-')}, PBR {enr.get('pbr', '-')}")

    for rep in (enr.get("reports") or [])[:2]:
        catalysts.append(f"[증권사 리포트 ({rep['date']})] {rep['broker']}: {rep['title']}")

    for nw in (enr.get("news") or [])[:2]:
        catalysts.append(f"[주요 뉴스 ({nw['date']})] {nw['title']} ({nw['press']})")

    return {
        "formulas": formulas,
        "catalysts": catalysts,
    }


def _evaluate_single_holding(item: dict, kis: KISClient = None):
    code = str(item.get("code", "")).strip()
    buy_price = float(item.get("buyPrice", 0))
    qty = int(item.get("qty", 1))
    buy_date = str(item.get("buyDate", "")).replace("-", "").strip()
    track = item.get("track", "TRACK_1_FIP_52W")

    if not code or buy_price <= 0:
        return None

    name = item.get("name", code)
    bars = []
    live_price = 0.0
    if kis is not None:
        try:
            bars = kis.get_daily_bars(code, min_bars=160)
        except Exception:
            bars = []
        try:
            px = kis.get_price(code)
            if px:
                if px.get("hts_kor_isnm"):
                    name = px["hts_kor_isnm"]
                if px.get("stck_prpr"):
                    live_price = float(px["stck_prpr"])
        except Exception:
            pass

    if not bars:
        n_fb, bars = fetch_fchart_daily(code, count=160)
        if n_fb:
            name = n_fb
    if not bars:
        return None

    enr = {"foreign5dNet": 0, "inst5dNet": 0, "news": []}
    if kis is not None:
        try:
            inv = kis.get_investor_5d(code)
            enr["foreign5dNet"] = inv.get("foreign5dNet", 0)
            enr["inst5dNet"] = inv.get("inst5dNet", 0)
            enr["news"] = fetch_stock_news_only(code, limit=2)
        except Exception:
            enr = fetch_stock_enrichment(code, kis=None)
    else:
        enr = fetch_stock_enrichment(code, kis=None)

    closes = [b["close"] for b in bars]
    c = live_price if live_price > 0 else closes[-1]
    prev_c = closes[-2] if len(closes) >= 2 else c
    ret_1d = (c / prev_c - 1.0) * 100.0 if prev_c > 0 else 0.0

    sma5 = sum(closes[-5:]) / 5.0 if len(closes) >= 5 else c
    sma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else c
    sma50 = sum(closes[-50:]) / 50.0 if len(closes) >= 50 else c

    trs = []
    for i in range(max(1, len(bars) - 20), len(bars)):
        h_i, l_i, pc_i = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h_i - l_i, abs(h_i - pc_i), abs(l_i - pc_i)))
    atr20 = sum(trs) / len(trs) if trs else c * 0.025

    held_bars = [b for b in bars if b["date"] >= buy_date] if buy_date and len(buy_date) == 8 else bars[-5:]
    hold_days = max(1, len(held_bars))
    peak_high = max([b["high"] for b in held_bars]) if held_bars else max(c, buy_price)
    mfe_pct = (peak_high / buy_price - 1.0) * 100.0

    gross_pnl_pct = (c / buy_price - 1.0) * 100.0
    net_pnl_pct = gross_pnl_pct - 0.23
    net_pnl_krw = int(round((c - buy_price) * qty - (c * qty * 0.0023)))

    hard_stop_7pct = int(round(buy_price * 0.93))
    atr_stop_2x = int(round(buy_price - 2.0 * atr20))
    active_hard_stop = max(hard_stop_7pct, atr_stop_2x)
    sma20_price = int(round(sma20))

    dist_to_hard_stop_pct = ((c - active_hard_stop) / c) * 100.0
    dist_to_sma20_pct = ((c - sma20_price) / c) * 100.0

    if c <= active_hard_stop:
        signal = "SELL_STOP"
        signal_label = "손절 청산 신호 (EOD Hard Stop 이탈)"
        advice = (
            f"현재가({int(c):,}원)가 설정된 손절선({active_hard_stop:,}원, 매수가 대비 -7% / 2ATR)을 하향 이탈했습니다. "
            f"15년 실증 백테스트 원칙에 따라 희망회로(물타기·비자발적 장기보유)를 배제하고 장마감 동시호가 또는 익일 시가에 전량 기계적 손절을 권고합니다."
        )
    elif c < sma20_price and hold_days >= 2:
        signal = "SELL_TRAIL"
        signal_label = "추세 이탈 청산 신호 (20일 생명선 종가 이탈)"
        advice = (
            f"현재가({int(c):,}원)가 20일 이동평균선({sma20_price:,}원) 아래로 이탈했습니다 (현재 손익 {net_pnl_pct:+.2f}%). "
            f"추세추종(E2_S7/E1_S8) 원칙상 20일선 종가 이탈은 단기·중기 추세 종료를 의미하므로 익일 시가 전량 청산을 권고합니다."
        )
    elif hold_days >= 10 and gross_pnl_pct < 3.0:
        signal = "TIME_STOP"
        signal_label = "시간 손절 권고 (10거래일 경과 탄력 둔화)"
        advice = (
            f"매수 후 {hold_days}거래일이 경과했으나 수익률({net_pnl_pct:+.2f}%)이 목표 탄력(+3.0%)에 미달합니다. "
            f"Mark Minervini 시간 손절(Time Stop) 원칙에 따라 기회비용 회수를 위해 신규 주도주로의 교체 매매를 권고합니다."
        )
    elif dist_to_hard_stop_pct <= 2.0 or dist_to_sma20_pct <= 1.8:
        signal = "WARNING"
        signal_label = "주의 경계 (손절선/20일선 버퍼 2% 이내 근접)"
        advice = (
            f"현재가({int(c):,}원)가 핵심 방어선(손절선 {active_hard_stop:,}원 / 20일선 {sma20_price:,}원)에 근접했습니다. "
            f"장중 아랫꼬리 흔들기에는 매도하지 않되(EOD Stop 원칙), 15:20 종가 기준으로 방어선 하향 돌파 시 즉시 청산할 준비가 필요합니다."
        )
    elif gross_pnl_pct >= 10.0:
        signal = "HOLD_TRAIL"
        signal_label = "수익 추세 순항 중 (조기 반익절 금지 / 20일선 트레일링)"
        advice = (
            f"현재 {net_pnl_pct:+.2f}% 수익 구간입니다 (최고 도달 수익률 +{mfe_pct:.1f}%). "
            f"15년 KRX 실증 결과 +10% 조기 반익절은 손익비를 2.80에서 1.35로 훼손합니다. 20일선({sma20_price:,}원) 종가 이탈 전까지 전량 보유(Let Winners Run)하십시오."
        )
    else:
        signal = "HOLD"
        signal_label = "정상 보유 (추세 및 방어선 양호)"
        advice = (
            f"현재가({int(c):,}원)가 20일선({sma20_price:,}원) 및 손절선({active_hard_stop:,}원) 위에서 정상 추세를 유지 중입니다. "
            f"종가 기준 방어선 이탈 전까지 보유를 유지합니다."
        )

    return {
        "code": code,
        "name": name or item.get("name", code),
        "buyPrice": int(buy_price),
        "qty": qty,
        "buyDate": buy_date,
        "track": track,
        "currentPrice": int(c),
        "ret1d": round(ret_1d, 2),
        "grossPnlPct": round(gross_pnl_pct, 2),
        "netPnlPct": round(net_pnl_pct, 2),
        "netPnlKrw": net_pnl_krw,
        "holdDays": hold_days,
        "mfePct": round(mfe_pct, 2),
        "sma5": int(round(sma5)),
        "sma20": sma20_price,
        "sma50": int(round(sma50)),
        "atr20": int(round(atr20)),
        "hardStop7pct": hard_stop_7pct,
        "atrStop2x": atr_stop_2x,
        "activeHardStop": active_hard_stop,
        "distToStopPct": round(dist_to_hard_stop_pct, 2),
        "distToSma20Pct": round(dist_to_sma20_pct, 2),
        "signal": signal,
        "signalLabel": signal_label,
        "advice": advice,
        "foreign5dNet": enr.get("foreign5dNet", 0),
        "inst5dNet": enr.get("inst5dNet", 0),
        "news": (enr.get("news") or [])[:2],
    }


def evaluate_portfolio(portfolio_items: list, kis: KISClient = None, master_map: dict = None):
    evaluated = []
    alerts = []
    if not portfolio_items:
        return evaluated, alerts

    with ThreadPoolExecutor(max_workers=min(6, len(portfolio_items))) as ex:
        futs = [ex.submit(_evaluate_single_holding, item, kis) for item in portfolio_items]
        for fut in futs:
            try:
                eval_obj = fut.result()
            except Exception:
                eval_obj = None
            if not eval_obj:
                continue
            evaluated.append(eval_obj)
            if eval_obj["signal"] in ("SELL_STOP", "SELL_TRAIL", "TIME_STOP", "WARNING"):
                alerts.append(eval_obj)

    if evaluated:
        pf_codes = list(dict.fromkeys(e["code"] for e in evaluated))
        pf_disc_map = filter_candidates_disclosures(
            pf_codes,
            master_map=master_map,
            lookback_days=60,
            max_workers=min(6, len(pf_codes)),
        )
        for eval_obj in evaluated:
            disc = pf_disc_map.get(eval_obj["code"], {})
            disc_warnings = list(
                dict.fromkeys((disc.get("block_reasons") or []) + (disc.get("warnings") or []))
            )
            eval_obj["disclosureWarnings"] = disc_warnings
            eval_obj["disclosureCatalysts"] = disc.get("catalysts", [])

    return evaluated, alerts


def format_telegram_report(snapshot: dict, evaluated_portfolio: list, alerts: list):
    lines = []
    m = snapshot["macro"]
    lines.append("[돈테크랩 퀀트 어드바이저 & 포트폴리오 리포트]")
    lines.append(f"기준일시: {snapshot['generatedAt']} (최근 장마감: {m['lastMarketDate']})")
    lines.append(
        f"시장 레짐: {m['regimeLabel']} | Breadth(50일선 상회): {m['breadthPct']:.1f}% | 권장 주식비중: {m['recEquityExposurePct']}%"
    )
    lines.append(
        f"KOSPI: {m['kospiClose']:,.2f} ({m['kospiRet1d']:+.2f}%) | KOSDAQ: {m['kosdaqClose']:,.2f} ({m['kosdaqRet1d']:+.2f}%)"
    )
    df = m.get("disclosureFilter")
    if df:
        lines.append(
            f"공시/시장경보 필터({df.get('source', 'none')}): 상위 {df.get('checkedCount', 0)}종목 점검 / {df.get('blockedCount', 0)}종목 Kill-Switch 차단"
        )
    lines.append("-" * 42)

    if evaluated_portfolio:
        lines.append(f"[1. 보유 포트폴리오 진단 ({len(evaluated_portfolio)}종목)]")
        for p in evaluated_portfolio:
            lines.append(
                f"- {p['name']}({p['code']}): [{p['signalLabel']}] 현재가 {p['currentPrice']:,}원 / 매수가 {p['buyPrice']:,}원 ({p['netPnlPct']:+.2f}%, {p['netPnlKrw']:+,}원) | 손절선 {p['activeHardStop']:,}원 | 20일선 {p['sma20']:,}원"
            )
            if p["signal"] in ("SELL_STOP", "SELL_TRAIL", "TIME_STOP", "WARNING"):
                lines.append(f"  -> 권고: {p['advice']}")
            if p.get("disclosureWarnings"):
                lines.append(f"  -> [공시/시장경보 유의] {', '.join(p['disclosureWarnings'])}")
        lines.append("-" * 42)

    lines.append("[2. 금일 학술·실전 검증 상위 추천 종목]")
    for idx, t in enumerate(snapshot["recommendations"]["track1"][:3], 1):
        lines.append(
            f"[Track1 신고가+FIP {idx}] {t['name']}({t['code']}) 현재가 {t['close']:,}원 ({t['ret1d']:+.2f}%) | 52주고가비율 {t['ratio52wHigh']:.1f}% | FIP {t['fipId60']:+.3f} | 손절선 {t['recommendedStop']:,}원 | 20일선 {t['sma20']:,}원"
        )
    for idx, t in enumerate(snapshot["recommendations"]["track2"][:3], 1):
        lines.append(
            f"[Track2 거래폭발돌파 {idx}] {t['name']}({t['code']}) 현재가 {t['close']:,}원 ({t['ret1d']:+.2f}%) | 거래량 {max(t['volRatio'], t['recent3MaxVolRatio']):.1f}배 | 52주고가비율 {t['ratio52wHigh']:.1f}% | 손절선 {t['recommendedStop']:,}원"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="KRX Quant Recommendation & Portfolio Advisor Engine")
    parser.add_argument("--notify-telegram", action="store_true", help="Send Telegram report")
    parser.add_argument("--only-portfolio", action="store_true", help="Only re-evaluate portfolio")
    parser.add_argument(
        "--source",
        choices=["auto", "parquet", "kis"],
        default="auto",
        help="Universe & OHLCV data source (default: auto)",
    )
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    master_map = load_krx_master()

    kis = None
    try:
        kis = KISClient()
    except Exception as e:
        print(f"[WARN] KISClient init failed ({e}); falling back to Naver API.")

    parquet_available = bool(glob.glob(PARQUET_GLOB))
    if args.source == "parquet":
        if not parquet_available:
            print(f"[ERROR] --source parquet requested, but no files found at {PARQUET_GLOB}")
            sys.exit(1)
        use_parquet = True
    elif args.source == "kis":
        use_parquet = False
    else:
        use_parquet = parquet_available

    if use_parquet:
        data_source = "PARQUET_FULL_KIS" if kis is not None else "PARQUET_FULL_NAVER"
    else:
        data_source = f"KIS_REST_{kis.mode.upper()}" if kis is not None else "NAVER_FALLBACK"

    using_local_portfolio = os.path.exists(LOCAL_PORTFOLIO_PATH)
    active_portfolio_path = LOCAL_PORTFOLIO_PATH if using_local_portfolio else PORTFOLIO_PATH
    portfolio_items = []
    if os.path.exists(active_portfolio_path):
        try:
            with open(active_portfolio_path, "r", encoding="utf-8") as f:
                loaded_pf = json.load(f)
            if isinstance(loaded_pf, list):
                portfolio_items = loaded_pf
        except Exception as e:
            print(f"[WARN] Failed to load portfolio ({active_portfolio_path}): {e}")

    def save_local_portfolio_cache(evaluated_list, updated_at):
        if not using_local_portfolio:
            return
        os.makedirs(os.path.dirname(LOCAL_PORTFOLIO_CACHE_PATH), exist_ok=True)
        with open(LOCAL_PORTFOLIO_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "portfolioEvaluated": evaluated_list,
                "portfolioUpdatedAt": updated_at,
                "dataSource": data_source,
            }, f, ensure_ascii=False, indent=2)

    if args.only_portfolio and os.path.exists(SNAPSHOT_PATH):
        t0 = time.monotonic()
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        evaluated_portfolio, alerts = evaluate_portfolio(portfolio_items, kis=kis, master_map=master_map)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snapshot["dataSource"] = data_source
        snapshot["portfolioEvaluated"] = [] if using_local_portfolio else evaluated_portfolio
        snapshot["portfolioUpdatedAt"] = now_str
        save_local_portfolio_cache(evaluated_portfolio, now_str)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        if args.notify_telegram:
            msg = format_telegram_report(snapshot, evaluated_portfolio, alerts)
            send_telegram_message(msg)
        elapsed = time.monotonic() - t0
        print(f"[OK] Portfolio evaluated ({len(evaluated_portfolio)} items, {len(alerts)} alerts) in {elapsed:.2f}s [{data_source}].")
        return

    print(f"[1/4] Fetching KOSPI & KOSDAQ index bars and top liquid universe ({data_source})...")
    kospi_bars, kosdaq_bars = [], []
    if kis is not None:
        try:
            kospi_bars = kis.get_index_daily_bars("0001", min_bars=220)
            kosdaq_bars = kis.get_index_daily_bars("1001", min_bars=220)
        except Exception as e:
            print(f"[WARN] KIS index bars failed ({e}); falling back to Naver.")
    if len(kospi_bars) < 200:
        _, kospi_bars = fetch_fchart_daily("KOSPI", 260)
    if len(kosdaq_bars) < 200:
        _, kosdaq_bars = fetch_fchart_daily("KOSDAQ", 260)

    max_scanned_date = ""
    if use_parquet:
        print("[2/4] Scanning full KRX common-stock universe from Parquet via DuckDB (trailing ~450d)...")
        all_metrics, master_common_cnt, max_scanned_date = load_parquet_universe_metrics(lookback_days=450)
        print(f"  -> Master common stocks: {master_common_cnt} | Computed metrics (>=160 bars): {len(all_metrics)} stocks (maxDate={max_scanned_date})")
    else:
        universe = fetch_liquid_universe(kis=kis, kospi_pages=2, kosdaq_pages=2, page_size=60, max_universe=240)
        print(f"  -> Universe count: {len(universe)} stocks")

        print("[2/4] Fetching 260-day daily bars in parallel...")
        all_metrics = []

        def fetch_and_compute(u):
            bars = []
            name = u["name"]
            if kis is not None:
                try:
                    bars = kis.get_daily_bars(u["code"], min_bars=260)
                except Exception:
                    bars = []
            if not bars:
                fb_name, bars = fetch_fchart_daily(u["code"], 260)
                if fb_name:
                    name = fb_name
            if not bars:
                return None
            return compute_metrics(u["code"], name, u["market"], bars)

        with ThreadPoolExecutor(max_workers=6) as ex:
            fut_map = {ex.submit(fetch_and_compute, u): u for u in universe}
            for fut in as_completed(fut_map):
                try:
                    m = fut.result()
                    if m:
                        all_metrics.append(m)
                except Exception:
                    pass

        max_scanned_date = max((m["lastDate"] for m in all_metrics), default="")
        print(f"  -> Computed metrics for {len(all_metrics)} stocks")

    above50_cnt = sum(1 for m in all_metrics if m["aboveSma50"])
    above20_cnt = sum(1 for m in all_metrics if m["aboveSma20"])
    stage2_cnt = sum(1 for m in all_metrics if m["stage2"])
    total_cnt = max(1, len(all_metrics))
    breadth50_pct = round((above50_cnt / total_cnt) * 100.0, 1)
    breadth20_pct = round((above20_cnt / total_cnt) * 100.0, 1)
    stage2_pct = round((stage2_cnt / total_cnt) * 100.0, 1)

    def calc_idx_summary(bars):
        if not bars or len(bars) < 200:
            return {"close": 0, "ret1d": 0, "ret20d": 0, "sma20": 0, "sma50": 0, "sma200": 0, "aboveSma50": False}
        closes = [b["close"] for b in bars]
        c = closes[-1]
        r1 = (c / closes[-2] - 1.0) * 100.0
        r20 = (c / closes[-21] - 1.0) * 100.0
        s20 = sum(closes[-20:]) / 20.0
        s50 = sum(closes[-50:]) / 50.0
        s200 = sum(closes[-200:]) / 200.0
        return {
            "date": bars[-1]["date"],
            "close": round(c, 2),
            "ret1d": round(r1, 2),
            "ret20d": round(r20, 2),
            "sma20": round(s20, 2),
            "sma50": round(s50, 2),
            "sma200": round(s200, 2),
            "aboveSma20": c > s20,
            "aboveSma50": c > s50,
            "aboveSma200": c > s200,
        }

    kp = calc_idx_summary(kospi_bars)
    kq = calc_idx_summary(kosdaq_bars)

    if breadth50_pct >= 55.0 and kp["aboveSma20"]:
        regime_code = "BULL_ATTACK"
        regime_label = "강세 공격 구간 (Breadth >= 55% & KOSPI > SMA20)"
        rec_exposure = 100
        regime_desc = (
            f"유동성 상위 종목 중 50일선 상회 비율(Market Breadth)이 {breadth50_pct:.1f}%로 55% 이상이며 KOSPI가 20일 이동평균선 위에 위치합니다. "
            f"A_Strict_Master_Plan 검증(2012~2026 PF 1.30, 2022~2026 OOS PF 1.91·CAGR +13.73%·MDD -16.18%) 기준 공격 운용이 허용되는 구간입니다."
        )
    elif breadth50_pct >= 45.0:
        regime_code = "SELECTIVE_ROTATION"
        regime_label = "신규 진입 보류·관찰 전용 (Breadth 45~55% 또는 KOSPI < SMA20)"
        rec_exposure = 0
        regime_desc = (
            f"시장 Breadth({breadth50_pct:.1f}%)가 55% 미만이거나 KOSPI가 20일 이동평균선을 하회합니다. "
            f"OOS 검증상 레짐 차단 미적용 완화규칙(B1)은 PF 0.73(-74.35%)으로 손실이 발생했으므로 신규 진입 비중을 0%로 차단하고 RS 주도주 관찰만 수행합니다."
        )
    else:
        regime_code = "DEFENSIVE_BEAR"
        regime_label = "현금 100% 방어·신규 매수 전면 중단 (Breadth < 45%)"
        rec_exposure = 0
        regime_desc = (
            f"시장 Breadth(50일선 상회 비율)가 {breadth50_pct:.1f}%로 하락장 임계치(45%)를 하회합니다. "
            f"하락장 휩쏘(Whipsaw) 방지를 위해 권장 주식 비중 0%(현금 100% 방어)를 적용하고 신규 매수를 전면 중단합니다."
        )

    print("[3/4] Building Track 1 / Track 2 / Track 3 recommendations & screening disclosures...")
    t1, t2, t3, disclosure_summary = build_recommendations(
        all_metrics,
        breadth50_pct,
        kis=kis,
        master_map=master_map,
        rec_exposure=rec_exposure,
    )

    print("[4/4] Evaluating user portfolio...")
    evaluated_portfolio, alerts = evaluate_portfolio(portfolio_items, kis=kis, master_map=master_map)

    snapshot = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataSource": data_source,
        "macro": {
            "lastMarketDate": max_scanned_date if (use_parquet and max_scanned_date) else (kp.get("date", "") or max_scanned_date),
            "regimeCode": regime_code,
            "regimeLabel": regime_label,
            "recEquityExposurePct": rec_exposure,
            "regimeDesc": regime_desc,
            "breadthPct": breadth50_pct,
            "breadth20Pct": breadth20_pct,
            "stage2Pct": stage2_pct,
            "universeCount": total_cnt,
            "kospiClose": kp["close"],
            "kospiRet1d": kp["ret1d"],
            "kospiRet20d": kp["ret20d"],
            "kospiSma20": kp["sma20"],
            "kospiSma50": kp["sma50"],
            "kospiSma200": kp["sma200"],
            "kosdaqClose": kq["close"],
            "kosdaqRet1d": kq["ret1d"],
            "kosdaqRet20d": kq["ret20d"],
            "kosdaqSma20": kq["sma20"],
            "kosdaqSma50": kq["sma50"],
            "kosdaqSma200": kq["sma200"],
            "disclosureFilter": disclosure_summary,
        },
        "strategySpecs": {
            "track1": {
                "id": "TRACK_1_FIP_52W",
                "name": "Track 1: 52주 신고가 + FIP 연속추세 (E2_S7_EOD)",
                "booksAndPapers": "Mark Minervini (SEPA Stage 2) + George & Hwang (2004, 52W High) + Da, Gurun & Warachka (2014, FIP) + 고봉찬·김진우 (2014, 저변동성)",
                "backtest15y": "2012~2026-11-13 통합 검증(A_Strict_Master_Plan, N=891, 왕복비용 0.35%): 누적 +122.22%, PF 1.30, 손익비 2.73, MDD -47.16% | 2022~2026-11-13 OOS(N=208): 누적 +85.30%, CAGR +13.73%, PF 1.91, 손익비 3.46, MDD -16.18% (완화규칙 B1 OOS PF 0.73 대비 우위)",
                "entryRule": "레짐 조건(Breadth >= 55% & KOSPI > SMA20) + Stage 2 정배열(주가>20일>50일>150일) + 52주 최고가 90%+ + 60일 FIP ID <= -0.05 + 5일 수익률 -6%~+12% + 60일 수익률 > 0 + 주가 >= 2,000원 + 20일 평균대금 >= 50억 + Layer 1/2 공시 필터 통과",
                "exitRule": "장중 아랫꼬리 손절 금지. 15:20 종가 기준 매수가 대비 -7%(또는 2ATR) 이탈 시 손절, 종가 기준 20일 이동평균선 이탈 시 전량 추세 청산 (조기 반익절 금지)"
            },
            "track2": {
                "id": "TRACK_2_VOL_BREAKOUT",
                "name": "Track 2: 거래대금 폭발 기준봉 돌파 (E1_S8_EOD)",
                "booksAndPapers": "Nicolas Darvas (박스 돌파) + William O'Neil (CAN SLIM 거래량) + 유목민·박병창 (거래대금 기준봉)",
                "backtest15y": "Track 1+2 통합 2012~2026-11-13 검증(A_Strict_Master_Plan): Full PF 1.30(+122.22%), 2022~2026-11-13 OOS PF 1.91(CAGR +13.73%, MDD -16.18%) | 엄격 기준봉(거래량 2.5배+, 일간 +4~15%) 미충족 완화규칙(B1)은 OOS PF 0.73(-74.35%)으로 폐기",
                "entryRule": "레짐 조건(Breadth >= 55% & KOSPI > SMA20) + 50일·20일선 상회 + 52주 최고가 90%+ + 당일 거래량 2.5배+·등락률 +4~15%(또는 최근 3일 내 거래량 2.5배+·+4%+ 양봉 기준봉) + 10일 수익률 <= +12% + 주가 >= 2,000원 + 20일 평균대금 >= 50억 + Layer 1/2 공시 필터 통과",
                "exitRule": "종가 기준 매수가 대비 -7%(또는 기준봉 시가) 이탈 시 손절, 20일 이동평균선 종가 이탈 시 전량 익절"
            },
            "track3": {
                "id": "TRACK_3_PULLBACK_WATCH",
                "name": "Track 3: 기준봉 후 거래절벽 20일선 눌림목 (Type B 대기)",
                "booksAndPapers": "성필규·한봉호 (중심선 눌림목) + 실전 Type B (거래절벽 후 거래량 재돌파)",
                "backtest15y": "관찰 전용(Watchlist): 2012~2026-11-13 OOS 통합 포트폴리오 미포함(개별 15일 기준봉 눌림목 후보군). 레짐 차단 구간(Breadth < 55% 또는 KOSPI < SMA20)에서는 비중 0%로 관찰만 수행",
                "entryRule": "50일선 상회 + 52주 최고가 85%+ + 최근 15일 내 거래량 2.2배+·+5%+ 양봉 기준봉 후 당일 거래량 0.90배 이하 거래절벽 + 20일선 이격 -3.5%~+8.5% + 기준봉 중심값 97%+ 지지 + Layer 1/2 공시 필터 통과",
                "exitRule": "종가 기준 20일선 또는 기준봉 중심값 이탈 시 손절, 직전 전고점 도달 및 20일선 트레일링 청산"
            }
        },
        "recommendations": {
            "track1": t1,
            "track2": t2,
            "track3": t3,
        },
        "portfolioEvaluated": [] if using_local_portfolio else evaluated_portfolio,
        "portfolioUpdatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_local_portfolio_cache(evaluated_portfolio, snapshot["portfolioUpdatedAt"])

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[DONE] Snapshot saved to {SNAPSHOT_PATH}")
    print(f"  -> Regime: {regime_label} (Breadth 50d: {breadth50_pct}%)")
    print(f"  -> Track 1 picks: {len(t1)} | Track 2 picks: {len(t2)} | Track 3 watchlist: {len(t3)}")

    if args.notify_telegram:
        msg = format_telegram_report(snapshot, evaluated_portfolio, alerts)
        ok = send_telegram_message(msg)
        print(f"  -> Telegram notification sent: {ok}")


if __name__ == "__main__":
    main()
