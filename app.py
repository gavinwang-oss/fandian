import json
import logging
import os
import re
import requests
from flask import Flask, request, redirect, url_for, render_template, session, abort
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from werkzeug.security import check_password_hash

from auth import load_current_user, login_user, logout_user
from config import Config
from llm_utils import embed_text
from vector_index import get_vector_index
from db import (
    init_db,
    get_hotel_id_for_number,
    get_or_create_guest,
    get_or_create_active_stay,
    log_message,
    get_hotel_info,
    get_hotel,
    create_task,
    list_hotel_docs,
    update_hotel_doc_embedding,
    list_recent_messages_for_stay,
    is_opted_out,
    set_opted_out,
    log_inbound,
    is_rate_limited,
    get_staff_user_by_email,
    set_stay_room_number,
    mark_welcome_sent,
)
from admin import admin_bp
from outreach import run_scheduled_outreach, send_welcome as _send_welcome_sms

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config["SECRET_KEY"]
app.register_blueprint(admin_bp)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hotel-concierge")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(run_scheduled_outreach, "interval", hours=1, id="outreach")
    _scheduler.start()
    logger.info("outreach_scheduler_started")
except ImportError:
    logger.warning("apscheduler_not_installed — scheduled outreach disabled")

if app.config.get("APP_ENV") == "production" and app.secret_key == "dev-secret-key":
    raise RuntimeError("FLASK_SECRET_KEY must be set in production")


@app.before_request
def _load_user():
    load_current_user()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        user = get_staff_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            login_user(user)
            logger.info("auth_login", extra={"email": email})
            return redirect(url_for("admin.admin_messages"))
        logger.info("auth_login_failed", extra={"email": email})
        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    user_email = session.get("email")
    logout_user()
    logger.info("auth_logout", extra={"email": user_email})
    return redirect(url_for("login"))


@app.route("/sms", methods=["POST"])
def sms_reply():
    if not app.config.get("DISABLE_TWILIO_VALIDATION"):
        validator = RequestValidator(app.config["TWILIO_TOKEN"])
        signature = request.headers.get("X-Twilio-Signature", "")
        url = request.url
        post_data = request.form.to_dict()
        if not validator.validate(url, post_data, signature):
            logger.warning("twilio_signature_invalid", extra={"url": url})
            abort(403)

    try:
        from_number = request.values.get("From", "")
        to_number = request.values.get("To", "")
        body = request.values.get("Body", "")

        hotel_id = get_hotel_id_for_number(to_number)
        if not hotel_id:
            logger.warning("unknown_hotel_number", extra={"to": to_number})
            resp = MessagingResponse()
            resp.message("Sorry, this number is not configured.")
            return str(resp)

        guest_id = get_or_create_guest(from_number)
        logger.info("inbound_sms", extra={"guest_id": guest_id, "hotel_id": hotel_id})

        stop_keywords = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
        help_keyword = "help"
        text = body.strip().lower()

        if is_rate_limited(
            guest_id,
            hotel_id,
            app.config["RATE_LIMIT_WINDOW_SECONDS"],
            app.config["RATE_LIMIT_COUNT"],
        ):
            logger.info("rate_limited", extra={"guest_id": guest_id, "hotel_id": hotel_id})
            return str(MessagingResponse())

        log_inbound(guest_id, hotel_id)

        stay_id = get_or_create_active_stay(guest_id, hotel_id)
        inbound_id = log_message(stay_id, "inbound", body, source="guest")

        if text in stop_keywords:
            set_opted_out(guest_id, hotel_id, True)
            logger.info("opt_out", extra={"guest_id": guest_id, "hotel_id": hotel_id})
            reply_text = app.config["STOP_RESPONSE"]
            log_message(stay_id, "outbound", reply_text, source="system")
            resp = MessagingResponse()
            resp.message(reply_text)
            return str(resp)

        if text == help_keyword:
            reply_text = app.config["HELP_RESPONSE"]
            log_message(stay_id, "outbound", reply_text, source="system")
            resp = MessagingResponse()
            resp.message(reply_text)
            return str(resp)

        if is_opted_out(guest_id, hotel_id):
            logger.info("opted_out_suppressed", extra={"guest_id": guest_id, "hotel_id": hotel_id})
            return str(MessagingResponse())

        hotel_info = get_hotel_info(hotel_id)

        # Room check-in via QR code: "Room 402" → set room number, send welcome
        room_number = _parse_room_number(body)
        if room_number:
            set_stay_room_number(hotel_id, stay_id, room_number)
            hotel = get_hotel(hotel_id)
            hotel_name = hotel_info.get("hotel_name") or (hotel["name"] if hotel else "the hotel")
            from_number = hotel["phone_number"] if hotel else app.config.get("TWILIO_NUMBER", "")
            reply_text = (
                f"Welcome to {hotel_name}! You're all set in Room {room_number}. "
                f"Text us anytime — we're here 24/7 for anything you need during your stay."
            )
            log_message(stay_id, "outbound", reply_text, source="ai")
            mark_welcome_sent(stay_id)
            logger.info("room_checkin", extra={"stay_id": stay_id, "room": room_number})
            resp = MessagingResponse()
            resp.message(reply_text)
            return str(resp)

        reply_text = route_message(body, hotel_info, hotel_id, stay_id, inbound_id)

        log_message(stay_id, "outbound", reply_text, source="ai")
        resp = MessagingResponse()
        resp.message(reply_text)
        return str(resp)
    except Exception as exc:
        logger.exception("sms_webhook_error", extra={"error": str(exc)})
        return str(MessagingResponse())


