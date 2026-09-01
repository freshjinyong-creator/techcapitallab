export interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  value?: number;
}

export interface LinePoint {
  time: string;
  value: number;
}

export interface BollingerBandsResult {
  upper: LinePoint[];
  middle: LinePoint[];
  lower: LinePoint[];
}

export interface MACDResult {
  macd: LinePoint[];
  signal: LinePoint[];
  histogram: { time: string; value: number; color: string }[];
}

/**
 * Simple Moving Average (SMA)
 */
export function calculateSMA(data: CandleData[], period: number): LinePoint[] {
  const result: LinePoint[] = [];
  if (!data || data.length < period) return result;

  let sum = 0;
  for (let i = 0; i < period; i++) {
    sum += data[i].close;
  }
  result.push({ time: data[period - 1].time, value: Math.round((sum / period) * 100) / 100 });

  for (let i = period; i < data.length; i++) {
    sum += data[i].close - data[i - period].close;
    result.push({ time: data[i].time, value: Math.round((sum / period) * 100) / 100 });
  }

  return result;
}

/**
 * Exponential Moving Average (EMA)
 */
export function calculateEMA(data: CandleData[], period: number): LinePoint[] {
  const result: LinePoint[] = [];
  if (!data || data.length < period) return result;

  const k = 2 / (period + 1);
  let ema = 0;

  for (let i = 0; i < period; i++) {
    ema += data[i].close;
  }
  ema /= period;
  result.push({ time: data[period - 1].time, value: Math.round(ema * 100) / 100 });

  for (let i = period; i < data.length; i++) {
    ema = data[i].close * k + ema * (1 - k);
    result.push({ time: data[i].time, value: Math.round(ema * 100) / 100 });
  }

  return result;
}

/**
 * Bollinger Bands
 */
export function calculateBollingerBands(
  data: CandleData[],
  period: number = 20,
  multiplier: number = 2
): BollingerBandsResult {
  const upper: LinePoint[] = [];
  const middle: LinePoint[] = [];
  const lower: LinePoint[] = [];

  if (!data || data.length < period) return { upper, middle, lower };

  for (let i = period - 1; i < data.length; i++) {
    const slice = data.slice(i - period + 1, i + 1);
    const mean = slice.reduce((acc, c) => acc + c.close, 0) / period;
    const variance =
      slice.reduce((acc, c) => acc + Math.pow(c.close - mean, 2), 0) / period;
    const stdDev = Math.sqrt(variance);

    const time = data[i].time;
    middle.push({ time, value: Math.round(mean * 100) / 100 });
    upper.push({ time, value: Math.round((mean + multiplier * stdDev) * 100) / 100 });
    lower.push({ time, value: Math.round((mean - multiplier * stdDev) * 100) / 100 });
  }

  return { upper, middle, lower };
}

/**
 * Relative Strength Index (RSI)
 */
export function calculateRSI(
  data: CandleData[],
  period: number = 14
): LinePoint[] {
  const result: LinePoint[] = [];
  if (!data || data.length <= period) return result;

  let gains = 0;
  let losses = 0;

  for (let i = 1; i <= period; i++) {
    const diff = data[i].close - data[i - 1].close;
    if (diff >= 0) gains += diff;
    else losses -= diff;
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;

  let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  let rsi = 100 - 100 / (1 + rs);
  result.push({ time: data[period].time, value: Math.round(rsi * 100) / 100 });

  for (let i = period + 1; i < data.length; i++) {
    const diff = data[i].close - data[i - 1].close;
    const currentGain = diff >= 0 ? diff : 0;
    const currentLoss = diff < 0 ? -diff : 0;

    avgGain = (avgGain * (period - 1) + currentGain) / period;
    avgLoss = (avgLoss * (period - 1) + currentLoss) / period;

    rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsi = 100 - 100 / (1 + rs);
    result.push({ time: data[i].time, value: Math.round(rsi * 100) / 100 });
  }

  return result;
}

/**
 * MACD (Moving Average Convergence Divergence)
 */
export function calculateMACD(
  data: CandleData[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9
): MACDResult {
  const result: MACDResult = { macd: [], signal: [], histogram: [] };
  if (!data || data.length < slowPeriod + signalPeriod) return result;

  const fastEMA = calculateEMA(data, fastPeriod);
  const slowEMA = calculateEMA(data, slowPeriod);

  const fastMap = new Map(fastEMA.map(p => [p.time, p.value]));
  const rawMacd: LinePoint[] = [];

  for (const s of slowEMA) {
    const fVal = fastMap.get(s.time);
    if (fVal !== undefined) {
      rawMacd.push({ time: s.time, value: fVal - s.value });
    }
  }

  if (rawMacd.length < signalPeriod) return result;

  const k = 2 / (signalPeriod + 1);
  let signalEma = 0;
  for (let i = 0; i < signalPeriod; i++) {
    signalEma += rawMacd[i].value;
  }
  signalEma /= signalPeriod;

  const signalLine: LinePoint[] = [];
  const macdLine: LinePoint[] = [];
  const histogram: { time: string; value: number; color: string }[] = [];

  signalLine.push({ time: rawMacd[signalPeriod - 1].time, value: Math.round(signalEma * 100) / 100 });
  macdLine.push({ time: rawMacd[signalPeriod - 1].time, value: Math.round(rawMacd[signalPeriod - 1].value * 100) / 100 });
  const hist0 = rawMacd[signalPeriod - 1].value - signalEma;
  histogram.push({
    time: rawMacd[signalPeriod - 1].time,
    value: Math.round(hist0 * 100) / 100,
    color: hist0 >= 0 ? "#26a69a" : "#ef5350",
  });

  for (let i = signalPeriod; i < rawMacd.length; i++) {
    signalEma = rawMacd[i].value * k + signalEma * (1 - k);
    const mVal = rawMacd[i].value;
    const hist = mVal - signalEma;

    macdLine.push({ time: rawMacd[i].time, value: Math.round(mVal * 100) / 100 });
    signalLine.push({ time: rawMacd[i].time, value: Math.round(signalEma * 100) / 100 });
    histogram.push({
      time: rawMacd[i].time,
      value: Math.round(hist * 100) / 100,
      color: hist >= 0 ? "#26a69a" : "#ef5350",
    });
  }

  return { macd: macdLine, signal: signalLine, histogram };
}
