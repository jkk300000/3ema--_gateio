"""
진입·청산 메소드만 검증하는 스크립트.
전략 조건 없이 1계약 시장가 진입 → TP/SL 등록 → 포지션·주문 확인.

사용:
  python scripts/test_order_flow.py [long|short]
  (기본: long)

주의: 실제 주문이 나가므로 테스트넷 또는 소액으로 실행하세요.
"""
import logging
import sys

sys.path.insert(0, ".")

import config
from src.api.gate_client import GateFuturesClient
from src.strategy.indicators import add_indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    side = "long"
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("long", "short"):
        side = sys.argv[1].lower()

    client = GateFuturesClient()
    # 캔들 + 지표로 현재가·ATR 사용 (SL/TP 계산용)
    raw = client.get_candles(limit=config.CANDLE_LIMIT)
    candles = add_indicators(raw)
    if not candles:
        logger.error("캔들 조회 실패")
        return
    row = candles[-1]
    close = float(row["c"])
    atr = float(row.get("atr") or 0)
    if atr <= 0:
        logger.error("ATR 없음")
        return

    sl_dist = atr * config.SL_ATR_MULT
    if side == "long":
        sl_price = close - sl_dist
        tp_price = close + sl_dist * config.RR_RATIO
    else:
        sl_price = close + sl_dist
        tp_price = close - sl_dist * config.RR_RATIO

    size_str = str(config.ORDER_SIZE_MIN)
    logger.info("테스트: side=%s size=%s close=%.2f sl=%.2f tp=%.2f", side, size_str, close, sl_price, tp_price)

    # 1) 시장가 진입
    try:
        client.place_market_order(side, size_str, reduce_only=False)
    except Exception as e:
        logger.exception("진입 주문 실패: %s", e)
        return

    # 2) 포지션 확인
    pos = client.get_position()
    if pos:
        logger.info("포지션 확인: side=%s size=%s entry=%.2f", pos["side"], pos["size"], pos["entry_price"])
    else:
        logger.warning("포지션 없음 (체결 대기 중일 수 있음)")

    # 3) TP/SL 등록
    try:
        client.create_stop_loss_order(str(sl_price), size_str, side)
        client.create_take_profit_order(str(tp_price), size_str, side)
    except Exception as e:
        logger.exception("TP/SL 등록 실패: %s", e)
        return

    # 4) 등록된 가격 트리거 주문 목록
    try:
        orders = client.list_price_orders(status="open")
        for o in (orders or []):
            trigger = getattr(o, "trigger", None)
            price = getattr(trigger, "price", "?") if trigger else "?"
            logger.info("가격 트리거 주문: id=%s price=%s", getattr(o, "id", "?"), price)
    except Exception as e:
        logger.warning("주문 목록 조회 실패: %s", e)

    logger.info("--- 완료. 청산은 거래소에서 수동 청산하거나 TP/SL 체결 대기 ---")


if __name__ == "__main__":
    main()
