import type { CandleData } from "./technicalIndicators";
import {
  calculateSMA,
  calculateBollingerBands,
  calculateRSI,
  calculateMACD,
} from "./technicalIndicators";

export type StrategyType =
  | "MA_CROSS"
  | "RSI_OVERSOLD"
  | "BOLLINGER_REVERSAL"
  | "BREAKOUT_HIGH"
  | "SURGE_PULLBACK"
  | "VOLUME_SURGE"
  | "MA_ALIGN_BULL"
  | "MACD_GOLDEN_CROSS"
  | "VOLATILITY_BREAKOUT"
  | "DISPARITY_OVERSOLD"
  | "HIGH_52W_BREAKOUT"
  // Lecture Patterns
  | "LECTURE_INVERTED_HAMMER"
  | "LECTURE_HAMMER"
  | "LECTURE_BULLISH_ENGULFING"
  | "LECTURE_MORNING_STAR"
  | "LECTURE_THREE_SOLDIERS"
  | "LECTURE_PIERCING_LINE"
  | "LECTURE_BEARISH_ENGULFING";

export interface BacktestParams {
  strategy: StrategyType;
  takeProfitPct?: number; // e.g. 10 for 10%
  stopLossPct?: number; // e.g. 5 for 5%
  maxHoldDays?: number; // e.g. 20 days
  feeRatePct?: number; // default 0.2%
  rsiPeriod?: number;
  rsiBuy?: number;
  rsiSell?: number;
  maFast?: number;
  maSlow?: number;
  bbPeriod?: number;
  breakoutDays?: number;
}

export interface Trade {
  entryDate: string;
  entryPrice: number;
  exitDate: string;
  exitPrice: number;
  returnPct: number;
  holdDays: number;
  exitReason: "TP" | "SL" | "SIGNAL" | "MAX_DAYS" | "END_OF_DATA";
}

export interface ChartMarker {
  time: string;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
}

export interface BacktestResult {
  strategyName: string;
  totalReturnPct: number;
  buyAndHoldReturnPct: number;
  winRatePct: number;
  winCount: number;
  lossCount: number;
  totalTrades: number;
  profitFactor: number;
  avgReturnPct: number;
  maxDrawdownPct: number;
  trades: Trade[];
  markers: ChartMarker[];
}

export interface LecturePatternPoint {
  time: string;
  index: number;
  price: number;
  patternKey: StrategyType;
  title: string;
  badgeText: string;
  description: string;
  volumeRatio: number;
  marker: ChartMarker;
}

