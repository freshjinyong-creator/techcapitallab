import type { CandleData } from "./technicalIndicators";
import {
  calculateSMA,
  calculateRSI,
  calculateBollingerBands,
} from "./technicalIndicators";

export type StrategyType =
  | "MA_CROSS"
  | "RSI_OVERSOLD"
  | "BOLLINGER_REVERSAL"
  | "BREAKOUT_HIGH"
  | "SURGE_PULLBACK";

export interface BacktestParams {
  strategy: StrategyType;
  stopLossPct?: number;
  takeProfitPct?: number;
  maxHoldDays?: number;
  feePct?: number;
  maFast?: number;
  maSlow?: number;
  rsiPeriod?: number;
  rsiBuy?: number;
  rsiSell?: number;
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

export function runBacktest(
  data: CandleData[],
  params: BacktestParams
): BacktestResult {
  const emptyResult: BacktestResult = {
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

  if (!data || data.length < 30) return emptyResult;

  const feeRate = (params.feePct ?? 0.2) / 100;
  const stopLoss = params.stopLossPct ? params.stopLossPct / 100 : null;
  const takeProfit = params.takeProfitPct ? params.takeProfitPct / 100 : null;
  const maxHoldDays = params.maxHoldDays ?? null;

  const maFastPeriod = params.maFast ?? 5;
  const maSlowPeriod = params.maSlow ?? 20;
  const maFast = calculateSMA(data, maFastPeriod);
  const maSlow = calculateSMA(data, maSlowPeriod);
  const rsi = calculateRSI(data, params.rsiPeriod ?? 14);
  const bb = calculateBollingerBands(data, params.bbPeriod ?? 20, 2);

  const maFastMap = new Map(maFast.map(p => [p.time, p.value]));
  const maSlowMap = new Map(maSlow.map(p => [p.time, p.value]));
  const rsiMap = new Map(rsi.map(p => [p.time, p.value]));
  const bbLowerMap = new Map(bb.lower.map(p => [p.time, p.value]));
  const bbUpperMap = new Map(bb.upper.map(p => [p.time, p.value]));

  const trades: Trade[] = [];
  const markers: ChartMarker[] = [];

  let inPosition = false;
  let entryIndex = -1;
  let entryPrice = 0;
  let entryDate = "";

  const rsiBuyThreshold = params.rsiBuy ?? 30;
  const rsiSellThreshold = params.rsiSell ?? 70;
  const breakoutPeriod = params.breakoutDays ?? 20;

  for (let i = 1; i < data.length; i++) {
    const prevBar = data[i - 1];
    const currBar = data[i];
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
          const prevFast = maFastMap.get(prevBar.time);
          const prevSlow = maSlowMap.get(prevBar.time);
          const currFast = maFastMap.get(currBar.time);
          const currSlow = maSlowMap.get(currBar.time);
          if (
            prevFast !== undefined &&
            prevSlow !== undefined &&
            currFast !== undefined &&
            currSlow !== undefined &&
            prevFast >= prevSlow &&
            currFast < currSlow
          ) {
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
        } else if (params.strategy === "BREAKOUT_HIGH") {
          if (i >= breakoutPeriod) {
            const lowRange = data
              .slice(i - breakoutPeriod, i)
              .map(c => c.low);
            const minLow = Math.min(...lowRange);
            if (currBar.close < minLow) {
              shouldExit = true;
              exitReason = "SIGNAL";
            }
          }
        } else if (params.strategy === "SURGE_PULLBACK") {
          if (holdDays >= 5) {
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
        const prevFast = maFastMap.get(prevBar.time);
        const prevSlow = maSlowMap.get(prevBar.time);
        const currFast = maFastMap.get(currBar.time);
        const currSlow = maSlowMap.get(currBar.time);
        if (
          prevFast !== undefined &&
          prevSlow !== undefined &&
          currFast !== undefined &&
          currSlow !== undefined &&
          prevFast <= prevSlow &&
          currFast > currSlow
        ) {
          shouldEnter = true;
        }
      } else if (params.strategy === "RSI_OVERSOLD") {
        const prevRsi = rsiMap.get(prevBar.time);
        const currRsi = rsiMap.get(currBar.time);
        if (
          prevRsi !== undefined &&
          currRsi !== undefined &&
          prevRsi < rsiBuyThreshold &&
          currRsi >= rsiBuyThreshold
        ) {
          shouldEnter = true;
        }
      } else if (params.strategy === "BOLLINGER_REVERSAL") {
        const lower = bbLowerMap.get(prevBar.time);
        if (
          lower !== undefined &&
          prevBar.close <= lower &&
          currBar.close > lower
        ) {
          shouldEnter = true;
        }
      } else if (params.strategy === "BREAKOUT_HIGH") {
        if (i >= breakoutPeriod) {
          const highRange = data
            .slice(i - breakoutPeriod, i)
            .map(c => c.high);
          const maxHigh = Math.max(...highRange);
          if (currBar.close > maxHigh) {
            shouldEnter = true;
          }
        }
      } else if (params.strategy === "SURGE_PULLBACK") {
        if (i >= 4) {
          const recentSurge = data
            .slice(i - 3, i)
            .some(c => (c.close - c.open) / c.open >= 0.1);
          const currFast = maFastMap.get(currBar.time);
          if (recentSurge && currFast && currBar.close >= currFast * 0.98) {
            shouldEnter = true;
          }
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
