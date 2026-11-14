type KisMode = "prod" | "vps";

interface KisCreds {
  appKey: string;
  appSecret: string;
  mode: KisMode;
  baseUrl: string;
}

interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface NaverAuxData {
  stockName: string;
  targetPrice: number | null;
  per: string;
  pbr: string;
  foreign5dNet: number;
  inst5dNet: number;
  news: Array<{ title: string; press: string; date: string; url: string }>;
}

const KIS_URLS: Record<KisMode, string> = {
  prod: "https://openapi.koreainvestment.com:9443",
  vps: "https://openapivts.koreainvestment.com:29443",
};

const NAVER_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  Referer: "https://m.stock.naver.com/",
};

const KIS_MIN_INTERVAL_MS = 150;
const KIS_EGW00201_WAIT_MS = 600;
const KIS_MAX_RETRIES = 3;

let memTokenCache: { mode: KisMode; accessToken: string; expiresAtTs: number } | null = null;
let kisSlotChain: Promise<void> = Promise.resolve();
let lastKisReqMs = 0;

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function acquireKisSlot(): Promise<void> {
  const prev = kisSlotChain;
  let release!: () => void;
  kisSlotChain = new Promise<void>(resolve => {
    release = resolve;
  });
  await prev;
  try {
    const now = Date.now();
    const waitMs = Math.max(0, lastKisReqMs + KIS_MIN_INTERVAL_MS - now);
    if (waitMs > 0) {
      await sleep(waitMs);
    }
    lastKisReqMs = Date.now();
  } finally {
    release();
  }
}

function toNumber(val: unknown, fallback = 0): number {
  if (val === null || val === undefined || val === "") return fallback;
  const n = Number(String(val).replace(/,/g, "").trim());
  return Number.isFinite(n) ? n : fallback;
}

function parseExpiredTs(obj: any): number {
  if (obj?.expires_at_ts) {
    const n = Number(obj.expires_at_ts);
    if (Number.isFinite(n) && n > 0) return n;
  }
  const expStr = String(obj?.expires_at_str || obj?.access_token_token_expired || "").trim();
  if (expStr) {
    const iso = expStr.includes("T") ? expStr : expStr.replace(" ", "T") + "+09:00";
    const ms = Date.parse(iso);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  return 0;
}

function loadKisCredsFromEnv(env: any): KisCreds | null {
  const appKey = String(env?.KIS_APP_KEY || "").trim();
  const appSecret = String(env?.KIS_APP_SECRET || "").trim();
  if (!appKey || !appSecret) return null;
  const rawMode = String(env?.KIS_MODE || "prod").toLowerCase();
  const mode: KisMode = rawMode === "vps" ? "vps" : "prod";
  return {
    appKey,
    appSecret,
    mode,
    baseUrl: KIS_URLS[mode],
  };
}

async function getKisAccessToken(
  creds: KisCreds,
  kv: any,
  forceRefresh = false
): Promise<string> {
  const nowSec = Math.floor(Date.now() / 1000);
  if (
    !forceRefresh &&
    memTokenCache &&
    memTokenCache.mode === creds.mode &&
    memTokenCache.expiresAtTs > nowSec + 60
  ) {
    return memTokenCache.accessToken;
  }

  const kvKey = `kis_token_${creds.mode}`;
  if (!forceRefresh && kv && typeof kv.get === "function") {
    try {
      const cachedRaw = await kv.get(kvKey, "json");
      if (cachedRaw?.mode === creds.mode && cachedRaw?.access_token) {
        const expTs = parseExpiredTs(cachedRaw);
        if (expTs > nowSec + 60) {
          memTokenCache = {
            mode: creds.mode,
            accessToken: String(cachedRaw.access_token),
            expiresAtTs: expTs,
          };
          return memTokenCache.accessToken;
        }
      }
    } catch {
      // ignore KV read failure
    }
  }

  await acquireKisSlot();
  const resp = await fetch(`${creds.baseUrl}/oauth2/tokenP`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      appkey: creds.appKey,
      appsecret: creds.appSecret,
    }),
  });
  if (!resp.ok) {
    throw new Error(`KIS token HTTP ${resp.status}`);
  }
  const data: any = await resp.json();
  const accessToken = String(data?.access_token || "");
  if (!accessToken) {
    throw new Error(`KIS token error: ${data?.error_code || "UNKNOWN"}`);
  }

  const issuedAtTs = Math.floor(Date.now() / 1000);
  const expiresIn = toNumber(data?.expires_in, 86400);
  let expiresAtTs = parseExpiredTs(data);
  if (expiresAtTs <= issuedAtTs) {
    expiresAtTs = issuedAtTs + expiresIn;
  }
  const expiresAtStr =
    String(data?.access_token_token_expired || "").trim() ||
    new Date(expiresAtTs * 1000).toISOString();

  memTokenCache = {
    mode: creds.mode,
    accessToken,
    expiresAtTs,
  };

  if (kv && typeof kv.put === "function") {
    try {
      const ttl = Math.max(60, Math.floor(expiresAtTs - issuedAtTs - 60));
      await kv.put(
        kvKey,
        JSON.stringify({
          mode: creds.mode,
          access_token: accessToken,
          token_type: String(data?.token_type || "Bearer"),
          expires_in: expiresIn,
          issued_at_ts: issuedAtTs,
          expires_at_ts: expiresAtTs,
          expires_at_str: expiresAtStr,
        }),
        { expirationTtl: ttl }
      );
    } catch {
      // ignore KV write failure
    }
  }

  return accessToken;
}