export function detectLecturePatternPoints(
  data: CandleData[],
  pattern: StrategyType
): LecturePatternPoint[] {
  if (!data || data.length < 20) return [];

  const ma20 = calculateSMA(data, 20);
  const ma60 = calculateSMA(data, 60);
  const ma20Map = new Map(ma20.map(m => [m.time, m.value]));
  const ma60Map = new Map(ma60.map(m => [m.time, m.value]));

  const points: LecturePatternPoint[] = [];

  for (let i = 20; i < data.length; i++) {
    const currBar = data[i];
    const prevBar = data[i - 1];
    const date = currBar.time;

    const avgVol20 = data.slice(i - 20, i).reduce((s, c) => s + c.volume, 0) / 20;
    const volRatio = avgVol20 > 0 ? currBar.volume / avgVol20 : 1;
    const volPrevRatio = prevBar.volume > 0 ? currBar.volume / prevBar.volume : 1;

    const body = Math.abs(currBar.close - currBar.open);
    const upperShadow = currBar.high - Math.max(currBar.open, currBar.close);
    const lowerShadow = Math.min(currBar.open, currBar.close) - currBar.low;

    const m20 = ma20Map.get(date);
    const m60 = ma60Map.get(date);

    if (pattern === "LECTURE_INVERTED_HAMMER") {
      const isBottom = (m60 && currBar.close < m60) || (m20 && currBar.close < m20);
      const isHammerShape = upperShadow >= body * 1.6 && lowerShadow <= body * 0.8 && upperShadow > 0;
      const isVolSurge = volRatio >= 1.8 || volPrevRatio >= 1.8;

      if (isBottom && isHammerShape && isVolSurge) {
        points.push({
          time: date,
          index: i,
          price: currBar.close,
          patternKey: pattern,
          title: "바닥권 역망치 매집봉 (1-1강)",
          badgeText: "🔨 역망치 매집",
          description: `위꼬리 ${Math.round((upperShadow / (body || 1)) * 10) / 10}배, 거래량 20MA 대비 ${volRatio.toFixed(1)}배 폭증`,
          volumeRatio: Math.round(volRatio * 10) / 10,
          marker: {
            time: date,
            position: "aboveBar",
            color: "#ef4444",
            shape: "arrowDown",
            text: "🔨 [역망치 매집]",
          },
        });
      }
    } else if (pattern === "LECTURE_HAMMER") {
      const isBottom = m20 ? currBar.close < m20 : true;
      const isHammerShape = lowerShadow >= body * 1.8 && upperShadow <= body * 0.6 && lowerShadow > 0;
      const isVolGood = volRatio >= 1.2 || volPrevRatio >= 1.4;

      if (isBottom && isHammerShape && isVolGood) {
        points.push({
          time: date,
          index: i,
          price: currBar.close,
          patternKey: pattern,
          title: "바닥권 망치형 반등 (1-1강)",
          badgeText: "🪓 바닥 망치",
          description: `아래꼬리 ${Math.round((lowerShadow / (body || 1)) * 10) / 10}배, 저가 지지 반등 시그널`,
          volumeRatio: Math.round(volRatio * 10) / 10,
          marker: {
            time: date,
            position: "belowBar",
            color: "#e91e63",
            shape: "arrowUp",
            text: "🪓 [바닥 망치]",
          },
        });
      }
    } else if (pattern === "LECTURE_BULLISH_ENGULFING") {
      const isPrevBear = prevBar.close < prevBar.open;
      const isCurrBull = currBar.close > currBar.open;
      const prevBody = prevBar.open - prevBar.close;
      const currBody = currBar.close - currBar.open;

      const nearMA20 = m20
        ? Math.abs(currBar.low - m20) / m20 <= 0.04 ||
          Math.abs(currBar.close - m20) / m20 <= 0.04 ||
          (prevBar.low <= m20 && currBar.close >= m20 * 0.96)
        : true;

      const isEngulf =
        currBar.open <= prevBar.close * 1.008 &&
        currBar.close >= prevBar.open * 0.992 &&
        currBody >= prevBody * 1.1;

      const isVolSurge = volRatio >= 1.4 || volPrevRatio >= 1.6;

      if (nearMA20 && isPrevBear && isCurrBull && isEngulf && isVolSurge) {
        points.push({
          time: date,
          index: i,
          price: currBar.close,
          patternKey: pattern,
          title: "20일선 눌림목 상승장악형 (1-2강)",
          badgeText: "🟢 상승장악형",
          description: `20일선 지지 + 전일 음봉 장악 + 거래량 ${volPrevRatio.toFixed(1)}배 폭증`,
          volumeRatio: Math.round(volRatio * 10) / 10,
          marker: {
            time: date,
            position: "belowBar",
            color: "#10b981",
            shape: "arrowUp",
            text: "🟢 [상승장악형]",
          },
        });
      }
    } else if (pattern === "LECTURE_MORNING_STAR") {
      if (i >= 2) {
        const p2 = data[i - 2];
        const p1 = data[i - 1];
        const p2Body = p2.open - p2.close;
        const p1Body = Math.abs(p1.close - p1.open);

        const isP2Bear = p2.close < p2.open && p2Body > 0;
        const isP1Small = p1Body <= p2Body * 0.6;
        const isCBull =
          currBar.close > currBar.open &&
          currBar.close >= (p2.open + p2.close) / 2;

        if (isP2Bear && isP1Small && isCBull) {
          points.push({
            time: date,
            index: i,
            price: currBar.close,
            patternKey: pattern,
            title: "바닥권 샛별형 3봉 반전 (1-2강)",
            badgeText: "⭐ 샛별형",
            description: `음봉 ➡️ 갭 도지/단봉 ➡️ 50%+ 회복 양봉 3봉 완성`,
            volumeRatio: Math.round(volRatio * 10) / 10,
            marker: {
              time: date,
              position: "belowBar",
              color: "#f59e0b",
              shape: "arrowUp",
              text: "⭐ [샛별형]",
            },
          });
        }
      }
    } else if (pattern === "LECTURE_THREE_SOLDIERS") {
      if (i >= 2) {
        const p2 = data[i - 2];
        const p1 = data[i - 1];
        const isBull3 =
          p2.close > p2.open && p1.close > p1.open && currBar.close > currBar.open;
        const isRising =
          p1.close > p2.close &&
          currBar.close > p1.close &&
          currBar.high > p1.high;

        if (isBull3 && isRising) {
          points.push({
            time: date,
            index: i,
            price: currBar.close,
            patternKey: pattern,
            title: "적삼병 연속 양봉 추세 돌파 (1-2강)",
            badgeText: "📈 적삼병",
            description: `3연속 양봉 계단식 우상향 파동 전개`,
            volumeRatio: Math.round(volRatio * 10) / 10,
            marker: {
              time: date,
              position: "belowBar",
              color: "#8b5cf6",
              shape: "arrowUp",
              text: "📈 [적삼병]",
            },
          });
        }
      }
    } else if (pattern === "LECTURE_PIERCING_LINE") {
      const isPrevBear = prevBar.close < prevBar.open;
      const isCurrBull = currBar.close > currBar.open;
      const prevMid = (prevBar.open + prevBar.close) / 2;
      const isGapDown = currBar.open <= prevBar.close * 1.005;
      const isPierce = currBar.close > prevMid && currBar.close < prevBar.open;

      if (isPrevBear && isCurrBull && isGapDown && isPierce) {
        points.push({
          time: date,
          index: i,
          price: currBar.close,
          patternKey: pattern,
          title: "관통형 50%+ 되돌림 반등 (1-2강)",
          badgeText: "⚡ 관통형",
          description: `전일 음봉 몸통 50% 이상을 뚫고 올라온 강한 매수세`,
          volumeRatio: Math.round(volRatio * 10) / 10,
          marker: {
            time: date,
            position: "belowBar",
            color: "#06b6d4",
            shape: "arrowUp",
            text: "⚡ [관통형]",
          },
        });
      }
    } else if (pattern === "LECTURE_BEARISH_ENGULFING") {
      const isPrevBull = prevBar.close > prevBar.open;
      const isCurrBear = currBar.close < currBar.open;
      const isEngulf =
        currBar.open >= prevBar.close * 0.995 &&
        currBar.close <= prevBar.open * 1.005;
      const isHighLevel = m20 ? currBar.close >= m20 : true;

      if (isHighLevel && isPrevBull && isCurrBear && isEngulf) {
        points.push({
          time: date,
          index: i,
          price: currBar.close,
          patternKey: pattern,
          title: "고점 하락장악형 매도 경고 (1-2강)",
          badgeText: "⚠️ 하락장악형",
          description: `전일 양봉을 삼키는 장대음봉 출현 - 즉각 리스크 관리 필요`,
          volumeRatio: Math.round(volRatio * 10) / 10,
          marker: {
            time: date,
            position: "aboveBar",
            color: "#3b82f6",
            shape: "arrowDown",
            text: "⚠️ [하락장악형]",
          },
        });
      }
    }
  }

  return points;
}

