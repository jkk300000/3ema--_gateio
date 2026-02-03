"""
Discord 웹훅 알림.
.env에 DISCORD_WEBHOOK_URL 설정 시에만 전송.
"""
import logging
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


def _send(content: str, embed: Optional[dict] = None) -> bool:
    url = getattr(config, "DISCORD_WEBHOOK_URL", "") or ""
    if not url or not url.strip().startswith("https://discord.com/api/webhooks/"):
        return False
    payload = {"content": content[:2000]}
    if embed:
        payload["embeds"] = [embed]
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            logger.warning("Discord 웹훅 실패: status=%s body=%s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.debug("Discord 웹훅 오류: %s", e)
        return False


def notify_discord(
    event: str,
    side: Optional[str] = None,
    entry_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    tp1_price: Optional[float] = None,
    tp2_price: Optional[float] = None,
    close_price: Optional[float] = None,
    size: Optional[str] = None,
    extra: Optional[str] = None,
) -> bool:
    """
    event: "entry" | "partial_tp" | "close" | "breakeven_applied" | "breakeven_closed"
    """
    if not getattr(config, "DISCORD_WEBHOOK_URL", ""):
        return False

    def _f(x: Optional[float]) -> str:
        return f"{x:.2f}" if x is not None else "-"

    if event == "entry":
        content = (
            f"**진입** {side or '-'} | 수량 {size or '-'}\n"
            f"진입가: {_f(entry_price)} | 손절가: {_f(sl_price)}\n"
            f"최종 익절가: {_f(tp_price)} | TP1: {_f(tp1_price)} | TP2: {_f(tp2_price)}"
        )
        return _send(content)

    if event == "partial_tp":
        content = f"**부분 익절** {side or '-'} | 익절가: {_f(close_price)} {extra or ''}"
        return _send(content)

    if event == "close":
        content = f"**청산** {side or '-'} | 청산가: {_f(close_price)} {extra or ''}"
        return _send(content)

    if event == "breakeven_applied":
        content = f"**브레이크이븐 동작** {side or '-'} | SL을 진입가 {_f(entry_price)}로 이동 완료"
        return _send(content)

    if event == "breakeven_closed":
        content = f"**브레이크이븐 청산** {side or '-'} | 청산가: {_f(close_price)}"
        return _send(content)

    return False