async function kisGet(
  creds: KisCreds,
  kv: any,
  apiPath: string,
  trId: string,
  params: Record<string, string>
): Promise<any> {
  let tokenRefreshed = false;
  let lastErr: Error | null = null;
  const qs = new URLSearchParams(params).toString();
  const fullUrl = `${creds.baseUrl}${apiPath}?${qs}`;

  for (let attempt = 0; attempt <= KIS_MAX_RETRIES; attempt++) {
    const token = await getKisAccessToken(creds, kv, false);
    await acquireKisSlot();

    try {
      const resp = await fetch(fullUrl, {
        method: "GET",
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          authorization: `Bearer ${token}`,
          appkey: creds.appKey,
          appsecret: creds.appSecret,
          tr_id: trId,
          custtype: "P",
        },
      });

      if (resp.status === 401 && !tokenRefreshed) {
        tokenRefreshed = true;
        await getKisAccessToken(creds, kv, true);
        continue;
      }

      const contentType = resp.headers.get("content-type") || "";
      const data: any = contentType.includes("json") ? await resp.json() : {};
      const msgCd = String(data?.msg_cd || "");

      if ((msgCd === "EGW00123" || msgCd === "EGW00121") && !tokenRefreshed) {
        tokenRefreshed = true;
        await getKisAccessToken(creds, kv, true);
        continue;
      }

      if (resp.status === 429 || resp.status >= 500 || msgCd === "EGW00201") {
        lastErr = new Error(`KIS transient error HTTP=${resp.status} msg_cd=${msgCd}`);
        if (attempt < KIS_MAX_RETRIES) {
          await sleep(KIS_EGW00201_WAIT_MS * (attempt + 1));
          continue;
        }
        throw lastErr;
      }

      if (!resp.ok) {
        throw new Error(`KIS HTTP ${resp.status}`);
      }
      if (String(data?.rt_cd || "0") !== "0") {
        throw new Error(`KIS API error rt_cd=${data?.rt_cd} msg_cd=${msgCd}`);
      }
      return data;
    } catch (err: any) {
      lastErr = err instanceof Error ? err : new Error(String(err));
      if (attempt < KIS_MAX_RETRIES) {
        await sleep(KIS_EGW00201_WAIT_MS);
        continue;
      }
      throw lastErr;
    }
  }

  throw lastErr || new Error("KIS request failed");
}

function getKstDateOffsetStr(daysAgo: number): string {
  const kstMs = Date.now() + 9 * 3600 * 1000 - daysAgo * 86400 * 1000;
  return new Date(kstMs).toISOString().slice(0, 10).replace(/-/g, "");
}

function parseKisBars(rows: any[]): Bar[] {
  const out: Bar[] = [];
  for (const row of rows || []) {
    const dt = String(row?.stck_bsop_date || "").trim();
    const close = toNumber(row?.stck_clpr, 0);
    if (!dt || close <= 0) continue;
    out.push({
      date: dt,
      open: toNumber(row?.stck_oprc, close) || close,
      high: toNumber(row?.stck_hgpr, close) || close,
      low: toNumber(row?.stck_lwpr, close) || close,
      close,
      volume: toNumber(row?.acml_vol, 0),
    });
  }
  return out;
}

