"""
EMA 캔들패턴 전략 실행 진입점.
Gate.io BTCUSDT 무기한 선물, 30분봉 기준 진입/청산.
"""
import logging
import sys

# 프로젝트 루트를 path에 추가
sys.path.insert(0, ".")

import config
from src.engine import TradingEngine
from src.api.gate_client import GateFuturesClient
from src.api.price_stream import PriceStream

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # urllib3 Retry 경고(RemoteDisconnected 등) 억제 — 캔들 조회는 gate_client에서 재시도·로그함
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logger.info("EMA 캔들패턴 전략 시작 (Gate.io BTCUSDT 30분봉)")
    if getattr(config, "TEST_MODE", False) and getattr(config, "TEST_ENTRY_OVERRIDE", False):
        logger.warning("테스트 모드: 진입 조건 무시, %s 방향 최소 수량(1계약) 진입", getattr(config, "TEST_ENTRY_SIDE", "long"))
    # WebSocket 가격 스트림 시작 (브레이크이븐용 실시간 가격)
    price_stream = PriceStream()
    price_stream.start()
    # GateFuturesClient에 가격 스트림 연결
    client = GateFuturesClient(price_stream=price_stream)
    engine = TradingEngine(client=client)
    try:
        engine.run_loop(interval_sec=60.0)
    finally:
        price_stream.stop()


if __name__ == "__main__":
    main()
