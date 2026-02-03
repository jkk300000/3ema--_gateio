"""
Gate.io 선물 실시간 가격 스트림 (WebSocket).
브레이크이븐 등에서 정확한 현재가 사용을 위해 틱 데이터 수신.
"""
import json
import logging
import threading
import time
from typing import Optional
import websocket

import config

logger = logging.getLogger(__name__)


class PriceStream:
    """Gate.io 선물 WebSocket 틱 가격 스트림."""
    
    def __init__(self, contract: str = None, settle: str = None):
        self.contract = contract or config.CONTRACT
        self.settle = settle or config.SETTLE
        self._last_price: Optional[float] = None
        self._lock = threading.Lock()
        self._ws = None
        self._running = False
        self._thread = None
        # WebSocket URL: wss://fx-ws.gateio.ws/v4/ws/usdt
        self._ws_url = f"wss://fx-ws.gateio.ws/v4/ws/{self.settle}"
        
    def get_last_price(self) -> Optional[float]:
        """현재 마지막 틱 가격 (스레드 안전)."""
        with self._lock:
            return self._last_price
    
    def _on_message(self, ws, message):
        """WebSocket 메시지 수신 핸들러."""
        try:
            data = json.loads(message)
            # Gate.io Futures WebSocket: tickers 채널 응답 형식
            # {"time": ts, "channel": "futures.tickers", "event": "update", "result": [{"contract": "BTC_USDT", "last": "76500", ...}]}
            channel = data.get("channel")
            if channel == "futures.tickers":
                result = data.get("result")
                if isinstance(result, list) and result:
                    for ticker in result:
                        if isinstance(ticker, dict) and ticker.get("contract") == self.contract:
                            last_str = ticker.get("last")
                            if last_str:
                                try:
                                    price = float(last_str)
                                    with self._lock:
                                        old_price = self._last_price
                                        self._last_price = price
                                        if old_price != price:
                                            logger.debug("WebSocket 가격 업데이트: %s = %.2f", self.contract, price)
                                except (ValueError, TypeError) as e:
                                    logger.debug("가격 파싱 오류: last_str=%s: %s", last_str, e)
        except Exception as e:
            logger.debug("WebSocket 메시지 파싱 오류: %s", e)
    
    def _on_error(self, ws, error):
        """WebSocket 오류 핸들러."""
        logger.warning("WebSocket 오류: %s", error)
    
    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 핸들러."""
        logger.info("WebSocket 연결 종료: code=%s msg=%s", close_status_code, close_msg)
        if self._running:
            # 재연결 시도
            logger.info("WebSocket 재연결 시도...")
            time.sleep(2)
            self.start()
    
    def _on_open(self, ws):
        """WebSocket 연결 시작 핸들러."""
        logger.info("WebSocket 연결 성공: contract=%s", self.contract)
        # tickers 채널 구독
        subscribe_msg = {
            "time": int(time.time()),
            "channel": f"futures.tickers",
            "event": "subscribe",
            "payload": [self.contract]
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info("WebSocket tickers 채널 구독: %s", self.contract)
    
    def _run_websocket(self):
        """WebSocket 연결 실행 (별도 스레드)."""
        while self._running:
            try:
                # Gate.io WebSocket 헤더: 소수점 지원
                custom_headers = {"X-Gate-Size-Decimal": "1"}
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                    header=custom_headers
                )
                self._ws.run_forever()
            except Exception as e:
                logger.exception("WebSocket 실행 오류: %s", e)
                if self._running:
                    time.sleep(5)  # 재연결 전 대기
    
    def start(self):
        """가격 스트림 시작."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_websocket, daemon=True)
        self._thread.start()
        logger.info("가격 스트림 시작: contract=%s", self.contract)
    
    def stop(self):
        """가격 스트림 중지."""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("가격 스트림 중지")
