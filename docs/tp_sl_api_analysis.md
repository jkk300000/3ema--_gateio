# Gate.io TP/SL API 분석: Create price-triggered order 사용 여부

## 1. 현재 구현

포지션 진입 확인 후 TP/SL 설정 시 **동일한 API**를 사용하고 있음:

| 기능 | 호출 경로 | 실제 API |
|------|-----------|----------|
| Take profit | `client.create_take_profit_order(...)` | `api.create_price_triggered_order(settle, order)` |
| Stop loss | `client.create_stop_loss_order(...)` | `api.create_price_triggered_order(settle, order)` |

즉, **Take profit / Stop loss 모두 Gate.io의 "Create price-triggered order" 한 종류의 API**를 쓰고 있다.

---

## 2. Gate.io API 구조

### 2.1 선물(Futures) 주문 관련 엔드포인트

- **Place Futures Order** (`POST /futures/{settle}/orders`)  
  - 일반 주문(시장가/지정가).  
  - **take_profit / stop_loss 파라미터는 없음.** (스팟의 `POST /spot/orders`에는 v4.105.7에서 stop_loss, take_profit 추가된 것과 구분됨)

- **Create price-triggered order** (`POST /futures/{settle}/price_orders`)  
  - “가격 조건이 만족되면 주문을 넣는” 자동 주문.  
  - **선물에서 TP/SL을 구현하는 공식 수단이 이 API임.**

### 2.2 문서/스키마 상 구분

- TP/SL **전용 엔드포인트**(예: `POST .../take_profit`, `POST .../stop_loss`)는 **선물 API에 없음.**
- “Create price-triggered order” 하나로:
  - **trigger 조건** (가격 + rule: `>=` / `<=`)으로
    - 익절 조건 → Take profit 용도  
    - 손절 조건 → Stop loss 용도  
  를 구분해서 사용하는 구조.

즉, **Take profit 주문 = price-triggered order (trigger가 익절 가격/rule)**  
**Stop loss 주문 = price-triggered order (trigger가 손절 가격/rule)** 이 맞는 사용법이다.

---

## 3. 결론

| 질문 | 답 |
|----------|
| TP/SL 설정에 **Create price-triggered order**를 쓰고 있나? | **예.** 두 기능 모두 `create_price_triggered_order` 호출로 처리하고 있음. |
| Take profit / Stop loss에 **이 API를 쓰는 것이 맞나?** | **예.** Gate.io 선물 API에는 TP/SL 전용 엔드포인트가 없고, **가격 조건부 자동 주문 = price-triggered order**가 TP/SL을 넣는 정식 방법임. |

### 3.1 우리 구현이 API와 맞는 부분

- **Take profit**: `FuturesPriceTrigger`에 `price=tp`, `rule=1(long)` 또는 `rule=2(short)` 로 “해당 가격 도달 시 청산” 조건을 넣고, `FuturesInitialOrder`는 `reduce_only=True` + 청산 방향(sell/buy)으로 설정 → **Create price-triggered order**로 TP 주문 생성. ✅  
- **Stop loss**: 동일하게 `price=sl`, `rule=2(long)` 또는 `rule=1(short)` 로 손절 조건을 넣고, 같은 API로 SL 주문 생성. ✅  

따라서 **포지션 진입 확인 후 TP/SL 설정 시 "Create price-triggered order"만 사용하는 현재 동작은 Gate.io 스펙과 일치하며, take profit / stop loss 모두에 이 함수를 쓰는 것이 맞다.**

---

## 4. 참고

- Spot: `POST /spot/orders`에 `stop_loss`, `take_profit` 파라미터가 있음 (v4.105.7).  
- **Futures**: 그런 필드는 없고, TP/SL은 **반드시** `POST /futures/{settle}/price_orders` (Create price-triggered order)로만 설정 가능.

---

## 5. Gate API v4 Futures 문서 기준 점검 (2026-02)

출처: [Gate API v4 – Futures](https://www.gate.com/docs/developers/apiv4/en/#futures)

### 5.1 Get Futures Account (`GET /futures/{settle}/accounts`)

- **v4.106.3**: `total` 필드는 **classic 선물 계정 전용**. Unified 계정에서는 `equity` 사용.
- **수정**: 계정 잔고를 쓸 때 `equity`가 있으면 `equity`, 없으면 `total` 사용하도록 `engine.py` 및 `gate_client._log_connection_once` 수정함.

### 5.2 Create Price-Triggered Order (`POST /futures/{settle}/price_orders`)

- **v4.105.10**: `price`, `rule` 필드는 **필수**.
- **FuturesInitialOrder**: 트리거 시 실행할 주문에 `side`(buy/sell) 지정 필요. 미설정 시 오류 가능.
- **수정**: TP/SL 생성 시 `FuturesInitialOrder.side`를 `setattr(initial, "side", order_side)`로 항상 설정하도록 `gate_client.py` 수정함.

### 5.3 Size 타입 (v4.106.0 / v4.106.19)

- Futures 관련 `size` 필드는 **문자열(string)**.
- **v4.106.19**: Contract에 `enable_decimal` 필드 추가. `false`이면 size는 **정수 문자열**만 허용.
- 현재 구현: `_position_size`에서 `int(size_contracts)`로 정수 계약 수 사용, TP/SL에서도 `str(int(size))` 사용 → 정수 문자열 전송으로 문서와 일치.