export function runBacktest(
  data: CandleData[],
  params: BacktestParams
): BacktestResult {
  if (!data || data.length < 20) {
    return {
      strategyName: params.strategy,
      totalReturnPct: 0,
      buyAndHoldReturnPct: 0,
      winRatePct: 0,
      winCount: 0,
      lossCount: 0,
      totalTrades: 0,
      profitFactor: 0,
      avgReturnPct: 0,
      maxDrawdownPct: 0,
      trades: [],
      markers: [],
    };
  }

  const ma5 = calculateSMA(data, params.maFast ?? 5);
  const ma20 = calculateSMA(data, params.maSlow ?? 20);
  const ma60 = calculateSMA(data, 60);
  const rsi = calculateRSI(data, params.rsiPeriod ?? 14);
  const bb = calculateBollingerBands(data, params.bbPeriod ?? 20, 2);
  const macd = calculateMACD(data, 12, 26, 9);

  const ma5Map = new Map(ma5.map(p => [p.time, p.value]));
  const ma20Map = new Map(ma20.map(p => [p.time, p.value]));
  const ma60Map = new Map(ma60.map(p => [p.time, p.value]));
  const rsiMap = new Map(rsi.map(p => [p.time, p.value]));
  const bbLowerMap = new Map(bb.lower.map(p => [p.time, p.value]));
  const bbUpperMap = new Map(bb.upper.map(p => [p.time, p.value]));
  const macdMap = new Map(macd.macd.map(p => [p.time, p.value]));
  const macdSignalMap = new Map(macd.signal.map(p => [p.time, p.value]));

  const trades: Trade[] = [];
  const markers: ChartMarker[] = [];

  const feeRate = (params.feeRatePct ?? 0.2) / 100;
  const takeProfit = params.takeProfitPct ? params.takeProfitPct / 100 : null;
  const stopLoss = params.stopLossPct ? params.stopLossPct / 100 : null;
  const maxHoldDays = params.maxHoldDays ?? 20;
  const rsiBuyThreshold = params.rsiBuy ?? 30;
  const rsiSellThreshold = params.rsiSell ?? 70;
  const breakoutPeriod = params.breakoutDays ?? 20;

  let inPosition = false;
  let entryPrice = 0;
  let entryDate = "";
  let entryIndex = -1;

  for (let i = 20; i < data.length; i++) {
    const currBar = data[i];
    const prevBar = data[i - 1];
    const date = currBar.time;

    if (inPosition) {
      const holdDays = i - entryIndex;
      const rawReturn = (currBar.close - entryPrice) / entryPrice;
      let shouldExit = false;
      let exitReason: Trade["exitReason"] = "SIGNAL";
      let exitPrice = currBar.close;

      if (stopLoss !== null && rawReturn <= -stopLoss) {
        shouldExit = true;
        exitReason = "SL";
        exitPrice = Math.round(entryPrice * (1 - stopLoss));
      } else if (takeProfit !== null && rawReturn >= takeProfit) {
        shouldExit = true;
        exitReason = "TP";
        exitPrice = Math.round(entryPrice * (1 + takeProfit));
      } else if (maxHoldDays !== null && holdDays >= maxHoldDays) {
        shouldExit = true;
        exitReason = "MAX_DAYS";
      } else {
        if (params.strategy === "MA_CROSS") {
          const prev5 = ma5Map.get(prevBar.time);
          const prev20 = ma20Map.get(prevBar.time);
          const curr5 = ma5Map.get(currBar.time);
          const curr20 = ma20Map.get(currBar.time);
          if (prev5 && prev20 && curr5 && curr20 && prev5 >= prev20 && curr5 < curr20) {
            shouldExit = true;
            exitReason = "SIGNAL";
          }
        } else if (params.strategy === "RSI_OVERSOLD") {
          const currRsi = rsiMap.get(currBar.time);
          if (currRsi !== undefined && currRsi >= rsiSellThreshold) {
            shouldExit = true;
            exitReason = "SIGNAL";
          }
        } else if (params.strategy === "BOLLINGER_REVERSAL") {
          const upper = bbUpperMap.get(currBar.time);
          if (upper !== undefined && currBar.close >= upper) {
            shouldExit = true;
            exitReason = "SIGNAL";
          }
        } else if (params.strategy === "BREAKOUT_HIGH" || params.strategy === "HIGH_52W_BREAKOUT") {
          const days = params.strategy === "HIGH_52W_BREAKOUT" ? 20 : breakoutPeriod;
          if (i >= days) {
            const minLow = Math.min(...data.slice(i - days, i).map(c => c.low));
            if (currBar.close < minLow) {
              shouldExit = true;
              exitReason = "SIGNAL";
            }
          }
        } else if (params.strategy === "MACD_GOLDEN_CROSS") {
          const prevM = macdMap.get(prevBar.time);
          const prevS = macdSignalMap.get(prevBar.time);
          const currM = macdMap.get(currBar.time);
          const currS = macdSignalMap.get(currBar.time);
          if (prevM !== undefined && prevS !== undefined && currM !== undefined && currS !== undefined) {
            if (prevM >= prevS && currM < currS) {
              shouldExit = true;
              exitReason = "SIGNAL";
            }
          }
        } else if (params.strategy === "MA_ALIGN_BULL") {
          const curr5 = ma5Map.get(currBar.time);
          const curr20 = ma20Map.get(currBar.time);
          if (curr5 && curr20 && curr5 < curr20) {
            shouldExit = true;
            exitReason = "SIGNAL";
          }
        } else if (
          params.strategy === "SURGE_PULLBACK" ||
          params.strategy === "VOLUME_SURGE" ||
          params.strategy === "VOLATILITY_BREAKOUT" ||
          params.strategy === "DISPARITY_OVERSOLD" ||
          params.strategy.startsWith("LECTURE_")
        ) {
          if (holdDays >= (maxHoldDays ?? 10)) {
            shouldExit = true;
            exitReason = "SIGNAL";
          }
        }
      }

      if (shouldExit || i === data.length - 1) {
        if (i === data.length - 1 && !shouldExit) {
          exitReason = "END_OF_DATA";
        }
        const finalReturn =
          ((exitPrice - entryPrice) / entryPrice - feeRate) * 100;
        trades.push({
          entryDate,
          entryPrice,
          exitDate: date,
          exitPrice,
          returnPct: Math.round(finalReturn * 100) / 100,
          holdDays,
          exitReason,
        });

        markers.push({
          time: date,
          position: "aboveBar",
          color: finalReturn >= 0 ? "#ef5350" : "#2196f3",
          shape: "arrowDown",
          text: `매도(${exitReason}) ${finalReturn >= 0 ? "+" : ""}${finalReturn.toFixed(1)}%`,
        });

        inPosition = false;
        entryIndex = -1;
      }
    } else {
      let shouldEnter = false;

      if (params.strategy === "MA_CROSS") {
        const prev5 = ma5Map.get(prevBar.time);
        const prev20 = ma20Map.get(prevBar.time);
        const curr5 = ma5Map.get(currBar.time);
        const curr20 = ma20Map.get(currBar.time);
        if (prev5 && prev20 && curr5 && curr20 && prev5 <= prev20 && curr5 > curr20) {
          shouldEnter = true;
        }
      } else if (params.strategy === "RSI_OVERSOLD") {
        const prevRsi = rsiMap.get(prevBar.time);
        const currRsi = rsiMap.get(currBar.time);
        if (prevRsi !== undefined && currRsi !== undefined && prevRsi < rsiBuyThreshold && currRsi >= rsiBuyThreshold) {
          shouldEnter = true;
        }
      } else if (params.strategy === "BOLLINGER_REVERSAL") {
        const lower = bbLowerMap.get(prevBar.time);
        if (lower !== undefined && prevBar.close <= lower && currBar.close > lower) {
          shouldEnter = true;
        }
      } else if (params.strategy === "BREAKOUT_HIGH") {
        if (i >= breakoutPeriod) {
          const maxHigh = Math.max(...data.slice(i - breakoutPeriod, i).map(c => c.high));
          if (currBar.close > maxHigh) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "SURGE_PULLBACK") {
        if (i >= 4) {
          const recentSurge = data.slice(i - 3, i).some(c => (c.close - c.open) / c.open >= 0.1);
          const currFast = ma5Map.get(currBar.time);
          if (recentSurge && currFast && currBar.close >= currFast * 0.98) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "VOLUME_SURGE") {
        if (i >= 20) {
          const avgVol = data.slice(i - 20, i).reduce((sum, c) => sum + c.volume, 0) / 20;
          const isSurge = currBar.volume >= avgVol * 3.0;
          const isBull = (currBar.close - currBar.open) / currBar.open >= 0.05;
          if (isSurge && isBull) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "MA_ALIGN_BULL") {
        const prev5 = ma5Map.get(prevBar.time);
        const prev20 = ma20Map.get(prevBar.time);
        const prev60 = ma60Map.get(prevBar.time);
        const curr5 = ma5Map.get(currBar.time);
        const curr20 = ma20Map.get(currBar.time);
        const curr60 = ma60Map.get(currBar.time);
        if (curr5 && curr20 && curr60 && prev5 && prev20 && prev60) {
          const wasAligned = prev5 > prev20 && prev20 > prev60;
          const isAligned = curr5 > curr20 && curr20 > curr60;
          if (!wasAligned && isAligned) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "MACD_GOLDEN_CROSS") {
        const prevM = macdMap.get(prevBar.time);
        const prevS = macdSignalMap.get(prevBar.time);
        const currM = macdMap.get(currBar.time);
        const currS = macdSignalMap.get(currBar.time);
        if (prevM !== undefined && prevS !== undefined && currM !== undefined && currS !== undefined) {
          if (prevM <= prevS && currM > currS && currM < 0) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "VOLATILITY_BREAKOUT") {
        const range = prevBar.high - prevBar.low;
        const target = currBar.open + range * 0.5;
        if (currBar.high >= target && currBar.close >= target) {
          shouldEnter = true;
        }
      } else if (params.strategy === "DISPARITY_OVERSOLD") {
        const ma20Val = ma20Map.get(currBar.time);
        if (ma20Val) {
          const disparity = (currBar.close / ma20Val) * 100;
          if (disparity <= 90 && currBar.close > currBar.open) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "HIGH_52W_BREAKOUT") {
        if (i >= 250) {
          const max52w = Math.max(...data.slice(i - 250, i).map(c => c.high));
          if (currBar.close > max52w) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_INVERTED_HAMMER") {
        if (i >= 20) {
          const m20 = ma20Map.get(currBar.time);
          const m60 = ma60Map.get(currBar.time);
          const avgVol = data.slice(i - 20, i).reduce((s, c) => s + c.volume, 0) / 20;
          const body = Math.abs(currBar.close - currBar.open);
          const upperShadow = currBar.high - Math.max(currBar.open, currBar.close);
          const lowerShadow = Math.min(currBar.open, currBar.close) - currBar.low;
          const isBottom = (m60 && currBar.close < m60) || (m20 && currBar.close < m20);
          const isHammerShape = upperShadow >= body * 1.6 && lowerShadow <= body * 0.8 && upperShadow > 0;
          const isVolSurge = currBar.volume >= avgVol * 1.8 || (prevBar.volume > 0 && currBar.volume >= prevBar.volume * 1.8);
          if (isBottom && isHammerShape && isVolSurge) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_HAMMER") {
        if (i >= 20) {
          const m20 = ma20Map.get(currBar.time);
          const avgVol = data.slice(i - 20, i).reduce((s, c) => s + c.volume, 0) / 20;
          const body = Math.abs(currBar.close - currBar.open);
          const upperShadow = currBar.high - Math.max(currBar.open, currBar.close);
          const lowerShadow = Math.min(currBar.open, currBar.close) - currBar.low;
          const isBottom = m20 ? currBar.close < m20 : true;
          const isHammerShape = lowerShadow >= body * 1.8 && upperShadow <= body * 0.6 && lowerShadow > 0;
          const isVolGood = currBar.volume >= avgVol * 1.2 || (prevBar.volume > 0 && currBar.volume >= prevBar.volume * 1.4);
          if (isBottom && isHammerShape && isVolGood) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_BULLISH_ENGULFING") {
        if (i >= 20) {
          const m20 = ma20Map.get(currBar.time);
          const avgVol = data.slice(i - 20, i).reduce((s, c) => s + c.volume, 0) / 20;
          const isPrevBear = prevBar.close < prevBar.open;
          const isCurrBull = currBar.close > currBar.open;
          const prevBody = prevBar.open - prevBar.close;
          const currBody = currBar.close - currBar.open;
          const nearMA20 = m20
            ? Math.abs(currBar.low - m20) / m20 <= 0.04 ||
              Math.abs(currBar.close - m20) / m20 <= 0.04 ||
              (prevBar.low <= m20 && currBar.close >= m20 * 0.96)
            : true;
          const isEngulf =
            currBar.open <= prevBar.close * 1.008 &&
            currBar.close >= prevBar.open * 0.992 &&
            currBody >= prevBody * 1.1;
          const isVolSurge = currBar.volume >= avgVol * 1.4 || (prevBar.volume > 0 && currBar.volume >= prevBar.volume * 1.6);
          if (nearMA20 && isPrevBear && isCurrBull && isEngulf && isVolSurge) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_MORNING_STAR") {
        if (i >= 2) {
          const p2 = data[i - 2];
          const p1 = data[i - 1];
          const p2Body = p2.open - p2.close;
          const p1Body = Math.abs(p1.close - p1.open);
          const isP2Bear = p2.close < p2.open && p2Body > 0;
          const isP1Small = p1Body <= p2Body * 0.6;
          const isCBull = currBar.close > currBar.open && currBar.close >= (p2.open + p2.close) / 2;
          if (isP2Bear && isP1Small && isCBull) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_THREE_SOLDIERS") {
        if (i >= 2) {
          const p2 = data[i - 2];
          const p1 = data[i - 1];
          const isBull3 = p2.close > p2.open && p1.close > p1.open && currBar.close > currBar.open;
          const isRising = p1.close > p2.close && currBar.close > p1.close && currBar.high > p1.high;
          if (isBull3 && isRising) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "LECTURE_PIERCING_LINE") {
        const isPrevBear = prevBar.close < prevBar.open;
        const isCurrBull = currBar.close > currBar.open;
        const prevMid = (prevBar.open + prevBar.close) / 2;
        const isGapDown = currBar.open <= prevBar.close * 1.005;
        const isPierce = currBar.close > prevMid && currBar.close < prevBar.open;
        if (isPrevBear && isCurrBull && isGapDown && isPierce) {
          shouldEnter = true;
        }
      } else if (params.strategy === "LECTURE_BEARISH_ENGULFING") {
        const isPrevBull = prevBar.close > prevBar.open;
        const isCurrBear = currBar.close < currBar.open;
        const isEngulf = currBar.open >= prevBar.close * 0.995 && currBar.close <= prevBar.open * 1.005;
        if (isPrevBull && isCurrBear && isEngulf) {
          shouldEnter = true;
        }
      }

      if (shouldEnter) {
        inPosition = true;
        entryIndex = i;
        entryPrice = currBar.close;
        entryDate = date;

        markers.push({
          time: date,
          position: "belowBar",
          color: "#e91e63",
          shape: "arrowUp",
          text: "매수(진입)",
        });
      }
    }
  }

  const winTrades = trades.filter(t => t.returnPct > 0);
  const lossTrades = trades.filter(t => t.returnPct <= 0);
  const winCount = winTrades.length;
  const lossCount = lossTrades.length;
  const totalTrades = trades.length;
  const winRatePct =
    totalTrades > 0
      ? Math.round((winCount / totalTrades) * 10000) / 100
      : 0;

  const totalGains = winTrades.reduce((acc, t) => acc + t.returnPct, 0);
  const totalLosses = Math.abs(
    lossTrades.reduce((acc, t) => acc + t.returnPct, 0)
  );
  const profitFactor =
    totalLosses > 0
      ? Math.round((totalGains / totalLosses) * 100) / 100
      : totalGains > 0
        ? 999
        : 0;

  let capital = 1.0;
  let peak = 1.0;
  let maxDrawdown = 0;

  for (const t of trades) {
    capital *= 1 + t.returnPct / 100;
    if (capital > peak) peak = capital;
    const dd = (peak - capital) / peak;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  const totalReturnPct = Math.round((capital - 1) * 10000) / 100;
  const firstPrice = data[0].close;
  const lastPrice = data[data.length - 1].close;
  const buyAndHoldReturnPct =
    Math.round(((lastPrice - firstPrice) / firstPrice) * 10000) / 100;
  const avgReturnPct =
    totalTrades > 0
      ? Math.round(
          (trades.reduce((acc, t) => acc + t.returnPct, 0) / totalTrades) * 100
        ) / 100
      : 0;

  return {
    strategyName: params.strategy,
    totalReturnPct,
    buyAndHoldReturnPct,
    winRatePct,
    winCount,
    lossCount,
    totalTrades,
    profitFactor,
    avgReturnPct,
    maxDrawdownPct: Math.round(maxDrawdown * 10000) / 100,
    trades,
    markers,
  };
}