_ROOM_RE = re.compile(r'^room\s+(\S+)$', re.IGNORECASE)


def _parse_room_number(body: str) -> str | None:
    """Return the room identifier if the message is a QR-code room check-in signal."""
    m = _ROOM_RE.match(body.strip())
    return m.group(1) if m else None


def route_message(body: str, hotel_info: dict, hotel_id: int, stay_id: int, inbound_message_id: int) -> str:
    # LLM decision: reply vs task
    decision = decide_action_llm(body, hotel_info, stay_id, hotel_id)
    if decision:
        action = decision.get("action")
        if action == "task":
            summary = decision.get("task_summary") or body
            department = decision.get("department") or "frontdesk"
            create_task(
                stay_id,
                "general_request",
                created_from_message_id=inbound_message_id,
                summary=summary,
                department=department,
            )
            logger.info("task_created", extra={"type": "general_request", "stay_id": stay_id})
            return decision.get("reply") or "Got it — I’ve passed this to staff and will update you shortly."
        if action == "reply":
            reply = decision.get("reply")
            if reply:
                return reply

    # Fallback (LLM with hotel info + knowledge snippets)
    llm_reply = llm_fallback(body, hotel_info, hotel_id, stay_id)
    if llm_reply:
        return llm_reply

    return (
        "I can help with towels, water, late checkout, valet, and hotel info like "
        "breakfast hours, pool hours, gym hours, and Wi‑Fi. How can I help?"
    )


def llm_fallback(user_message: str, hotel_info: dict, hotel_id: int, stay_id: int) -> str | None:
    api_key = app.config["OPENAI_API_KEY"]
    if not api_key:
        return None

    model = app.config["OPENAI_MODEL"]

    instructions = (
        "You are an SMS concierge for a hotel. Answer concisely (1-3 sentences). "
        "Use the provided hotel info and knowledge snippets when relevant, but you may also use general knowledge. "
        "Never invent hotel policies, hours, pricing, or availability. If the answer depends on hotel-specific info "
        "that is not provided, say you'll check with staff or ask a brief clarifying question. "
        "If the guest asks for something that requires staff (towels, water, valet, late checkout, complaints), "
        "acknowledge and say staff will assist. If you don't know, ask a brief clarifying question or say you'll connect them to staff."
    )

    info_lines = "\n".join([f"- {k}: {v}" for k, v in hotel_info.items()])
    recent = list_recent_messages_for_stay(stay_id, 6)
    recent_text = "\n".join([f"{r['direction']}: {r['body']}" for r in reversed(recent)])
    knowledge = retrieve_knowledge_snippets(user_message, hotel_id)
    prompt = (
        f"Hotel info:\n{info_lines}\n\n"
        f"Knowledge snippets:\n{knowledge}\n\n"
        f"Recent conversation:\n{recent_text}\n\n"
        f"Guest message: {user_message}\nAnswer:"
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": instructions,
                "input": prompt,
                "temperature": 0.2,
                "max_output_tokens": 200,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.error("llm_error", extra={"status": resp.status_code, "body": resp.text})
            return None

        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "").strip()
                        if text:
                            return text
        return None
    except Exception as exc:
        logger.exception("llm_exception", extra={"error": str(exc)})
        return None


def retrieve_knowledge_snippets(query: str, hotel_id: int) -> str:
    docs = list_hotel_docs(hotel_id, 200)
    if not docs:
        return "None"

    idx = get_vector_index(hotel_id)
    if idx:
        results = idx.query(query, k=3)
        if results:
            top_docs = [d for score, d in results if score > 0.2]
            if top_docs:
                return _format_snippets(top_docs)

    query_vec = embed_text(query)
    if query_vec:
        scored = []
        for d in docs:
            vec = None
            if d["embedding_json"]:
                try:
                    vec = json.loads(d["embedding_json"])
                except Exception:
                    vec = None
            if not vec:
                vec = embed_text(f"{d['title']}\n{d['content']}")
                if vec:
                    update_hotel_doc_embedding(d["id"], json.dumps(vec))
            if vec:
                score = cosine_similarity(query_vec, vec)
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_docs = [d for score, d in scored[:3] if score > 0.2]
        if not top_docs:
            return "None"
    else:
        query_terms = {t for t in _tokenize(query) if len(t) > 2}
        scored = []
        for d in docs:
            content = f"{d['title']} {d['content']}"
            terms = {t for t in _tokenize(content) if len(t) > 2}
            score = len(query_terms.intersection(terms))
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_docs = [d for _, d in scored[:3]]
        if not top_docs:
            return "None"

    return _format_snippets(top_docs)