async function fetchNaverAux(code: string): Promise<NaverAuxData> {
  const result: NaverAuxData = {
    stockName: code,
    targetPrice: null,
    per: "-",
    pbr: "-",
    foreign5dNet: 0,
    inst5dNet: 0,
    news: [],
  };

  try {
    const [intResp, newsResp] = await Promise.all([
      fetch(`https://m.stock.naver.com/api/stock/${code}/integration`, { headers: NAVER_HEADERS }),
      fetch(`https://m.stock.naver.com/api/news/stock/${code}?pageSize=3`, {
        headers: NAVER_HEADERS,
      }),
    ]);

    if (intResp.ok) {
      const intData: any = await intResp.json();
      if (intData.stockName) result.stockName = intData.stockName;
      for (const ti of intData.totalInfos || []) {
        if (ti.code === "per") result.per = ti.value;
        if (ti.code === "pbr") result.pbr = ti.value;
      }
      if (intData.consensusInfo?.priceTargetMean) {
        const tp = parseInt(
          String(intData.consensusInfo.priceTargetMean).replace(/,/g, ""),
          10
        );
        if (!isNaN(tp)) result.targetPrice = tp;
      }
      for (const row of (intData.dealTrendInfos || []).slice(0, 5)) {
        const f = parseInt(String(row.foreignerPureBuyQuant || "0").replace(/[,+]/g, ""), 10);
        const o = parseInt(String(row.organPureBuyQuant || "0").replace(/[,+]/g, ""), 10);
        if (!isNaN(f)) result.foreign5dNet += f;
        if (!isNaN(o)) result.inst5dNet += o;
      }
    }

    if (newsResp.ok) {
      const nd: any = await newsResp.json();
      if (Array.isArray(nd)) {
        for (const group of nd) {
          for (const item of group.items || []) {
            const dt = String(item.datetime || "");
            result.news.push({
              title: (item.titleFull || item.title || "").replace(/&quot;/g, '"'),
              press: item.officeName || "",
              date: dt.length >= 8 ? `${dt.slice(0, 4)}-${dt.slice(4, 6)}-${dt.slice(6, 8)}` : dt,
              url:
                item.mobileNewsUrl || `https://m.stock.naver.com/domestic/stock/${code}/news`,
            });
            if (result.news.length >= 3) break;
          }
          if (result.news.length >= 3) break;
        }
      }
    }
  } catch {
    // ignore auxiliary naver errors
  }

  return result;
}

function buildQuoteResponse(params: {
  code: string;
  stockName: string;
  bars: Bar[];
  overridePrice?: number;
  overrideRet1d?: number | null;
  w52High?: number;
  foreign5dNet: number;
  inst5dNet: number;
  targetPrice: number | null;
  per: string;
  pbr: string;
  news: Array<{ title: string; press: string; date: string; url: string }>;
  dataSource: string;
}) {
  const { bars } = params;
  const closes = bars.map(b => b.close);
  const lastClose = closes[closes.length - 1];
  const currentPrice =
    params.overridePrice && params.overridePrice > 0 ? params.overridePrice : lastClose;
  const prevC = closes.length >= 2 ? closes[closes.length - 2] : currentPrice;
  const ret1d =
    params.overrideRet1d !== null && params.overrideRet1d !== undefined
      ? params.overrideRet1d
      : prevC > 0
        ? (currentPrice / prevC - 1) * 100
        : 0;

  const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / Math.max(1, arr.length);
  const sma5 = Math.round(avg(closes.slice(-5)));
  const sma20 = Math.round(avg(closes.slice(-20)));
  const sma50 = Math.round(avg(closes.slice(-50)));
  const sma150 = Math.round(avg(closes.slice(-150)));
  const high250 = Math.max(params.w52High || 0, ...bars.map(b => b.high));

  const trs: number[] = [];
  for (let i = Math.max(1, bars.length - 20); i < bars.length; i++) {
    const hi = bars[i].high;
    const lo = bars[i].low;
    const pc = bars[i - 1].close;
    trs.push(Math.max(hi - lo, Math.abs(hi - pc), Math.abs(lo - pc)));
  }
  const atr20 = Math.round(avg(trs));

  return {
    code: params.code,
    name: params.stockName,
    lastDate: bars[bars.length - 1].date,
    currentPrice: Math.round(currentPrice),
    ret1d: Number(ret1d.toFixed(2)),
    sma5,
    sma20,
    sma50,
    sma150,
    high250: Math.round(high250),
    ratio52wHigh: high250 > 0 ? Number(((currentPrice / high250) * 100).toFixed(1)) : 0,
    atr20,
    foreign5dNet: params.foreign5dNet,
    inst5dNet: params.inst5dNet,
    targetPrice: params.targetPrice,
    per: params.per,
    pbr: params.pbr,
    news: params.news,
    recentBars: bars.slice(-90),
    dataSource: params.dataSource,
  };
}

