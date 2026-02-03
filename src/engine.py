"""
EMA 캔들패턴 전략 엔진.
- 30봉 완성 직후에만 진입 조건 체크 (캐시)
- 브레이크이븐 시간 경과 시 손절가를 진입가로 변경
"""
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

# KST = UTC+9 (로그용 년-월-일 시:분 표시)
KST = timezone(timedelta(hours=9))


def _ts_to_ymdhm(ts: int) -> str:
    """Unix timestamp → 'YYYY-MM-DD HH:MM' (KST)."""
    return datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d %H:%M")

import config
from src.api.gate_client import GateFuturesClient
from src.strategy.indicators import add_indicators
from src.strategy.signals import check_entry
from src.notify.discord import notify_discord

logger = logging.getLogger(__name__)

# 30분 = 1800초
BAR_SECONDS = config.CHART_BAR_MINUTES * 60
# 브레이크이븐: 진입 시각 기준 N시간 경과 시 적용
BREAKEVEN_SECONDS = int(config.BREAKEVEN_HOURS * 3600)


class TradingEngine:
    def __init__(self, client: Optional[GateFuturesClient] = None):
        self.client = client or GateFuturesClient()
        # 캐시: 마지막으로 진입 조건을 체크한 봉의 timestamp (30봉 완성 직후만 체크)
        self._last_bar_ts_checked: Optional[int] = None
        # 진입 신호 발생 후 포지션 확인 중인지
        self._waiting_for_position = False
        # 진입 방향 (1=long, -1=short), 진입가, 수량, SL/TP 가격 등 (진입 확인 후 설정)
        self._entry_side: Optional[str] = None
        self._entry_price: Optional[float] = None
        self._entry_size: Optional[str] = None
        self._sl_price: Optional[float] = None
        self._tp_price: Optional[float] = None
        self._tp1_price: Optional[float] = None
        self._tp2_price: Optional[float] = None
        self._entry_timestamp: Optional[float] = None  # 포지션 확정(TP/SL 등록) 시각 (브레이크이븐 시간 계산용)
        self._sl_order_id: Optional[str] = None  # 브레이크이븐 시 기존 SL 취소 후 재등록
        self._tp_order_ids: list = []  # TP1, TP2, 최종 TP (또는 최종만)
        self._tp_sl_set: bool = False  # 진입 확인 후 TP/SL 한 번만 등록
        self._breakeven_done: bool = False  # 브레이크이븐 적용 후 한 번만 SL 이동
        self._breakeven_in_progress: bool = False  # 브레이크이븐 진행 중 플래그 (중복 실행 방지)
        self._logged_failed_order_ids: set = set()  # 이미 로그한 실패 주문 ID (중복 로그 방지)
        self._consec_losses: int = 0
        self._skip_next: bool = False
        self._partial_tp1_done: bool = False
        self._partial_tp2_done: bool = False
        self._partial_tp1_notified: bool = False  # Discord 부분 익절 알림용 (TP1/TP2 구분)
        # 30분마다 지표 로그: 마지막으로 로그한 봉의 timestamp
        self._last_indicators_logged_ts: Optional[int] = None
        # 브레이크이븐 체크 스레드 제어
        self._breakeven_thread: Optional[threading.Thread] = None
        self._stop_breakeven_thread = False
        # 진입 조건 체크 스레드 제어 (30분 봉 완성 시점 감지)
        self._entry_check_thread: Optional[threading.Thread] = None
        self._stop_entry_check_thread = False
        self._entry_check_lock = threading.Lock()  # 진입 체크 중복 실행 방지
        # 마지막으로 처리한 완성된 봉의 종료 시각 (새 봉 완성 감지용)
        self._last_processed_bar_end_ts: Optional[int] = None
        # Discord/청산 감지: 이전 사이클에 포지션이 있었는지, 이전 포지션 수량
        self._had_position_last_run: bool = False
        self._last_position_size: Optional[float] = None

    def _current_bar_start_ts(self) -> int:
        """현재 시간 기준 이번 30분봉 시작 시각 (Unix sec)."""
        return int(time.time() // BAR_SECONDS) * BAR_SECONDS

    def _is_new_bar_closed(self, candles: list) -> bool:
        """방금 30봉이 하나 완성되었는지. 캔들 마지막 봉의 t가 이전 봉 구간이면 완성됐음."""
        if not candles or len(candles) < 2:
            return False
        last_t = candles[-1]["t"]
        current_start = self._current_bar_start_ts()
        # 마지막 봉이 현재 봉이 아니면(즉 last_t < current_start) 이미 완성된 봉이 있음
        return last_t < current_start

    def _get_candles_cached(self, limit: int = None) -> list:
        """캔들 조회 후 지표 추가. API 호출 1회."""
        if limit is None:
            limit = config.CANDLE_LIMIT
        raw = self.client.get_candles(limit=limit)
        out = add_indicators(raw)
        if out:
            logger.debug("캔들 로드: %d봉 (최근 종가=%.2f)", len(out), out[-1]["c"])
        return out

    def _position_size(self, close: float, atr: float, equity: float) -> Tuple[Optional[str], bool]:
        """포지션 크기(계약 수 문자열) 및 sl_allowed."""
        if close <= 0 or atr <= 0 or equity <= 0:
            return None, False
        sl_dist = atr * config.SL_ATR_MULT
        sl_pct = (sl_dist / close) * 100.0
        if sl_pct > config.MAX_SL_PCT:
            return None, False
        # 레버리지 계산: sl_pct와 MAX_RISK_PCT로 결정
        leverage = min(config.MAX_LEVERAGE, config.MAX_RISK_PCT / sl_pct)
        if leverage < 1.0:
            return None, False
        # 계약 수 = (equity * leverage) / (close * CONTRACT_MULTIPLIER)
        contract_value = close * config.CONTRACT_MULTIPLIER
        size = int((equity * leverage) / contract_value)
        if size < config.ORDER_SIZE_MIN:
            return None, False
        return str(size), True

    def _position_size_test_mode(self, close: float, equity: float) -> Tuple[Optional[str], bool]:
        """테스트 모드: MAX_LEVERAGE만으로 수량 계산 (최소 수량)."""
        if close <= 0 or equity <= 0:
            return None, False
        contract_value = close * config.CONTRACT_MULTIPLIER
        size = int((equity * config.MAX_LEVERAGE) / contract_value)
        size = max(config.ORDER_SIZE_MIN, size)  # 최소 1계약
        return str(size), True

    def _setup_tp_sl_orders(self, side: str, size: str, entry: float, sl: float, tp: float):
        """TP/SL 등록. side=포지션 방향(long/short), size=포지션 수량."""
        size_int = max(1, int(float(size)))
        size = str(size_int)
        # 기존 가격 트리거 주문 제거(이전 실패로 중복 방지)
        try:
            self.client.cancel_all_price_orders()
        except Exception as e:
            logger.warning("기존 가격 트리거 주문 취소 중 오류(무시): %s", e)
        # SL 1개: 전량 청산 (가격 트리거 주문)
        try:
            self.client.create_stop_loss_order(str(sl), size, side)
        except Exception as e:
            logger.error("SL 주문 등록 실패: side=%s size=%s sl=%.2f 오류=%s", side, size, sl, e)
            raise
        # TP: TP1·TP2는 정해진 수량·가격(부분 익절), 최종 TP는 전체 포지션 수량·가격(전체 포지션 익절)
        try:
            if config.ENABLE_PARTIAL_TP and self._tp1_price is not None and self._tp2_price is not None:
                size1 = max(1, int(size_int * config.TP1_CLOSE_PCT / 100.0))
                size2 = max(1, int((size_int - size1) * config.TP2_CLOSE_PCT / 100.0))
                # TP1, TP2: 부분 익절 (is_partial=True)
                self.client.create_take_profit_order(str(self._tp1_price), str(size1), side, is_partial=True)
                self.client.create_take_profit_order(str(self._tp2_price), str(size2), side, is_partial=True)
                # 최종 TP: 전체 포지션 익절 (is_partial=False)
                self.client.create_take_profit_order(str(tp), size, side, is_partial=False)
            else:
                # TP 1개만: 전체 포지션 익절 (is_partial=False)
                self.client.create_take_profit_order(str(tp), size, side, is_partial=False)
        except Exception as e:
            logger.error("TP 주문 등록 실패: side=%s size=%s tp=%.2f 오류=%s", side, size, tp, e)
            raise
        # 브레이크이븐 시간 계산용: 포지션 확정 시각 기록
        self._entry_timestamp = time.time()

    def _breakeven_check_loop(self):
        """브레이크이븐 체크 전용 스레드: 1초마다 _try_breakeven 호출."""
        logger.info("브레이크이븐 체크 스레드 시작 (1초 주기)")
        while not self._stop_breakeven_thread:
            try:
                self._try_breakeven()
            except Exception as e:
                logger.exception("브레이크이븐 체크 오류: %s", e)
            time.sleep(1.0)
        logger.info("브레이크이븐 체크 스레드 종료")

    def _entry_check_loop(self):
        """진입 조건 체크 전용 스레드: 2초마다 30분 봉 완성 시점 감지 → 지표 계산 → 진입 체크."""
        logger.info("진입 조건 체크 스레드 시작 (2초 주기, 30분 봉 완성 시점 감지)")
        while not self._stop_entry_check_thread:
            try:
                # 락으로 중복 실행 방지
                if self._entry_check_lock.acquire(blocking=False):
                    try:
                        # 30분 봉 완성 시점 감지
                        now = time.time()
                        current_bar_start = self._current_bar_start_ts()
                        # 현재 봉의 종료 시각
                        current_bar_end = current_bar_start + BAR_SECONDS
                        
                        # 새 봉이 완성되었는지 확인
                        if self._last_processed_bar_end_ts is None:
                            # 첫 실행: 현재 봉 시작 시각을 기준으로 이전 봉 종료 시각 설정
                            # (현재 봉이 아직 완성되지 않았으므로, 이전 봉 종료 시각 = 현재 봉 시작 시각)
                            self._last_processed_bar_end_ts = current_bar_start
                        
                        # 이전에 처리한 봉의 종료 시각이 지났고, 아직 현재 봉 종료 시각 전이면 새 봉 완성
                        # 예: 이전 봉이 12:00-12:30이면, 12:30 이후에 완성됨
                        # 현재 봉(12:30-13:00)이 아직 완성되지 않았으면(now < 13:00) 처리
                        # 하지만 이미 처리한 봉이면 스킵 (now >= current_bar_end면 다음 봉으로 넘어감)
                        if now >= self._last_processed_bar_end_ts and now < current_bar_end:
                            # 새 봉 완성 감지: 지표 계산 → 진입 체크
                            logger.info("30분 봉 완성 감지: 지표 계산 시작")
                            start_time = time.time()
                            
                            # 1. 지표 계산 (캔들 조회 + 지표 추가)
                            candles = self._get_candles_cached()
                            if candles:
                                calc_time = time.time() - start_time
                                logger.info("지표 계산 완료: %.2f초 소요", calc_time)
                                
                                # 2. 지표 로그 출력
                                self._log_indicators_if_new_bar(candles)
                                
                                # 3. 진입 조건 체크 및 실행
                                self._check_and_execute_entry_with_candles(candles)
                                
                                # 처리한 봉의 종료 시각 업데이트 (다음 봉 완성 감지용)
                                # 완성된 봉의 종료 시각 = 현재 봉 종료 시각 (다음 봉이 완성될 때까지 기다림)
                                self._last_processed_bar_end_ts = current_bar_end
                            else:
                                logger.warning("지표 계산 실패: 캔들 조회 결과 없음")
                    finally:
                        self._entry_check_lock.release()
            except Exception as e:
                logger.exception("진입 조건 체크 오류: %s", e)
            time.sleep(2.0)
        logger.info("진입 조건 체크 스레드 종료")

    def _check_and_execute_entry_with_candles(self, candles: list):
        """진입 조건 체크 및 진입 실행 (지표 계산 완료된 candles 사용)."""
        # 포지션이 있거나 진입 대기 중이면 스킵
        pos = self.client.get_position()
        if pos:
            # 기존 포지션이 있으면 진입 시도하지 않음
            logger.debug("포지션 존재로 진입 스킵: %s size=%s", pos["side"], pos["size"])
            return
        if self._waiting_for_position:
            return

        # 포지션 없음: 진입 대기 중이 아닐 때만 상태 초기화
        if self._entry_side is not None and not self._waiting_for_position:
            self._entry_side = None
            self._entry_price = None
            self._entry_size = None
            self._sl_price = None
            self._tp_price = None
            self._tp1_price = None
            self._tp2_price = None
            self._tp_sl_set = False
            self._breakeven_done = False
            self._breakeven_in_progress = False
            self._logged_failed_order_ids.clear()
            self._entry_timestamp = None
            self._partial_tp1_done = False
            self._partial_tp2_done = False

        if not candles:
            return

        # 진입 조건: 지표 로그와 동일하게 '완성된 가장 최근 봉' 기준으로만 체크 (테스트 모드 포함)
        completed_row = self._last_completed_bar_row(candles)
        if completed_row is None:
            return
        last_ts = completed_row["t"]
        if config.ENTRY_CHECK_ONLY_ON_BAR_CLOSE:
            if self._last_bar_ts_checked == last_ts:
                return  # 이미 이 봉으로 체크했으면 스킵
            self._last_bar_ts_checked = last_ts

        completed_idx = next((i for i, c in enumerate(candles) if c.get("t") == last_ts), None)
        if completed_idx is None:
            return
        long_ok, short_ok = check_entry(candles, completed_idx)
        
        # 테스트 모드: 진입 조건 오버라이드
        if getattr(config, "TEST_MODE", False) and getattr(config, "TEST_ENTRY_OVERRIDE", False):
            side_override = getattr(config, "TEST_ENTRY_SIDE", "long").lower()
            if side_override == "long":
                long_ok = True
                short_ok = False
            elif side_override == "short":
                long_ok = False
                short_ok = True
            else:
                logger.warning("테스트 모드: 잘못된 TEST_ENTRY_SIDE 값 (%s), 기본값 long 사용", side_override)
                long_ok = True
                short_ok = False
            logger.info("테스트 모드: 진입 조건 오버라이드 → %s (long_ok=%s short_ok=%s)", config.TEST_ENTRY_SIDE, long_ok, short_ok)
        
        logger.info("진입 조건 체크(봉 %s): long_ok=%s short_ok=%s", _ts_to_ymdhm(last_ts), long_ok, short_ok)
        if long_ok or short_ok:
            logger.info("진입 신호: long_ok=%s short_ok=%s (봉 %s)", long_ok, short_ok, _ts_to_ymdhm(last_ts))
        if not long_ok and not short_ok:
            return
        if config.ENABLE_SKIP_AFTER_3_LOSSES and self._skip_next:
            self._skip_next = False
            logger.info("Skip next trade (3 consecutive losses)")
            return

        row = completed_row
        if not row or row.get("atr") is None:
            logger.warning("진입 스킵: 완성 봉 또는 ATR 없음 (row=%s atr=%s)", row is not None, row.get("atr") if row else None)
            return
        close = row["c"]
        atr = row["atr"]
        try:
            acc = self.client.get_account()
            acc_list = acc if isinstance(acc, list) else [acc] if acc else []
            if acc_list:
                a0 = acc_list[0]
                total = float(getattr(a0, "equity", None) or getattr(a0, "total", None) or 0)
            else:
                total = 0.0
            equity = total * (config.EQUITY_PCT / 100.0)
            logger.info("계정 조회: total=%.2f USDT, 투입자본(%.0f%%)=%.2f", total, config.EQUITY_PCT, equity)
        except Exception as e:
            logger.warning("계정 조회 실패: %s", e)
            equity = 0.0
        if equity <= 0:
            logger.warning("진입 스킵: 투입 자본 없음 (equity=%.2f)", equity)
            return
        # 테스트 모드: sl_pct 미사용, MAX_LEVERAGE만으로 수량 계산 → TP1/TP2/최종 청산 분배 검증용. 일반 모드: _position_size(레버리지·sl_pct) 적용
        if getattr(config, "TEST_MODE", False) and getattr(config, "TEST_ENTRY_OVERRIDE", False):
            size_str, sl_allowed = self._position_size_test_mode(close, equity)
        else:
            size_str, sl_allowed = self._position_size(close, atr, equity)
            if not sl_allowed or not size_str or int(size_str) < config.ORDER_SIZE_MIN:
                logger.warning("진입 스킵: 포지션 수량 조건 불충족 (sl_allowed=%s size_str=%s ORDER_SIZE_MIN=%s)", sl_allowed, size_str, config.ORDER_SIZE_MIN)
                return

        # 진입 방향 결정 (명확하게)
        if long_ok:
            side = "long"
        elif short_ok:
            side = "short"
        else:
            logger.error("진입 방향 결정 실패: long_ok=%s short_ok=%s", long_ok, short_ok)
            return
        
        sl_dist = atr * config.SL_ATR_MULT
        if side == "long":
            sl_price = close - sl_dist
            tp_price = close + sl_dist * config.RR_RATIO
        else:  # short
            sl_price = close + sl_dist
            tp_price = close - sl_dist * config.RR_RATIO
        
        logger.info("진입 방향 결정: side=%s (long_ok=%s short_ok=%s)", side, long_ok, short_ok)

        full_tp_pct = abs(tp_price - close) / close * 100.0
        tp1_pct = full_tp_pct * (config.TP1_RATIO_OF_TP / 100.0)
        tp2_pct = full_tp_pct * (config.TP2_RATIO_OF_TP / 100.0)
        if side == "long":
            tp1_price = close * (1 + tp1_pct / 100.0)
            tp2_price = close * (1 + tp2_pct / 100.0)
        else:
            tp1_price = close * (1 - tp1_pct / 100.0)
            tp2_price = close * (1 - tp2_pct / 100.0)

        # 진입 주문 직전 포지션 재확인 (기존 포지션 존재 시 진입 방지)
        pos_before_entry = self.client.get_position()
        if pos_before_entry:
            existing_side = pos_before_entry["side"]
            if existing_side != side:
                logger.warning("진입 스킵: 기존 포지션 존재 (기존=%s 진입시도=%s) → 반대 방향 진입 불가", existing_side, side)
                return
            else:
                logger.warning("진입 스킵: 동일 방향 포지션 이미 존재 (%s size=%s)", existing_side, pos_before_entry["size"])
                return
        
        # 진입 주문 (시장가)
        # side 변수 확인 로그 (디버깅용)
        logger.info("진입 주문 전 최종 확인: side=%s long_ok=%s short_ok=%s", side, long_ok, short_ok)
        order_result = None
        try:
            order_result = self.client.place_market_order(side, size_str, reduce_only=False)
            logger.info("진입 주문 접수: side=%s size=%s 예상가=%.2f sl=%.2f tp=%.2f", side, size_str, close, sl_price, tp_price)
        except Exception as e:
            logger.exception("진입 주문 실패: %s", e)
            return

        # 주문 후 즉시 포지션 확인 (주문이 체결되기 전까지 약간의 지연)
        time.sleep(1.0)
        pos_immediate = self.client.get_position()
        if pos_immediate:
            actual_pos_side = pos_immediate["side"]
            if actual_pos_side != side:
                logger.error("진입 주문 후 포지션 방향 불일치: 진입시도=%s 실제포지션=%s size=%s → 주문이 반대 방향 포지션과 충돌, 상태 초기화", 
                           side, actual_pos_side, pos_immediate["size"])
                # 상태 초기화 (다음 진입 시도 가능하도록)
                self._waiting_for_position = False
                self._entry_side = None
                self._entry_price = None
                self._entry_size = None
                self._sl_price = None
                self._tp_price = None
                self._tp1_price = None
                self._tp2_price = None
                return

        self._waiting_for_position = True
        self._entry_side = side
        self._entry_price = close
        self._entry_size = size_str
        self._sl_price = sl_price
        self._tp_price = tp_price
        self._tp1_price = tp1_price
        self._tp2_price = tp2_price

        # 진입 직후 포지션 폴링(2초 간격, 최대 45초) → 포지션 보이면 즉시 TP/SL 등록
        poll_interval = 2.0
        poll_max_sec = 45
        for _ in range(max(1, int(poll_max_sec / poll_interval))):
            time.sleep(poll_interval)
            pos = self.client.get_position()
            if pos:
                # 실제 포지션 방향과 진입 시도 방향 일치 확인
                actual_pos_side = pos["side"]
                if actual_pos_side != self._entry_side:
                    logger.warning("포지션 방향 불일치: 진입시도=%s 실제포지션=%s size=%s → TP/SL 등록 스킵, 재시도", 
                                 self._entry_side, actual_pos_side, pos["size"])
                    # 포지션 방향이 일치하지 않으면 계속 폴링 (진입 주문이 아직 처리 중일 수 있음)
                    continue
                
                size_str_pos = str(int(abs(pos["size"])))
                logger.info("포지션 확인(진입 직후): %s size=%s entry=%.2f", pos["side"], size_str_pos, pos["entry_price"])
                logger.info("가격 트리거(조건부) TP/SL 등록: side=%s size=%s sl=%.2f tp=%.2f", self._entry_side, size_str_pos, self._sl_price, self._tp_price)
                try:
                    self._setup_tp_sl_orders(
                        self._entry_side,
                        size_str_pos,
                        self._entry_price,
                        self._sl_price,
                        self._tp_price,
                    )
                    self._tp_sl_set = True
                    self._waiting_for_position = False
                    logger.info("TP/SL 등록 완료")
                    self._partial_tp1_notified = False
                    # Discord: 진입 알림
                    notify_discord(
                        "entry",
                        side=self._entry_side,
                        entry_price=self._entry_price,
                        sl_price=self._sl_price,
                        tp_price=self._tp_price,
                        tp1_price=self._tp1_price,
                        tp2_price=self._tp2_price,
                        size=size_str_pos,
                    )
                except Exception as e:
                    logger.exception("TP/SL 등록 실패: %s", e)
                    # TP/SL 등록 실패 시 상태 초기화 (다음 진입 시도 가능하도록)
                    self._waiting_for_position = False
                break
        else:
            # 폴링 시간 초과: 포지션을 찾지 못함
            logger.warning("진입 직후 포지션 확인 시간 초과 (%d초): 진입 주문이 실패했거나 아직 처리 중일 수 있음", poll_max_sec)
            self._waiting_for_position = False

    def _last_completed_bar_row(self, candles: list) -> Optional[dict]:
        """캔들 중 '30분봉 완성'된 가장 최근 봉 1개 반환.
        완성 = 봉 종료 시각(bar.t + 30분)이 이미 지남. 시계 12:30이 아니라 봉이 실제로 닫혔을 때 기준."""
        if not candles:
            return None
        now = time.time()
        # 캔들은 시간순(오래된 것 먼저). 뒤에서부터 보며 '종료 시각이 지난 봉' 중 가장 최근 봉 선택
        for i in range(len(candles) - 1, -1, -1):
            row = candles[i]
            bar_t = row.get("t")
            if bar_t is None:
                continue
            bar_end = bar_t + BAR_SECONDS
            if bar_end <= now:
                return row
        return None

    def _log_indicators_if_new_bar(self, candles: list) -> None:
        """30분봉이 완성된 시점을 기준으로, 그 봉까지 반영된 지표를 계산해 로그.
        기준은 시계가 아니라 '봉 종료 시각(bar.t + 30분)이 지났는지'."""
        if not candles:
            return
        row_to_log = self._last_completed_bar_row(candles)
        if row_to_log is None:
            return
        bar_ts = row_to_log.get("t")
        if bar_ts is None:
            return
        if self._last_indicators_logged_ts == bar_ts:
            return
        self._last_indicators_logged_ts = bar_ts
        ema20 = row_to_log.get("ema20")
        ema50 = row_to_log.get("ema50")
        ema100 = row_to_log.get("ema100")
        atr = row_to_log.get("atr")
        c = row_to_log.get("c")
        logger.info(
            "지표(30분봉 완성 %s): close=%.2f | EMA20=%.1f EMA50=%.1f EMA100=%.1f | ATR(14)=%.4f",
            _ts_to_ymdhm(bar_ts),
            c if c is not None else 0,
            ema20 if ema20 is not None else 0,
            ema50 if ema50 is not None else 0,
            ema100 if ema100 is not None else 0,
            atr if atr is not None else 0,
        )

    def run_once(self) -> None:
        """한 사이클: 포지션 확인, 실패한 주문 확인, 브레이크이븐 체크.
        진입 조건 체크는 별도 스레드에서 30분 봉 완성 시점에만 실행."""
        pos = self.client.get_position()

        # ----- 청산 감지: 이전에는 포지션이 있었는데 지금 없음 -----
        if self._had_position_last_run and pos is None:
            close_price = self.client.get_last_price()
            if self._breakeven_done:
                notify_discord(
                    "breakeven_closed",
                    side=self._entry_side,
                    close_price=close_price,
                )
            else:
                notify_discord(
                    "close",
                    side=self._entry_side,
                    close_price=close_price,
                )
            self._entry_side = None
            self._entry_price = None
            self._entry_size = None
            self._sl_price = None
            self._tp_price = None
            self._tp1_price = None
            self._tp2_price = None
            self._tp_sl_set = False
            self._breakeven_done = False
            self._entry_timestamp = None
            self._last_position_size = None
            self._partial_tp1_notified = False
        self._had_position_last_run = (pos is not None)
        if pos is None:
            self._last_position_size = None
            return

        # ----- 포지션 있음: 부분 익절 감지 후, 진입 직후면 TP/SL 등록, 실패 주문 확인, 브레이크이븐 -----
        pos_size_abs = abs(pos["size"])
        if self._last_position_size is not None and pos_size_abs < self._last_position_size:
            # 포지션 수량 감소 = 부분 익절 발생
            fill_price = self.client.get_last_price()
            extra = ""
            if self._tp1_price is not None and self._tp2_price is not None:
                if not self._partial_tp1_notified:
                    extra = "(TP1 추정)"
                    self._partial_tp1_notified = True
                else:
                    extra = "(TP2 추정)"
            notify_discord(
                "partial_tp",
                side=pos["side"],
                close_price=fill_price,
                extra=extra.strip(),
            )
        self._last_position_size = pos_size_abs

        if pos:
            logger.info("포지션 보유: %s size=%s entry=%.2f", pos["side"], pos["size"], pos["entry_price"])
            # 실패한 트리거 주문 확인 (이메일 알림 대신 로그로 확인, 중복 로그 방지)
            # 참고: reduce_only=True로 등록된 이전 주문들의 실패 로그가 나타날 수 있음
            # Gate.io의 reduce_only 주문 실패 시 다른 청산 주문 취소 여부 확인
            try:
                # 실패 전 열린 주문 수 확인 (reduce_only 실패 시 취소 여부 확인용)
                open_before = self.client.list_price_orders(status="open")
                open_count_before = len(open_before) if open_before else 0
                failed = self.client.list_failed_price_orders()
                failed_reduce_only_count = 0
                for o in failed:
                    order_id = str(getattr(o, "id", ""))
                    # 이미 로그한 주문은 스킵
                    if order_id in self._logged_failed_order_ids:
                        continue
                    trigger = getattr(o, "trigger", None)
                    price = getattr(trigger, "price", "?") if trigger else "?"
                    reason = getattr(o, "reason", "?")
                    finish_as = getattr(o, "finish_as", "?")
                    order_type = getattr(o, "order_type", "?")
                    initial = getattr(o, "initial", None)
                    size = getattr(initial, "size", "?") if initial else "?"
                    side = getattr(initial, "side", "?") if initial else "?"
                    reduce_only = getattr(initial, "reduce_only", "?") if initial else "?"
                    # reduce_only=False인 이전 주문의 실패는 무시 (현재는 reduce_only=True 사용)
                    # reduce_only=False면 일반 주문으로 취급되어 마진이 필요하고, TP/SL 용도가 아니므로 무시
                    if reduce_only is False:
                        logger.debug("이전 reduce_only=False 주문 실패 (무시, 현재는 reduce_only=True 사용): order_id=%s reason=%s", order_id, reason)
                        self._logged_failed_order_ids.add(order_id)
                        continue
                    # reduce_only=True인 주문의 실패는 중요하므로 경고 로그 출력
                    # SL 주문인지 확인 (order_type과 trigger.rule로 판단)
                    trigger_obj = getattr(o, "trigger", None)
                    is_sl_order = False
                    sl_type = "알수없음"
                    if trigger_obj and pos:
                        rule = getattr(trigger_obj, "rule", None)
                        expected_sl_rule = 2 if pos["side"] == "long" else 1
                        # order_type으로 SL 주문 확인
                        # close-long-order / close-short-order: Order TP/SL
                        # close-long-position / close-short-position: Position TP/SL
                        # plan-close-long-position / plan-close-short-position: Position plan TP/SL
                        is_sl_by_type = False
                        if pos["side"] == "long":
                            is_sl_by_type = order_type in ("close-long-order", "close-long-position", "plan-close-long-position", "")
                        else:
                            is_sl_by_type = order_type in ("close-short-order", "close-short-position", "plan-close-short-position", "")
                        # rule과 order_type 모두 확인
                        if rule == expected_sl_rule and is_sl_by_type:
                            is_sl_order = True
                            # 브레이크이븐 SL인지 원래 SL인지 구분 (trigger_price가 진입가와 비슷하면 브레이크이븐)
                            price_float = float(price or 0)
                            entry_price = pos["entry_price"]
                            if abs(price_float - entry_price) < 1.0:  # 진입가와 1 이내 차이
                                sl_type = "브레이크이븐_SL"
                            else:
                                sl_type = "원래_SL"
                    if is_sl_order:
                        # SL 주문 실패 시 상세 정보 조회 및 분석
                        # 1) trade_id 확인 (이미 가져온 주문 객체에서 먼저 확인)
                        trade_id = getattr(o, "trade_id", None)
                        # trade_id가 없으면 상세 조회
                        if not trade_id:
                            try:
                                order_detail = self.client.get_price_triggered_order(order_id)
                                if order_detail:
                                    trade_id = getattr(order_detail, "trade_id", None)
                                    # 상세 정보에서 추가 필드 확인
                                    finish_as_detail = getattr(order_detail, "finish_as", finish_as)
                                    reason_detail = getattr(order_detail, "reason", reason)
                                    if finish_as_detail != finish_as or reason_detail != reason:
                                        finish_as = finish_as_detail
                                        reason = reason_detail
                            except Exception as e:
                                logger.debug("가격 트리거 주문 상세 조회 실패 order_id=%s: %s", order_id, e)
                        
                        # 2) trade_id가 있으면 실제 생성된 FuturesOrder 조회
                        trade_order_info = ""
                        if trade_id:
                            try:
                                trade_order = self.client.get_futures_order(str(trade_id))
                                if trade_order:
                                    trade_finish_as = getattr(trade_order, "finish_as", "?")
                                    trade_status = getattr(trade_order, "status", "?")
                                    trade_text = getattr(trade_order, "text", "?")
                                    trade_order_info = f" 실제주문(trade_id={trade_id}): status={trade_status} finish_as={trade_finish_as} text={trade_text}"
                                    # IOC 실패 확인
                                    if trade_finish_as == "ioc":
                                        logger.error("%s 실패 원인: IOC 시장가 주문 즉시 체결 실패 → 유동성 부족/급변장 가능성", sl_type)
                                    elif "reduce" in str(trade_text).lower() or "reduce" in str(reason).lower():
                                        logger.error("%s 실패 원인: reduce_only 규칙 위반 → 포지션 수량/방향 문제 가능성", sl_type)
                            except Exception as e:
                                logger.debug("실제 주문 조회 실패 trade_id=%s: %s", trade_id, e)
                        
                        # 3) 현재 포지션 및 열린 TP 주문 정보
                        current_pos_info = ""
                        open_tp_info = ""
                        if pos:
                            current_pos_info = f" 현재포지션: size={pos['size']} entry={pos['entry_price']}"
                            # 열린 TP 주문들의 총 수량 확인 (트리거 시점 포지션 감소 원인 분석)
                            try:
                                open_orders_for_analysis = self.client.list_price_orders(status="open")
                                tp_total_size = 0
                                tp_details = []
                                for o in (open_orders_for_analysis or []):
                                    initial = getattr(o, "initial", None)
                                    if initial:
                                        order_size = abs(int(getattr(initial, "size", 0)))
                                        trigger = getattr(o, "trigger", None)
                                        if trigger:
                                            rule = getattr(trigger, "rule", None)
                                            trigger_price = getattr(trigger, "price", "?")
                                            # TP 주문 식별: 롱 TP는 rule=1, 숏 TP는 rule=2
                                            is_tp = (pos["side"] == "long" and rule == 1) or (pos["side"] == "short" and rule == 2)
                                            if is_tp:
                                                tp_total_size += order_size
                                                tp_details.append(f"TP(price={trigger_price} size={order_size})")
                                if tp_total_size > 0:
                                    open_tp_info = f" 열린TP총수량={tp_total_size} ({', '.join(tp_details)})"
                            except Exception as e:
                                logger.debug("TP 주문 분석 중 오류: %s", e)
                        
                        # 4) 실패 원인 종합 로깅
                        logger.error("실패한 %s 주문 (트리거 시점 실패): order_id=%s order_type=%s trigger_price=%s size=%s side=%s reduce_only=%s finish_as=%s reason=%s%s%s%s", 
                                   sl_type, order_id, order_type, price, size, side, reduce_only, finish_as, reason, current_pos_info, open_tp_info, trade_order_info)
                        
                        # 5) 실패 원인 분석: 트리거 시점 포지션 수량과 주문 수량 비교
                        if pos and size != "?":
                            try:
                                order_size_int = int(size)
                                pos_size_abs = abs(pos["size"])
                                if order_size_int > pos_size_abs:
                                    logger.error("%s 실패 원인 분석: 주문수량(%d) > 현재포지션수량(%.2f) → reduce_only 규칙 위반 (TP 체결로 포지션 감소 가능성)", 
                                               sl_type, order_size_int, pos_size_abs)
                                elif order_size_int == pos_size_abs:
                                    logger.warning("%s 실패 원인 분석: 주문수량(%d) == 현재포지션수량(%.2f) → 다른 원인 가능성 (가격규칙, 마진, 리스크, IOC 미체결 등)", 
                                              sl_type, order_size_int, pos_size_abs)
                            except (ValueError, TypeError):
                                pass
                    else:
                        logger.warning("실패한 트리거 주문: order_id=%s type=%s trigger_price=%s size=%s side=%s reduce_only=%s finish_as=%s reason=%s", 
                                     order_id, order_type, price, size, side, reduce_only, finish_as, reason)
                    # 로그한 주문 ID 기록
                    self._logged_failed_order_ids.add(order_id)
                    if reduce_only is True:
                        failed_reduce_only_count += 1
                # reduce_only 주문 실패 후 열린 주문 수 확인 (다른 주문 취소 여부 확인)
                if failed_reduce_only_count > 0:
                    open_after = self.client.list_price_orders(status="open")
                    open_count_after = len(open_after) if open_after else 0
                    if open_count_after < open_count_before:
                        logger.warning("reduce_only 주문 실패 후 열린 주문 수 감소: %d → %d (거래소가 다른 주문을 취소했을 가능성)", 
                                     open_count_before, open_count_after)
                    else:
                        logger.debug("reduce_only 주문 실패 후 열린 주문 수 유지: %d (다른 주문은 영향 없음)", open_count_after)
            except Exception as e:
                logger.debug("실패한 주문 확인 중 오류: %s", e)
            if self._entry_side and not self._tp_sl_set:
                size_str = str(int(abs(pos["size"])))
                logger.info("가격 트리거(조건부) TP/SL 등록: side=%s size=%s sl=%.2f tp=%.2f", self._entry_side, size_str, self._sl_price, self._tp_price)
                self._setup_tp_sl_orders(
                    self._entry_side,
                    size_str,
                    self._entry_price,
                    self._sl_price,
                    self._tp_price,
                )
                self._tp_sl_set = True
                self._waiting_for_position = False
                logger.info("TP/SL 등록 완료")
            # 브레이크이븐은 별도 스레드에서 1초마다 체크 (run_loop 시작 시 시작됨)
            return

        # 진입 조건 체크는 별도 스레드에서 2초마다 실행 (run_loop 시작 시 시작됨)

    def _try_breakeven(self):
        """브레이크이븐: 진입 후 N시간 경과 시 기존 SL 취소 후 진입가로 SL 재등록 (한 번만)."""
        if not config.ENABLE_BREAKEVEN or self._entry_price is None or self._entry_side is None:
            return
        if self._entry_timestamp is None:
            return
        if self._breakeven_done or (time.time() - self._entry_timestamp) < BREAKEVEN_SECONDS:
            return
        # 브레이크이븐 진행 중이면 중복 실행 방지
        if self._breakeven_in_progress:
            return
        pos = self.client.get_position()
        if not pos:
            return
        # 실제 포지션의 진입가 및 방향 사용 (브레이크이븐은 실제 포지션 기준)
        actual_entry_price = pos["entry_price"]
        actual_position_side = pos["side"]  # 실제 포지션 방향 (long/short)
        # 진입 시 저장한 가격과 실제 거래소 진입가 비교 (디버깅용)
        if self._entry_price is not None:
            price_diff = abs(actual_entry_price - self._entry_price)
            if price_diff > 0.01:  # 0.01 이상 차이나면 로그
                logger.info("브레이크이븐: 진입 시 저장 가격(%.2f)과 실제 거래소 진입가(%.2f) 차이=%.2f", 
                           self._entry_price, actual_entry_price, price_diff)
            else:
                logger.debug("브레이크이븐: 진입 시 저장 가격(%.2f)과 실제 거래소 진입가(%.2f) 일치", 
                           self._entry_price, actual_entry_price)
        # 진입 시 저장된 side와 실제 포지션 side 일치 확인
        if self._entry_side is None:
            logger.warning("브레이크이븐: 진입 시 side 정보 없음, 실제 포지션 side 사용: %s", actual_position_side)
            use_side = actual_position_side
        elif self._entry_side != actual_position_side:
            logger.warning("브레이크이븐: 진입 시 side(%s)와 실제 포지션 side(%s) 불일치, 실제 포지션 side 사용", 
                         self._entry_side, actual_position_side)
            use_side = actual_position_side
        else:
            use_side = self._entry_side
        # 브레이크이븐 SL 수량은 진입 시 수량 사용 (현재 포지션 수량이 아닌 진입 시 수량)
        if self._entry_size is None:
            logger.warning("브레이크이븐: 진입 시 수량 정보 없음, 현재 포지션 수량 사용")
            actual_size = abs(pos["size"])
            size = str(int(actual_size))
        else:
            size = self._entry_size  # 진입 시 수량 사용
        # Gate API: 롱 SL(매도)은 trigger < last_price, 숏 SL(매수)은 trigger > last_price 여야 함
        # 브레이크이븐 적용 가능 여부 확인: 조건 성립 시에만 수정/재등록
        last_price = self.client.get_last_price()
        can_apply_breakeven = False
        if last_price is not None:
            if use_side == "long" and actual_entry_price < last_price:
                can_apply_breakeven = True  # 롱: 진입가 < 현재가 → SL(진입가) < 현재가 조건 만족
            elif use_side == "short" and actual_entry_price > last_price:
                can_apply_breakeven = True  # 숏: 진입가 > 현재가 → SL(진입가) > 현재가 조건 만족
            else:
                # 브레이크이븐 조건 미성립: 원래 SL 주문 유지, 다음 사이클에서 재체크
                # 롱: 현재가가 진입가보다 낮으면(손실 중) 브레이크이븐 불가 → 원래 SL 유지
                # 숏: 현재가가 진입가보다 높으면(손실 중) 브레이크이븐 불가 → 원래 SL 유지
                logger.info("브레이크이븐 대기: 조건 미성립 (롱: entry < 현재가, 숏: entry > 현재가 필요) side=%s entry=%.2f last=%.2f → 원래 SL 주문 유지", 
                           use_side, actual_entry_price, last_price)
                return
        else:
            logger.warning("브레이크이븐: 현재가 조회 실패, 스킵")
            return
        # 기존 SL 주문 찾아서 취소 후 진입가로 재등록
        # Gate.io API는 API로 생성한 TP/SL 주문 수정을 지원하지 않으므로 취소 후 재등록 필요
        # SL 주문 식별: trigger.rule과 진입 방향으로 구분
        # 롱: SL은 rule=2 (가격 <= trigger), TP는 rule=1 (가격 >= trigger)
        # 숏: SL은 rule=1 (가격 >= trigger), TP는 rule=2 (가격 <= trigger)
        sl_updated = False
        expected_sl_rule = 2 if use_side == "long" else 1  # 롱: rule=2, 숏: rule=1
        try:
            orders = self.client.list_price_orders(status="open")
            logger.info("브레이크이븐: 열린 가격 트리거 주문 수=%d 포지션방향=%s 예상_SL_rule=%d (Gate API: rule=1은 >=, rule=2는 <=)", 
                       len(orders) if orders else 0, use_side, expected_sl_rule)
            if not orders:
                logger.warning("브레이크이븐: 열린 가격 트리거 주문이 없습니다")
                return
            # 모든 SL 주문 찾기 (브레이크이븐 후에는 브레이크이븐 SL만 있어야 함)
            sl_order_ids = []
            for o in (orders or []):
                order_type = getattr(o, "order_type", "")
                trigger = getattr(o, "trigger", None)
                # order_type 필터링: SL 주문 타입 확인
                # close-long-order / close-short-order: Order TP/SL
                # close-long-position / close-short-position: Position TP/SL
                # plan-close-long-position / plan-close-short-position: Position plan TP/SL
                is_sl_order_type = False
                if use_side == "long":
                    is_sl_order_type = order_type in ("close-long-order", "close-long-position", "plan-close-long-position", "")
                else:
                    is_sl_order_type = order_type in ("close-short-order", "close-short-position", "plan-close-short-position", "")
                if is_sl_order_type:
                    if trigger and getattr(trigger, "strategy_type", 0) == 0:
                        trigger_rule = getattr(trigger, "rule", None)
                        # SL 주문인지 확인: rule이 일치하는지
                        if trigger_rule == expected_sl_rule:
                            sl_order_id = str(o.id)
                            trigger_price = getattr(trigger, "price", None)
                            sl_order_ids.append(sl_order_id)
                            logger.info("브레이크이븐: SL 주문 발견 order_id=%s order_type=%s 현재_trigger_price=%s 실제_진입가=%s", 
                                       sl_order_id, order_type, trigger_price, actual_entry_price)
            if sl_order_ids:
                # 브레이크이븐 진행 중 플래그 설정 (중복 실행 방지)
                self._breakeven_in_progress = True
                # SL 주문 취소 전에 조건 재확인 (가격 변동 대비)
                # 조건이 성립하지 않으면 원래 SL 주문을 유지해야 함
                last_price_recheck = self.client.get_last_price()
                if last_price_recheck is None:
                    logger.warning("브레이크이븐: SL 재등록 직전 현재가 조회 실패, 원래 SL 주문 유지")
                    self._breakeven_in_progress = False  # 플래그 해제
                    return
                # Gate API 규칙 재확인: 롱 SL은 trigger < last_price, 숏 SL은 trigger > last_price
                can_register = False
                if use_side == "long" and actual_entry_price < last_price_recheck:
                    can_register = True
                elif use_side == "short" and actual_entry_price > last_price_recheck:
                    can_register = True
                if not can_register:
                    logger.warning("브레이크이븐: SL 재등록 직전 조건 재확인 실패 (entry=%.2f last=%.2f) → 원래 SL 주문 유지, 다음 사이클에서 재시도", 
                                 actual_entry_price, last_price_recheck)
                    self._breakeven_in_progress = False  # 플래그 해제
                    return
                # 조건이 성립하면 기존 SL 주문 취소 후 브레이크이븐 SL 재등록
                try:
                    # 모든 기존 SL 주문 취소
                    cancelled_count = 0
                    for sl_order_id in sl_order_ids:
                        try:
                            self.client.cancel_price_order(sl_order_id)
                            cancelled_count += 1
                            logger.info("브레이크이븐: 기존 SL 주문 취소 완료 order_id=%s", sl_order_id)
                        except Exception as e:
                            logger.warning("브레이크이븐: SL 주문 취소 실패 order_id=%s: %s", sl_order_id, e)
                    if cancelled_count > 0:
                        # 거래소가 취소를 반영할 때까지 대기 (미반영 시 기존 SL + 새 SL = 2개로 인식되어 실패)
                        time.sleep(1.5)
                        # 취소된 주문이 열린 목록에서 사라졌는지 확인 후 재등록
                        cancelled_ids = set(sl_order_ids)
                        for attempt in range(6):
                            open_orders = self.client.list_price_orders(status="open")
                            open_ids = {str(getattr(o, "id", "")) for o in (open_orders or [])}
                            if not cancelled_ids & open_ids:
                                break
                            logger.info("브레이크이븐: 취소 반영 대기 중 (남은 SL id=%s)", cancelled_ids & open_ids)
                            time.sleep(0.5)
                        if cancelled_ids & open_ids:
                            logger.warning("브레이크이븐: 취소 반영 지연, 재등록 시도 (거래소가 곧 반영할 수 있음)")
                        # 재등록 직전에 포지션 수량 확인
                        pos_recheck = self.client.get_position()
                        if not pos_recheck:
                            logger.warning("브레이크이븐: SL 재등록 직전 포지션 조회 실패, 다음 사이클에서 재시도")
                            self._breakeven_in_progress = False  # 플래그 해제
                            return
                        actual_size_recheck = abs(pos_recheck["size"])
                        if actual_size_recheck < 1:
                            logger.warning("브레이크이븐: 포지션 수량이 0 이하 (size=%.2f), SL 재등록 불가", actual_size_recheck)
                            self._breakeven_in_progress = False  # 플래그 해제
                            return
                        # 브레이크이븐 SL 수량 = 현재 포지션 수량
                        # SL과 TP는 독립적으로 작동하므로, 각각 포지션 수량을 사용할 수 있음
                        # Gate.io는 각 reduce_only 주문이 독립적으로 포지션 수량을 초과하지 않도록 검증함
                        size_recheck = str(int(actual_size_recheck))
                        logger.info("브레이크이븐: SL 수량 계산 (현재 포지션 수량=%s)", size_recheck)
                        
                        # 브레이크이븐 SL 가격 조정: Gate.io 규칙 준수
                        # 롱 SL: trigger_price < last_price (현재가보다 낮아야 함)
                        # 숏 SL: trigger_price > last_price (현재가보다 높아야 함)
                        # 진입가가 현재가와 같거나 규칙 위반 시 현재가 기준으로 조정
                        # 반올림 후에도 규칙을 만족하도록 충분한 차이 확보
                        price_tick = getattr(config, "PRICE_TICK", 1)
                        breakeven_sl_price = actual_entry_price
                        
                        # 재등록 직전 현재가 재확인 (가격 변동 대비)
                        last_price_final = self.client.get_last_price()
                        if last_price_final is not None:
                            # 현재가를 반올림한 값 계산 (Gate.io API가 비교할 때 사용할 수 있는 값)
                            rounded_last_price = self.client._round_trigger_price(last_price_final)
                            
                            if use_side == "long":
                                # 롱: 반올림된 현재가보다 낮게 설정 (최소 2틱 차이 확보)
                                # 예: 현재가 78118.10 → 반올림 78118 → SL 78116 (2틱 차이)
                                if breakeven_sl_price >= rounded_last_price:
                                    breakeven_sl_price = rounded_last_price - (price_tick * 2)
                                    logger.info("브레이크이븐: 롱 SL 가격 조정 (진입가=%.2f >= 반올림현재가=%.2f) → %.2f", 
                                              actual_entry_price, rounded_last_price, breakeven_sl_price)
                            else:  # short
                                # 숏: 반올림된 현재가보다 높게 설정 (최소 2틱 차이 확보)
                                # 예: 현재가 78118.10 → 반올림 78118 → SL 78120 (2틱 차이)
                                if breakeven_sl_price <= rounded_last_price:
                                    breakeven_sl_price = rounded_last_price + (price_tick * 2)
                                    logger.info("브레이크이븐: 숏 SL 가격 조정 (진입가=%.2f <= 반올림현재가=%.2f) → %.2f", 
                                              actual_entry_price, rounded_last_price, breakeven_sl_price)
                        
                        # 실제 진입가로 브레이크이븐 SL 재등록 (1개만)
                        # create_stop_loss_order 내부에서 가격 반올림을 수행하므로, 실제 등록된 가격은 반올림된 값
                        entry_price_str = str(breakeven_sl_price)
                        try:
                            logger.info("브레이크이븐: SL 주문 등록 시도 side=%s trigger_price=%s size=%s", 
                                       use_side, entry_price_str, size_recheck)
                            self.client.create_stop_loss_order(entry_price_str, size_recheck, use_side)
                            # 실제 등록된 가격 확인 (반올림된 값)
                            rounded_entry_price = self.client._round_trigger_price(float(breakeven_sl_price))
                            sl_updated = True
                            self._breakeven_done = True
                            self._breakeven_in_progress = False  # 진행 중 플래그 해제
                            logger.info("브레이크이븐 완료: SL을 %.2f(원래진입가=%.2f, 반올림=%s)로 이동 완료", 
                                      breakeven_sl_price, actual_entry_price, rounded_entry_price)
                            # Discord: 브레이크이븐 동작 알림
                            notify_discord(
                                "breakeven_applied",
                                side=use_side,
                                entry_price=actual_entry_price,
                            )
                        except Exception as e:
                            logger.error("브레이크이븐: SL 재등록 실패 side=%s trigger_price=%s size=%s 오류=%s", 
                                       use_side, entry_price_str, size_recheck, e)
                            self._breakeven_in_progress = False  # 플래그 해제
                            # 재등록 실패해도 원래 SL은 이미 취소되었으므로, 다음 사이클에서 재시도
                            # (다음 사이클에서 조건이 성립하면 재등록 시도)
                except Exception as e:
                    logger.exception("브레이크이븐: SL 취소/재등록 중 오류: %s", e)
                    self._breakeven_in_progress = False  # 플래그 해제
        except Exception as e:
            logger.exception("브레이크이븐: SL 주문 조회/취소 중 오류: %s", e)
            self._breakeven_in_progress = False  # 플래그 해제

    def run_loop(self, interval_sec: float = 60.0):
        """주기적으로 run_once 호출. 포지션 확인은 매번, 진입 체크는 별도 스레드에서 30분 봉 완성 시점에만 실행.
        브레이크이븐 체크는 별도 스레드에서 1초마다 실행."""
        logger.info("엔진 주기 시작: interval_sec=%.1f, contract=%s, 진입 체크는 별도 스레드(30분 봉 완성 시점 감지)", interval_sec, config.CONTRACT)
        # 브레이크이븐 체크 스레드 시작 (1초 주기)
        self._stop_breakeven_thread = False
        self._breakeven_thread = threading.Thread(target=self._breakeven_check_loop, daemon=True)
        self._breakeven_thread.start()
        # 진입 조건 체크 스레드 시작 (2초 주기로 봉 완성 감지 → 지표 계산 → 진입 체크)
        self._stop_entry_check_thread = False
        self._entry_check_thread = threading.Thread(target=self._entry_check_loop, daemon=True)
        self._entry_check_thread.start()
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.exception("run_once 오류: %s", e)
            time.sleep(interval_sec)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    engine = TradingEngine()
    engine.run_loop(interval_sec=60.0)


if __name__ == "__main__":
    main()