def _tokenize(text: str):
    return [t.strip(".,!?;:()[]{}\"'").lower() for t in text.split()]


def _format_snippets(docs) -> str:
    lines = []
    for d in docs:
        snippet = d["content"].strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        lines.append(f"- {d['title']}: {snippet}")
    return "\n".join(lines) if lines else "None"


def is_hotel_specific_question(text: str) -> bool:
    keywords = [
        "check-in", "check in", "checkin", "check-out", "check out", "checkout",
        "breakfast", "pool", "gym", "spa", "wifi", "wi-fi", "parking",
        "front desk", "frontdesk", "policy", "fee", "price", "availability",
        "late checkout", "room service", "amenities", "housekeeping"
    ]
    return any(k in text for k in keywords)


def has_hotel_data(text: str, hotel_info: dict, hotel_id: int) -> bool:
    info_keys = set(hotel_info.keys())
    if "breakfast" in text and "breakfast_hours" in info_keys:
        return True
    if "pool" in text and "pool_hours" in info_keys:
        return True
    if "gym" in text and "gym_hours" in info_keys:
        return True
    if ("check in" in text or "checkin" in text) and "checkin_time" in info_keys:
        return True
    if ("check out" in text or "checkout" in text) and "checkout_time" in info_keys:
        return True
    if ("wifi" in text or "wi-fi" in text) and "wifi_info" in info_keys:
        return True
    if "parking" in text and "parking_info" in info_keys:
        return True
    if "front desk" in text and "front_desk_hours" in info_keys:
        return True
    snippets = retrieve_knowledge_snippets(text, hotel_id)
    return snippets != "None"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _parse_llm_json(text: str) -> dict | None:
    """Strip markdown fences, parse JSON, and validate required keys."""
    # Strip ```json ... ``` or ``` ... ``` fences
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first and last fence lines
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.error("llm_json_parse_error", extra={"error": str(exc), "raw": text[:200]})
        return None

    if not isinstance(parsed, dict):
        logger.error("llm_json_not_dict", extra={"raw": text[:200]})
        return None

    missing = [k for k in ("action", "reply") if k not in parsed]
    if missing:
        logger.error("llm_json_missing_keys", extra={"missing": missing, "raw": text[:200]})
        return None

    return parsed


def decide_action_llm(user_message: str, hotel_info: dict, stay_id: int, hotel_id: int) -> dict | None:
    api_key = app.config["OPENAI_API_KEY"]
    if not api_key:
        return None
    model = app.config["OPENAI_MODEL"]
    instructions = (
        "You are a hotel AI concierge. Classify the guest message into one of two actions:\n"
        "1. action=reply — the guest is asking about hotel policies, services, or general information "
        "and is NOT making a specific request or order. Answer directly using hotel info or general knowledge.\n"
        "2. action=task — the guest needs something done by hotel staff. Use this for any real, actionable "
        "service request including: bring towels/amenities, fix AC or maintenance issues, room service food orders, "
        "billing concerns or disputes, complaints about the room requiring staff attention, booking requests (spa, taxi, dinner), "
        "valet, late checkout if explicitly requested, or anything requiring physical staff action.\n\n"
        "Key distinctions:\n"
        "- 'Can I get a late checkout?' → task (clear request even if phrased as a question)\n"
        "- 'What is your late checkout policy?' → reply (info question only)\n"
        "- 'Do you have room service?' → reply (info question)\n"
        "- 'Can I get a burger and fries?' → task (food order)\n"
        "- 'I have a question about my bill' → task (billing issue needs staff)\n"
        "- 'I'm not happy with my room' → task (complaint needs staff follow-up)\n\n"
        "NEVER create a task for: greetings, thanks, acknowledgments, questions about staff names, "
        "meta questions about the AI, jokes, gibberish, or anything not a real hotel request.\n"
        "Do NOT invent hotel policies or hours not provided.\n"
        "Always include a polite reply. Respond with ONLY valid JSON."
    )
    schema = {
        "action": "reply | task",
        "reply": "string",
        "task_summary": "string or null",
        "department": "housekeeping | frontdesk | concierge | valet | maintenance | null",
    }
    info_lines = "\n".join([f"- {k}: {v}" for k, v in hotel_info.items()])
    recent = list_recent_messages_for_stay(stay_id, 6)
    recent_text = "\n".join([f"{r['direction']}: {r['body']}" for r in reversed(recent)])
    knowledge = retrieve_knowledge_snippets(user_message, hotel_id)
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": instructions,
                "input": (
                    f"Schema: {schema}\nHotel info:\n{info_lines}\n\nKnowledge snippets:\n{knowledge}\n\nRecent conversation:\n{recent_text}\n\nMessage: {user_message}"
                ),
                "temperature": 0.0,
                "max_output_tokens": 120,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.error("llm_decision_error", extra={"status": resp.status_code, "body": resp.text})
            return None
        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "").strip()
                        if text:
                            return _parse_llm_json(text)
        return None
    except Exception as exc:
        logger.exception("llm_decision_exception", extra={"error": str(exc)})
        return None


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