async function fetchViaKis(
  code: string,
  creds: KisCreds,
  kv: any,
  naverAuxPromise: Promise<NaverAuxData>
) {
  const priceData = await kisGet(
    creds,
    kv,
    "/uapi/domestic-stock/v1/quotations/inquire-price",
    "FHKST01010100",
    {
      FID_COND_MRKT_DIV_CODE: "J",
      FID_INPUT_ISCD: code,
    }
  );
  const priceOut = priceData?.output || {};

  const page1Data = await kisGet(
    creds,
    kv,
    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
    "FHKST03010100",
    {
      FID_COND_MRKT_DIV_CODE: "J",
      FID_INPUT_ISCD: code,
      FID_INPUT_DATE_1: getKstDateOffsetStr(90),
      FID_INPUT_DATE_2: getKstDateOffsetStr(0),
      FID_PERIOD_DIV_CODE: "D",
      FID_ORG_ADJ_PRC: "0",
    }
  );

  const page2Data = await kisGet(
    creds,
    kv,
    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
    "FHKST03010100",
    {
      FID_COND_MRKT_DIV_CODE: "J",
      FID_INPUT_ISCD: code,
      FID_INPUT_DATE_1: getKstDateOffsetStr(180),
      FID_INPUT_DATE_2: getKstDateOffsetStr(91),
      FID_PERIOD_DIV_CODE: "D",
      FID_ORG_ADJ_PRC: "0",
    }
  );

  let kisForeign5dNet: number | null = null;
  let kisInst5dNet: number | null = null;
  try {
    const invData = await kisGet(
      creds,
      kv,
      "/uapi/domestic-stock/v1/quotations/inquire-investor",
      "FHKST01010900",
      {
        FID_COND_MRKT_DIV_CODE: "J",
        FID_INPUT_ISCD: code,
      }
    );
    const invRows = (invData?.output || []).slice(0, 5);
    if (invRows.length > 0) {
      kisForeign5dNet = invRows.reduce((acc: number, r: any) => acc + toNumber(r?.frgn_ntby_qty, 0), 0);
      kisInst5dNet = invRows.reduce((acc: number, r: any) => acc + toNumber(r?.orgn_ntby_qty, 0), 0);
    }
  } catch {
    // fallback to naverAux investor flow
  }

  const barMap = new Map<string, Bar>();
  for (const b of parseKisBars(page2Data?.output2 || [])) {
    barMap.set(b.date, b);
  }
  for (const b of parseKisBars(page1Data?.output2 || [])) {
    barMap.set(b.date, b);
  }
  const bars = Array.from(barMap.values()).sort((a, b) => a.date.localeCompare(b.date));
  if (bars.length === 0) {
    throw new Error("KIS returned empty daily bars");
  }

  const naverAux = await naverAuxPromise;
  const kisPrice = toNumber(priceOut?.stck_prpr, 0);
  const kisRet1dRaw = priceOut?.prdy_ctrt;
  const kisRet1d =
    kisRet1dRaw !== undefined && kisRet1dRaw !== null && String(kisRet1dRaw).trim() !== ""
      ? toNumber(kisRet1dRaw, 0)
      : null;
  const kisName = String(priceOut?.hts_kor_isnm || "").trim();
  const stockName =
    kisName || (naverAux.stockName && naverAux.stockName !== code ? naverAux.stockName : code);
  const per = String(priceOut?.per || "").trim() || naverAux.per;
  const pbr = String(priceOut?.pbr || "").trim() || naverAux.pbr;
  const w52High = toNumber(priceOut?.w52_hgpr, 0);

  return buildQuoteResponse({
    code,
    stockName,
    bars,
    overridePrice: kisPrice > 0 ? kisPrice : undefined,
    overrideRet1d: kisRet1d,
    w52High,
    foreign5dNet: kisForeign5dNet !== null ? kisForeign5dNet : naverAux.foreign5dNet,
    inst5dNet: kisInst5dNet !== null ? kisInst5dNet : naverAux.inst5dNet,
    targetPrice: naverAux.targetPrice,
    per,
    pbr,
    news: naverAux.news,
    dataSource: creds.mode === "vps" ? "KIS_REST_VPS" : "KIS_REST_PROD",
  });
}

