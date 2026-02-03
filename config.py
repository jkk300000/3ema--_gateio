"""
EMA 캔들패턴 전략 설정.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Gate.io API
GATE_API_KEY = os.getenv("GATE_API_KEY", "")
GATE_API_SECRET = os.getenv("GATE_API_SECRET", "")
GATE_API_BASE = os.getenv("GATE_API_BASE", "https://api.gateio.ws/api/v4")
# 선물 전용 Base (캔들/주문 등)
GATE_FUTURES_BASE = os.getenv("GATE_FUTURES_BASE", "https://fx-api.gateio.ws/api/v4")

# 거래
SETTLE = "usdt"
CONTRACT = "BTC_USDT"
# 투입 자본: 선물 계정 전체 자본의 비율 (95%)
EQUITY_PCT = 95.0
CANDLE_INTERVAL = "30m"
CHART_BAR_MINUTES = 30
# 지표 계산용 캔들 개수 (많을수록 EMA100 등이 차트와 유사, API 1회당 조회)
CANDLE_LIMIT = 500
# BTC_USDT 선물: 1계약 = 0.0001 BTC (API에서 get_futures_contract로 확인 가능)
CONTRACT_MULTIPLIER = 0.0001
ORDER_SIZE_MIN = 1
# 가격 트리거(TP/SL) 단위: Gate API는 trigger.price가 이 값의 정수배여야 함 (BTC_USDT 선물은 1)
PRICE_TICK = 1

# EMA
EMA_20_LEN = 20
EMA_50_LEN = 50
EMA_100_LEN = 100

# 캔들 패턴
BODY_RATIO_MAX = 0.2
HAMMER_LOWER_MIN = 2.0
UPPER_WICK_MAX = 0.3
INVERTED_DOJI_UPPER_WICK_MIN = 0.4

# ATR / SL / TP
ATR_PERIOD = 14
SL_ATR_MULT = 3.0
RR_RATIO = 6.6
MAX_SL_PCT = 1.4
MAX_LEVERAGE = 10.0
MAX_RISK_PCT = 4.2

# 부분 익절 (진입가 대비 최종 TP %의 비율)
ENABLE_PARTIAL_TP = True
TP1_RATIO_OF_TP = 35.0   # TP1 = fullTpPct * 35%
TP1_CLOSE_PCT = 15.0     # 포지션의 15% 청산
TP2_RATIO_OF_TP = 40.0
TP2_CLOSE_PCT = 15.0     # 남은 포지션의 15%

# 브레이크이븐 (시간 기준: 진입 후 N시간 경과 시 손절가를 진입가로 이동)
ENABLE_BREAKEVEN = True
# BREAKEVEN_HOURS = 250.0  # 진입 후 250시간 경과 시 브레이크이븐 (기존 500봉×30분 ≈ 250시간과 동일)
BREAKEVEN_HOURS = 0.01
# 3연속 손절 스킵
ENABLE_SKIP_AFTER_3_LOSSES = True

# 캐시: 30봉 완성 직후에만 진입 체크
ENTRY_CHECK_ONLY_ON_BAR_CLOSE = True
# 포지션 확인: 진입 신호 발생 후에만 확인 시작, 진입 확인되면 중단, 청산 전까지
POSITION_CHECK_AFTER_SIGNAL_ONLY = True

# --- 테스트용: 진입/청산 메소드만 검증할 때 ---
# True면 진입 조건(EMA·캔들패턴) 무시하고, 완성된 30봉마다 지정 방향으로 최소 수량(1계약) 진입
TEST_MODE = True
TEST_ENTRY_OVERRIDE = True   # TEST_MODE일 때만 의미 있음 (둘 다 True여야 진입 조건 무시)
TEST_ENTRY_SIDE = "long"     # "long" 또는 "short"
