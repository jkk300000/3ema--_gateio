"""
캔들 패턴(도지/역도지/망치) 및 진입 조건.
- 정배열: ema20 > ema50 > ema100
- 역배열: ema20 < ema50 < ema100
- 롱: 정배열 + 패턴캔들 + (low<=ema50 or low<=ema100) + (close>ema50 or close>ema100)
- 숏: 역배열 + 패턴캔들 + (high>=ema50 or high>=ema100) + (close<ema50 or close<ema100)
"""
from typing import List, Dict, Any, Optional, Tuple

import config


def check_candle_pattern(row: Dict[str, Any]) -> bool:
    """도지/역도지/망치 중 하나면 True."""
    o, h, l, c = row["o"], row["h"], row["l"], row["c"]
    body = abs(c - o)
    range_ = h - l
    range_safe = range_ if range_ > 0 else 1e-6
    body_ratio = body / range_safe
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    body_safe = body if body > 0 else 1e-6

    if body_ratio > config.BODY_RATIO_MAX:
        return False
    # 도지
    if body_ratio <= config.BODY_RATIO_MAX:
        if upper_wick / range_safe >= config.INVERTED_DOJI_UPPER_WICK_MIN:
            return True  # 역도지
        if lower_wick / body_safe >= config.HAMMER_LOWER_MIN and upper_wick / range_safe <= config.UPPER_WICK_MAX:
            return True  # 망치
        return True  # 도지
    return False


def check_entry(
    candles_with_indicators: List[Dict[str, Any]],
    last_bar_index: int,
) -> Tuple[bool, bool]:
    """
    last_bar_index: 마지막으로 완성된 봉(30봉 완성 직후의 그 봉) 인덱스.
    returns: (long_signal, short_signal)
    """
    if last_bar_index < 0 or last_bar_index >= len(candles_with_indicators):
        return False, False
    row = candles_with_indicators[last_bar_index]
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    ema100 = row.get("ema100")
    if ema20 is None or ema50 is None or ema100 is None:
        return False, False

    bullish = ema20 > ema50 and ema50 > ema100
    bearish = ema20 < ema50 and ema50 < ema100
    pattern = check_candle_pattern(row)
    low, high, close, open_price = row["l"], row["h"], row["c"], row["o"]

    long_ok = (
        bullish
        and pattern
        and (low <= ema50 or low <= ema100)
        and (close > ema50 or close > ema100)
        and (open_price > ema50 or open_price > ema100)
    )
    short_ok = (
        bearish
        and pattern
        and (high >= ema50 or high >= ema100)
        and (close < ema50 or close < ema100)
        and (open_price < ema50 or open_price < ema100)
    )
    return long_ok, short_ok


def get_latest_indicators(candles_with_indicators: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """마지막 봉의 지표/가격 (진입가·SL·TP 계산용)."""
    if not candles_with_indicators:
        return None
    return candles_with_indicators[-1]
