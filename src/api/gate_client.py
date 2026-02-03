"""
Gate.io Futures API 클라이언트.
- 30분봉 OHLCV 조회 (캐시 가능)
- 포지션 조회
- 시장가 진입 주문
- TP/SL: 가격 트리거 주문(price_orders) 사용
"""
import logging
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.price_stream import PriceStream

import gate_api
from gate_api import (
    FuturesOrder,
    FuturesPriceTriggeredOrder,
    FuturesInitialOrder,
    FuturesPriceTrigger,
    FuturesUpdatePriceTriggeredOrder,
)

import config

logger = logging.getLogger(__name__)


class GateFuturesClient:
    def __init__(
        self,
        key: Optional[str] = None,
        secret: Optional[str] = None,
        base: Optional[str] = None,
        price_stream: Optional["PriceStream"] = None,
    ):
        self.key = key or config.GATE_API_KEY
        self.secret = secret or config.GATE_API_SECRET
        base = base or config.GATE_FUTURES_BASE
        self.config = gate_api.Configuration(host=base, key=self.key, secret=self.secret)
        self._api = None
        self._connection_logged = False
        self._price_stream = price_stream  # WebSocket 가격 스트림 (선택적)

    def _log_connection_once(self):
        """첫 API 호출 시 서버 연결 및 계정 확인 로그 1회 출력."""
        if self._connection_logged:
            return
        try:
            logger.info(
                "Gate.io 연결: base=%s, settle=%s, contract=%s",
                self.config.host,
                self.settle,
                self.contract,
            )
            acc = self.api.list_futures_accounts(self.settle)
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            # v4.106.3: total=classic 전용, Unified 계정은 equity 사용
            a0 = acc_list[0] if acc_list else None
            total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0) if a0 else 0.0
            logger.info("API 계정 확인 완료: 선물 계정 total=%.2f USDT", total)
            self._connection_logged = True
        except Exception as e:
            logger.warning("Gate.io 연결/계정 확인 중 오류 (재시도됨): %s", e)

    @property
    def api(self):
        if self._api is None:
            self._api = gate_api.FuturesApi(gate_api.ApiClient(self.config))
        return self._api

    @property
    def settle(self):
        return config.SETTLE

    @property
    def contract(self):
        return config.CONTRACT

    # --- 캔들 (캐시: limit으로 최근 N봉만 요청, 30봉 완성 시점에만 진입 체크하므로 주기적으로 limit=200 등으로 갱신) ---
    def get_candles(self, limit: int = None, interval: str = None, max_retries: int = 3, retry_delay: float = 3.0) -> list:
        """30분봉 OHLCV. 반환: [{"t": ts, "o", "h", "l", "c", "v"}, ...] 오래된 것부터.
        연결 끊김(RemoteDisconnected 등) 시 재시도 후 실패 시 빈 리스트 반환 및 로그."""
        self._log_connection_once()
        if limit is None:
            limit = getattr(config, "CANDLE_LIMIT", 300)
        interval = interval or config.CANDLE_INTERVAL
        for attempt in range(max_retries):
            try:
                raw = self.api.list_futures_candlesticks(
                    self.settle, self.contract, limit=limit, interval=interval
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "캔들 조회 실패 (재시도 %d/%d): %s - %s초 후 재시도",
                        attempt + 1, max_retries, e, retry_delay,
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error(
                        "캔들 조회 최종 실패 (%d회 시도): %s",
                        max_retries, e,
                    )
                    return []

        out = []
        for r in raw:
            # FuturesCandlestick: t, v, c, h, l, o (문서 기준)
            out.append({
                "t": int(r.t) if hasattr(r, "t") else int(getattr(r, "t", 0)),
                "o": float(r.o),
                "h": float(r.h),
                "l": float(r.l),
                "c": float(r.c),
                "v": float(r.v) if hasattr(r, "v") else 0.0,
            })
        # API는 최신이 먼저 올 수 있음 → 시간순 정렬
        out.sort(key=lambda x: x["t"])
        logger.debug("캔들 조회: interval=%s limit=%d → %d봉", interval, limit, len(out))
        return out

    def get_last_price(self) -> Optional[float]:
        """현재가(last) 조회. WebSocket 스트림 있으면 우선 사용, 없으면 REST API."""
        # WebSocket 스트림이 있으면 실시간 가격 사용
        if self._price_stream:
            ws_price = self._price_stream.get_last_price()
            if ws_price is not None:
                return ws_price
        # 폴백: REST API 티커 조회
        try:
            tickers = self.api.list_futures_tickers(self.settle)
            for t in (tickers or []):
                if getattr(t, "contract", None) == self.contract:
                    last_ = getattr(t, "last", None)
                    if last_ is not None:
                        return float(last_)
            return None
        except Exception as e:
            logger.debug("현재가 조회 실패: %s", e)
            return None

    # --- 계정/포지션 ---
    def get_account(self):
        self._log_connection_once()
        return self.api.list_futures_accounts(self.settle)

    def get_position(self):
        """현재 포지션 (해당 contract). 없으면 None."""
        try:
            pos = self.api.get_position(self.settle, self.contract)
            size = float(pos.size or 0)
            if size == 0:
                logger.debug("포지션 없음: %s", self.contract)
                return None
            out = {
                "size": size,
                "side": "long" if size > 0 else "short",
                "entry_price": float(pos.entry_price or 0),
                "contract": pos.contract,
            }
            logger.debug("포지션 조회: %s size=%s entry=%s", out["side"], out["size"], out["entry_price"])
            return out
        except gate_api.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise

    def list_positions(self, holding: bool = True):
        return self.api.list_positions(self.settle, holding=holding)

    # --- 주문: 시장가 진입 (price=0 = 시장가) ---
    def place_market_order(self, side: str, size: str, reduce_only: bool = False):
        """진입: 시장가. side: 'long' -> buy (size 양수), 'short' -> sell (size 음수). size: 계약 수 (문자열). price=0 → 시장가.
        
        Gate.io API: size의 부호로 방향 결정
        - 양수: buy (롱 진입)
        - 음수: sell (숏 진입)
        """
        logger.info("시장가 주문 요청: side=%s size=%s reduce_only=%s", side, size, reduce_only)
        
        # size를 정수로 변환하여 부호 결정
        size_int = int(size)
        if side == "long":
            # 롱 진입: size를 양수로
            order_size = str(abs(size_int))
            logger.info("주문 size 변환: 입력side=%s 입력size=%s → order_size=%s (양수=롱)", side, size, order_size)
        else:  # short
            # 숏 진입: size를 음수로
            order_size = str(-abs(size_int))
            logger.info("주문 size 변환: 입력side=%s 입력size=%s → order_size=%s (음수=숏)", side, size, order_size)
        
        # FuturesOrder 생성 (Gate.io API: size의 부호로 방향 결정)
        order = FuturesOrder(
            contract=self.contract,
            size=order_size,  # 양수=롱, 음수=숏
            iceberg=0,
            price="0",  # 0 = 시장가 (tif=ioc 필수)
            close=False,
            reduce_only=reduce_only,
            tif="ioc",  # 시장가 주문은 IOC 또는 FOK 필수 (Gate API 요구)
        )
        
        result = self.api.create_futures_order(self.settle, order)
        order_id = getattr(result, "id", "?")
        logger.info("시장가 주문 접수: order_id=%s 입력side=%s order.size=%s (양수=롱, 음수=숏)", order_id, side, order_size)
        return result

    # --- TP/SL: 가격 트리거 주문 (take_profit / stop_loss), 트리거 시 시장가 체결 ---
    # FuturesInitialOrder.price="0" → 트리거 도달 시 시장가로 체결 (Gate API 규격).
    # 포지션 방향별 청산: 롱 청산 = 매도(sell), 숏 청산 = 매수(buy). API 스키마는 side: "buy" | "sell".
    # FuturesPriceTrigger.rule: 1 = >= price, 2 = <= price
    # 롱 TP: 가격 >= trigger 시 매도(sell) → rule=1
    # 롱 SL: 가격 <= trigger 시 매도(sell) → rule=2
    # 숏 TP: 가격 <= trigger 시 매수(buy) → rule=2
    # 숏 SL: 가격 >= trigger 시 매수(buy) → rule=1
    def _close_side(self, position_side: str) -> str:
        """포지션 방향에 따른 청산 주문 side. long → sell, short → buy."""
        return "sell" if position_side == "long" else "buy"

    def _round_trigger_price(self, price) -> float:
        """트리거 가격을 계약 가격 단위(PRICE_TICK)의 정수배로 반올림. Gate API 요구."""
        tick = getattr(config, "PRICE_TICK", 1)
        p = float(price)
        rounded = round(p / tick) * tick
        # 부동소수 오차 방지: 정수면 정수, 아니면 소수 자릿수 제한
        if tick >= 1:
            return float(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return float(f"{rounded:.{nd}f}".rstrip("0").rstrip("."))

    def _round_trigger_price_str(self, price) -> str:
        """트리거 가격을 계약 가격 단위(PRICE_TICK)의 정수배로 반올림. Gate API 요구."""
        tick = getattr(config, "PRICE_TICK", 1)
        p = float(price)
        rounded = round(p / tick) * tick
        # 부동소수 오차 방지: 정수면 정수 문자열, 아니면 소수 자릿수 제한
        if tick >= 1:
            return str(int(round(rounded)))
        nd = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]) if "." in f"{tick:.10f}" else 0)
        return f"{rounded:.{nd}f}".rstrip("0").rstrip(".")

    def create_take_profit_order(self, trigger_price: str, size: str, side: str, is_partial: bool = False):
        """Take profit: 트리거 시 시장가 청산. price=0 → 시장가. side=포지션 방향(long/short).
        is_partial: True면 부분 익절(close-long-order), False면 전체 포지션 익절(close-long-position)"""
        order_side = self._close_side(side)
        size_int = int(size) if isinstance(size, str) else int(size)
        price_str = self._round_trigger_price_str(trigger_price)
        # Gate API 규칙:
        # - close-long-position: size=0 (전체 포지션 청산)
        # - plan-close-long-position: size < 0 (롱 포지션 부분 청산은 음수)
        # - plan-close-short-position: size > 0 (숏 포지션 부분 청산은 양수)
        if not is_partial:
            initial_size = 0  # 전체 청산
        else:
            # 부분 청산: 롱은 음수, 숏은 양수
            initial_size = -size_int if side == "long" else size_int
        initial = FuturesInitialOrder(
            contract=self.contract,
            size=initial_size,
            price="0",
            reduce_only=True,
            tif="ioc",
            **(dict(close=True, is_close=True) if not is_partial else {}),
        )
        setattr(initial, "side", order_side)
        trigger = FuturesPriceTrigger(
            strategy_type=0,
            price_type=0,
            price=price_str,
            rule=1 if side == "long" else 2,  # long: >= tp, short: <= tp
        )
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        # close-long-order/close-short-order는 read-only라 요청에 사용 불가
        # 부분 청산: plan-close-long-position / plan-close-short-position
        # 전체 청산: close-long-position / close-short-position
        if is_partial:
            order_type_value = "plan-close-long-position" if side == "long" else "plan-close-short-position"
        else:
            order_type_value = "close-long-position" if side == "long" else "close-short-position"
        setattr(order, "order_type", order_type_value)
        try:
            result = self.api.create_price_triggered_order(self.settle, order)
            logger.info("가격 트리거 TP 등록: trigger_price=%s size=%s order_type=%s(is_partial=%s) reduce_only=True order_id=%s", 
                       price_str, size, order_type_value, is_partial, getattr(result, "id", "?"))
            return result
        except Exception as e:
            logger.error("TP 주문 등록 실패: trigger_price=%s size=%s side=%s order_side=%s reduce_only=True 오류=%s", 
                        price_str, size, side, order_side, e)
            raise

    def create_stop_loss_order(self, trigger_price: str, size: str, side: str):
        """Stop loss: 트리거 시 시장가 청산. price=0 → 시장가. side=포지션 방향(long/short)."""
        order_side = self._close_side(side)
        size_int = int(size) if isinstance(size, str) else int(size)
        price_str = self._round_trigger_price_str(trigger_price)
        trigger_rule = 2 if side == "long" else 1  # long: <= sl (rule=2), short: >= sl (rule=1)
        
        # 등록 시점 가격 규칙 확인 (롱 SL: trigger < last_price, 숏 SL: trigger > last_price)
        last_price_check = self.get_last_price()
        if last_price_check is not None:
            if side == "long" and float(price_str) >= last_price_check:
                logger.warning("SL 등록 시 가격 규칙 위반 가능성: 롱 SL trigger_price(%.2f) >= last_price(%.2f) → 등록 시도하지만 실패할 수 있음", 
                             float(price_str), last_price_check)
            elif side == "short" and float(price_str) <= last_price_check:
                logger.warning("SL 등록 시 가격 규칙 위반 가능성: 숏 SL trigger_price(%.2f) <= last_price(%.2f) → 등록 시도하지만 실패할 수 있음", 
                             float(price_str), last_price_check)
        
        # 현재 포지션 확인 (등록 시점 수량 검증)
        pos_check = self.get_position()
        if pos_check:
            pos_size_abs = abs(pos_check["size"])
            if size_int > pos_size_abs:
                logger.warning("SL 등록 시 수량 규칙 위반 가능성: 주문수량(%d) > 현재포지션수량(%.2f) → reduce_only 규칙 위반 가능성", 
                             size_int, pos_size_abs)
        
        # close-long-position 타입 시 Gate API: initial.size 는 0 (전체 포지션 청산)
        initial = FuturesInitialOrder(
            contract=self.contract,
            size=0,  # close 시 size must zero (Gate API)
            price="0",  # 0 = 트리거 시 시장가 체결
            reduce_only=True,
            tif="ioc",
            close=True,
            is_close=True,
        )
        setattr(initial, "side", order_side)  # API 필수: 청산 방향 (buy/sell)
        trigger = FuturesPriceTrigger(
            strategy_type=0,
            price_type=0,
            price=price_str,
            rule=trigger_rule,  # long: <= sl (rule=2), short: >= sl (rule=1)
        )
        order = FuturesPriceTriggeredOrder(initial=initial, trigger=trigger)
        order_type_value = "close-long-position" if side == "long" else "close-short-position"
        setattr(order, "order_type", order_type_value)
        try:
            result = self.api.create_price_triggered_order(self.settle, order)
            order_id = getattr(result, "id", "?")
            logger.info("가격 트리거 SL 등록: trigger_price=%s size=%s side=%s order_side=%s order_type=%s(전체포지션) rule=%d reduce_only=True order_id=%s", 
                       price_str, size, side, order_side, order_type_value, trigger_rule, order_id)
            return result
        except Exception as e:
            logger.error("SL 주문 등록 실패: trigger_price=%s size=%s side=%s order_side=%s rule=%d reduce_only=True 오류=%s", 
                        price_str, size, side, order_side, trigger_rule, e)
            raise

    def list_price_orders(self, status: str = "open"):
        """가격 트리거 주문 목록. status: 'open', 'finished', 'inactive', 'invalid'."""
        return self.api.list_price_triggered_orders(self.settle, status=status)

    def get_price_triggered_order(self, order_id: str):
        """단일 가격 트리거 주문 상세 조회."""
        try:
            return self.api.get_price_triggered_order(self.settle, order_id)
        except Exception as e:
            logger.debug("가격 트리거 주문 상세 조회 실패 order_id=%s: %s", order_id, e)
            return None
    
    def get_futures_order(self, order_id: str):
        """단일 선물 주문 상세 조회."""
        try:
            return self.api.get_futures_order(self.settle, order_id)
        except Exception as e:
            logger.debug("선물 주문 상세 조회 실패 order_id=%s: %s", order_id, e)
            return None

    def list_failed_price_orders(self):
        """실패한 가격 트리거 주문 목록 조회 (finish_as='failed'인 finished 주문만)."""
        failed_orders = []
        try:
            # finished 상태 중 failed인 것들만 조회 (Gate API는 status='invalid' 미지원)
            finished = self.api.list_price_triggered_orders(self.settle, status="finished")
            for o in (finished or []):
                finish_as = getattr(o, "finish_as", None)
                # auto_cancelled는 SDK가 허용하지 않지만 Gate API 응답에 올 수 있음 (cancelled로 간주)
                if finish_as in ("failed", "cancelled", "auto_cancelled"):
                    failed_orders.append(o)
        except Exception as e:
            # SDK가 finish_as="auto_cancelled"를 파싱하지 못하는 경우 예외 발생 가능
            # 이 경우 경고만 출력하고 빈 리스트 반환 (다음 주기에서 재시도)
            error_msg = str(e)
            if "auto_cancelled" in error_msg:
                logger.debug("SDK가 auto_cancelled 상태를 파싱하지 못함 (무시): %s", error_msg)
            else:
                logger.warning("실패한 주문 조회 실패: %s", e)
        return failed_orders

    def cancel_price_order(self, order_id: str):
        logger.info("가격 트리거 주문 취소: order_id=%s", order_id)
        return self.api.cancel_price_triggered_order(self.settle, order_id)

    def cancel_all_price_orders(self):
        logger.info("가격 트리거 주문 전체 취소 요청")
        return self.api.cancel_price_triggered_order_list(self.settle)

    def update_price_triggered_order(self, order_id: str, trigger_price: str, size: Optional[int] = None):
        """기존 가격 트리거 주문 수정 (트리거 가격 변경). 브레이크이븐용."""
        price_str = self._round_trigger_price_str(trigger_price)
        # order_id는 정수형(uint64)으로 변환 필요
        order_id_int = int(order_id) if isinstance(order_id, str) else int(order_id)
        update = FuturesUpdatePriceTriggeredOrder(
            order_id=order_id_int,
            trigger_price=price_str,
        )
        if size is not None:
            update.size = int(size)
        result = self.api.update_price_triggered_order(self.settle, order_id_int, update)
        logger.info("가격 트리거 주문 수정: order_id=%s trigger_price=%s", order_id, price_str)
        return result