async function fetchViaNaverFallback(code: string, naverAux: NaverAuxData) {
  const fchartResp = await fetch(
    `https://fchart.stock.naver.com/sise.nhn?symbol=${code}&timeframe=day&count=250&requestType=0`,
    { headers: NAVER_HEADERS }
  );
  const xmlBuf = await fchartResp.arrayBuffer();
  let xmlText = "";
  try {
    xmlText = new TextDecoder("euc-kr").decode(xmlBuf);
  } catch {
    xmlText = new TextDecoder("utf-8").decode(xmlBuf);
  }

  const nameMatch = xmlText.match(/name="([^"]+)"/);
  const stockName =
    naverAux.stockName && naverAux.stockName !== code
      ? naverAux.stockName
      : nameMatch
        ? nameMatch[1]
        : code;

  const itemRegex = /<item\s+data="([^"]+)"\s*\/>/g;
  const bars: Bar[] = [];
  let match: RegExpExecArray | null;
  while ((match = itemRegex.exec(xmlText)) !== null) {
    const parts = match[1].split("|");
    if (parts.length >= 6) {
      const c = parseFloat(parts[4]);
      if (c > 0) {
        bars.push({
          date: parts[0],
          open: parseFloat(parts[1]) || c,
          high: parseFloat(parts[2]) || c,
          low: parseFloat(parts[3]) || c,
          close: c,
          volume: parseFloat(parts[5]) || 0,
        });
      }
    }
  }

  if (bars.length === 0) {
    return null;
  }

  return buildQuoteResponse({
    code,
    stockName,
    bars,
    foreign5dNet: naverAux.foreign5dNet,
    inst5dNet: naverAux.inst5dNet,
    targetPrice: naverAux.targetPrice,
    per: naverAux.per,
    pbr: naverAux.pbr,
    news: naverAux.news,
    dataSource: "NAVER_FALLBACK",
  });
}

export const onRequest = async (context: any) => {
  const url = new URL(context.request.url);
  const corsHeaders = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  if (context.request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  // POST: Send Telegram alert for portfolio item(s)
  if (context.request.method === "POST") {
    try {
      const body = await context.request.json();
      const text = body.text || "";
      const token = body.token || (context.env && context.env.TELEGRAM_TOKEN) || "";
      const chatId = body.chatId || (context.env && context.env.ADMIN_ID) || "";

      if (!text) {
        return new Response(JSON.stringify({ ok: false, error: "Empty message text" }), {
          status: 400,
          headers: corsHeaders,
        });
      }
      if (!token || !chatId) {
        return new Response(
          JSON.stringify({
            ok: false,
            error: "Telegram token/chatId not configured in Cloudflare env; use local CLI or enter Bot Token/Chat ID.",
          }),
          { status: 200, headers: corsHeaders }
        );
      }

      const tgResp = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text,
          disable_web_page_preview: true,
        }),
      });
      const tgJson = await tgResp.json();
      return new Response(JSON.stringify({ ok: tgResp.ok, result: tgJson }), {
        status: 200,
        headers: corsHeaders,
      });
    } catch (err: any) {
      return new Response(JSON.stringify({ ok: false, error: err?.message || "POST error" }), {
        status: 500,
        headers: corsHeaders,
      });
    }
  }

  // GET: Live quote + daily OHLCV metrics (KIS REST API primary, Naver fallback)
  const code = (url.searchParams.get("code") || "").trim();
  if (!code || !/^\d{6}$/.test(code)) {
    return new Response(JSON.stringify({ error: "Valid 6-digit stock code required" }), {
      status: 400,
      headers: corsHeaders,
    });
  }

  try {
    const creds = loadKisCredsFromEnv(context.env);
    const kv = context.env?.PV_KV;
    const naverAuxPromise = fetchNaverAux(code);

    let payload: any = null;
    if (creds) {
      try {
        payload = await fetchViaKis(code, creds, kv, naverAuxPromise);
      } catch {
        const aux = await naverAuxPromise;
        payload = await fetchViaNaverFallback(code, aux);
      }
    } else {
      const aux = await naverAuxPromise;
      payload = await fetchViaNaverFallback(code, aux);
    }

    if (!payload) {
      return new Response(JSON.stringify({ error: "No price data found for code " + code }), {
        status: 404,
        headers: corsHeaders,
      });
    }

    return new Response(JSON.stringify(payload), { status: 200, headers: corsHeaders });
  } catch (err: any) {
    return new Response(JSON.stringify({ error: err?.message || "Failed to fetch live quote" }), {
      status: 500,
      headers: corsHeaders,
    });
  }
};