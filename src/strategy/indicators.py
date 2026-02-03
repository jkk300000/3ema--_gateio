"""
지표: EMA20, EMA50, EMA100, ATR(14).
"""
import math
from typing import List, Dict, Any

import config


def compute_ema(prices: List[float], length: int) -> List[float]:
    """EMA. prices는 오래된 것부터."""
    if not prices or length < 1:
        return []
    k = 2.0 / (length + 1)
    out = []
    ema = prices[0]
    for i, p in enumerate(prices):
        if i == 0:
            ema = p
        else:
            ema = p * k + ema * (1 - k)
        out.append(ema)
    return out


def compute_atr(high: List[float], low: List[float], close: List[float], period: int) -> List[float]:
    """ATR(period). high/low/close는 오래된 것부터, 같은 길이."""
    n = len(close)
    if n < 2 or period < 1:
        return [0.0] * n
    tr_list = [high[0] - low[0]]
    for i in range(1, n):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_list.append(tr)
    # ATR = RMA(TR, period) (첫 ATR은 TR의 period개 평균, 이후 RMA)
    atr = []
    for i in range(n):
        if i < period - 1:
            atr.append(sum(tr_list[: i + 1]) / (i + 1))
        elif i == period - 1:
            atr.append(sum(tr_list[:period]) / period)
        else:
            prev_atr = atr[-1]
            new_atr = (prev_atr * (period - 1) + tr_list[i]) / period
            atr.append(new_atr)
    return atr


def add_indicators(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    candles: [{"t", "o", "h", "l", "c", "v"}, ...] 시간순.
    각 행에 ema20, ema50, ema100, atr 추가.
    """
    if not candles:
        return []
    close = [c["c"] for c in candles]
    high = [c["h"] for c in candles]
    low = [c["l"] for c in candles]

    ema20 = compute_ema(close, config.EMA_20_LEN)
    ema50 = compute_ema(close, config.EMA_50_LEN)
    ema100 = compute_ema(close, config.EMA_100_LEN)
    atr = compute_atr(high, low, close, config.ATR_PERIOD)

    out = []
    for i, c in enumerate(candles):
        row = dict(c)
        row["ema20"] = ema20[i] if i < len(ema20) else None
        row["ema50"] = ema50[i] if i < len(ema50) else None
        row["ema100"] = ema100[i] if i < len(ema100) else None
        row["atr"] = atr[i] if i < len(atr) else None
        out.append(row)
    return out
