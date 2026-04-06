"""
Proactive outreach — scheduled messages sent to guests without them texting first.

Triggered by:
  1. Staff check-in form  → welcome message (called from admin.py)
  2. QR room scan         → welcome message (called from app.py)
  3. Hourly scheduler     → pre-checkout reminder + post-stay feedback request
"""

import logging
import os
import requests
from datetime import datetime, date, timedelta

from twilio.rest import Client

from db import (
    get_stays_needing_outreach,
    mark_checkout_reminder_sent,
    mark_post_stay_sent,
    log_message,
)

logger = logging.getLogger("hotel-concierge")

# Formats we accept for check_out_date (datetime-local yields %Y-%m-%dT%H:%M)
_CHECKOUT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_checkout(value: str) -> datetime | None:
    """Parse a checkout date/datetime string into a datetime object."""
    for fmt in _CHECKOUT_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _twilio_client():
    sid = os.getenv("TWILIO_SID")
    token = os.getenv("TWILIO_TOKEN")
    if not sid or not token:
        return None
    return Client(sid, token)


def _is_line_guest(phone: str) -> bool:
    return isinstance(phone, str) and phone.startswith("line:")


def _line_user_id(phone: str) -> str:
    """Strip the 'line:' prefix to get the raw LINE user ID."""
    return phone[5:]


def _send_line_push(line_user_id: str, body: str, channel_token: str) -> bool:
    """Send a proactive (push) message to a LINE user."""
    if not line_user_id or not channel_token:
        return False
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {channel_token}",
                "Content-Type": "application/json",
            },
            json={
                "to": line_user_id,
                "messages": [{"type": "text", "text": body}],
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("line_push_failed", extra={"status": resp.status_code, "to": line_user_id})
            return False
        return True
    except Exception as exc:
        logger.error("line_push_exception", extra={"error": str(exc)})
        return False


def _send_sms(to: str, from_: str, body: str) -> bool:
    client = _twilio_client()
    if not client:
        logger.warning("outreach_skipped_no_twilio", extra={"to": to})
        return False
    try:
        client.messages.create(to=to, from_=from_, body=body)
        return True
    except Exception as exc:
        logger.error("outreach_sms_failed", extra={"to": to, "error": str(exc)})
        return False


def send_welcome(stay_id: int, room_number: str, hotel_name: str,
                 from_number: str, to_number: str, line_token: str = None) -> None:
    """Send a welcome message immediately after check-in (SMS or LINE)."""
    room_str = f"Room {room_number}" if room_number else "your room"
    body = (
        f"Welcome to {hotel_name}! You're all set in {room_str}. "
        f"Text us anytime — we're here 24/7 for anything you need during your stay."
    )
    if _is_line_guest(to_number) and line_token:
        sent = _send_line_push(_line_user_id(to_number), body, line_token)
    else:
        sent = _send_sms(to_number, from_number, body)
    if sent:
        log_message(stay_id, "outbound", body, source="system")
        logger.info("welcome_sent", extra={"stay_id": stay_id, "room": room_number})


def run_scheduled_outreach() -> None:
    """
    Check all hotels for stays needing outreach and send messages.
    Designed to be called hourly by APScheduler.

    Pre-checkout reminder: fires if checkout is within the next 18–30 hours
    (catches the morning-of-the-day-before window regardless of exact checkout time).

    Post-stay message: fires once the checkout datetime has passed.
    """
    now = datetime.utcnow()
    reminder_start = now + timedelta(hours=18)
    reminder_end   = now + timedelta(hours=30)

    stays = get_stays_needing_outreach()

    for s in stays:
        stay_id     = s["stay_id"]
        guest_phone = s["guest_phone"]
        hotel_phone = s["hotel_phone"]
        hotel_name  = s["hotel_name"]
        line_token  = s.get("hotel_line_token")
        room        = s["room_number"]
        room_str    = f"Room {room}" if room else "your room"
        is_line     = _is_line_guest(guest_phone)

        checkout_dt = _parse_checkout(s["check_out_date"])
        if not checkout_dt:
            logger.warning("outreach_bad_date", extra={
                "stay_id": stay_id, "value": s["check_out_date"]
            })
            continue

        # Pre-checkout reminder
        if not s["checkout_reminder_sent_at"] and reminder_start <= checkout_dt <= reminder_end:
            checkout_display = checkout_dt.strftime("%-I:%M %p") if "%H" in s["check_out_date"] else "tomorrow"
            body = (
                f"Hi! Just a reminder that checkout from {room_str} is tomorrow"
                f"{' at ' + checkout_display if checkout_display != 'tomorrow' else ''}. "
                f"Need a late checkout or anything else before you go? Just reply here."
            )
            if is_line and line_token:
                sent = _send_line_push(_line_user_id(guest_phone), body, line_token)
            else:
                sent = _send_sms(guest_phone, hotel_phone, body)
            if sent:
                log_message(stay_id, "outbound", body, source="system")
                mark_checkout_reminder_sent(stay_id)
                logger.info("checkout_reminder_sent", extra={"stay_id": stay_id})

        # Post-stay feedback — fires once checkout time has passed
        if not s["post_stay_sent_at"] and checkout_dt <= now:
            body = (
                f"Thanks for staying at {hotel_name}! We hope you had a great visit. "
                f"How was your stay? Reply with a number from 1 (poor) to 5 (excellent)."
            )
            if is_line and line_token:
                sent = _send_line_push(_line_user_id(guest_phone), body, line_token)
            else:
                sent = _send_sms(guest_phone, hotel_phone, body)
            if sent:
                log_message(stay_id, "outbound", body, source="system")
                mark_post_stay_sent(stay_id)
                logger.info("post_stay_sent", extra={"stay_id": stay_id})
