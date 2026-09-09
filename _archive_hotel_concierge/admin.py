import json
import logging
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, current_app, jsonify
from twilio.rest import Client

from auth import login_required, role_required
from vector_index import invalidate_vector_index
from translations import get_translations
from db import (
    list_messages_for_hotel,
    list_conversations_for_hotel,
    list_tasks,
    get_task_stats,
    update_task_status,
    update_task_fields,
    list_hotel_info,
    upsert_hotel_info,
    delete_hotel_info,
    list_messages_for_stay,
    list_messages_for_stay_after,
    list_tasks_for_stay,
    get_guest_phone_for_stay,
    log_message,
    add_hotel_doc,
    list_hotel_docs,
    get_hotel_doc,
    update_hotel_doc,
    delete_hotel_doc,
    list_staff_users,
    create_staff_user,
    list_guests_for_hotel,
    get_task,
    is_opted_out,
    get_guest_id_for_stay,
    get_stay,
    set_stay_room_number,
    set_stay_checkout_date,
    get_hotel,
    get_or_create_guest,
    get_or_create_active_stay,
    get_hotel_info,
    mark_welcome_sent,
    list_knowledge_suggestions,
    update_knowledge_suggestion_status,
    get_analytics,
    get_hotel_line_credentials,
    update_hotel_line_credentials,
    update_hotel_staff_language,
    get_dashboard_prefs,
    save_dashboard_prefs,
    dashboard_daily_counts,
    dashboard_ai_resolved_rows,
    dashboard_live,
    dashboard_department,
    dashboard_peak_hours,
    dashboard_top_questions,
    dashboard_unanswered,
)
from werkzeug.security import generate_password_hash

admin_bp = Blueprint("admin", __name__)


@admin_bp.context_processor
def inject_translations():
    lang = session.get("lang", "en")
    return {"t": get_translations(lang), "current_lang": lang}


@admin_bp.route("/set-language", methods=["POST"])
def set_language():
    lang = request.form.get("lang", "en")
    if lang not in ("en", "zh"):
        lang = "en"
    session["lang"] = lang
    next_url = request.form.get("next", "")
    if next_url and next_url.startswith("/admin"):
        return redirect(next_url)
    return redirect(url_for("admin.admin_messages"))


def _relative_time(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return str(value)
    if value.tzinfo is not None:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} min ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    if seconds < 604800:
        d = seconds // 86400
        return f"{d}d ago"
    return value.strftime("%b %d") if hasattr(value, "strftime") else str(value)[:10]


admin_bp.add_app_template_filter(_relative_time, "relative_time")
logger = logging.getLogger("hotel-concierge")


def _ensure_csrf_token():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(16)
    return session["csrf_token"]


def _require_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        abort(403)


@admin_bp.route("/admin/messages")
@login_required
def admin_messages():
    hotel_id = session.get("hotel_id")
    conversations = list_conversations_for_hotel(hotel_id)
    active_stay_id = request.args.get("stay", type=int)
    # auto-select first conversation if none specified
    if not active_stay_id and conversations:
        active_stay_id = conversations[0]["stay_id"]
    thread_rows = []
    thread_tasks = []
    thread_room_number = None
    thread_guest_phone = None
    csrf_token = _ensure_csrf_token()
    thread_check_out_date = None
    if active_stay_id:
        thread_rows = list_messages_for_stay(hotel_id, active_stay_id)
        thread_tasks = list_tasks_for_stay(hotel_id, active_stay_id)
        stay = get_stay(hotel_id, active_stay_id)
        if stay:
            thread_room_number = stay["room_number"]
            thread_check_out_date = stay["check_out_date"]
        if thread_rows:
            thread_guest_phone = thread_rows[0]["guest_phone"]
    return render_template(
        "messages.html",
        conversations=conversations,
        active_stay_id=active_stay_id,
        thread_rows=thread_rows,
        thread_tasks=thread_tasks,
        thread_room_number=thread_room_number,
        thread_guest_phone=thread_guest_phone,
        thread_check_out_date=thread_check_out_date,
        csrf_token=csrf_token,
        title="Messages",
        active_page="messages",
    )


@admin_bp.route("/admin/messages/poll")
@login_required
def admin_messages_poll():
    """Return new messages (after a given id) and updated inbox as JSON."""
    hotel_id = session.get("hotel_id")
    stay_id = request.args.get("stay", type=int)
    after_id = request.args.get("after", type=int, default=0)

    new_msgs = []
    if stay_id:
        rows = list_messages_for_stay_after(hotel_id, stay_id, after_id)
        for r in rows:
            new_msgs.append({
                "id": r["id"],
                "direction": r["direction"],
                "body": r["body"],
                "created_at": _relative_time(r["created_at"]),
            })

    # Also send updated inbox so previews / unread dots refresh
    conversations = list_conversations_for_hotel(hotel_id)
    inbox = []
    for c in conversations:
        inbox.append({
            "stay_id": c["stay_id"],
            "label": f"Room {c['room_number']}" if c["room_number"] else c["guest_phone"],
            "last_body": (c["last_body"] or "")[:60],
            "last_at": _relative_time(c["last_at"]),
            "top_open_priority": c.get("top_open_priority") or "",
            "open_task_count": c.get("open_task_count") or 0,
        })

    return jsonify({"messages": new_msgs, "inbox": inbox})


@admin_bp.route("/admin/tasks", methods=["GET", "POST"])
@login_required
def admin_tasks():
    hotel_id = session.get("hotel_id")
    staff_id = session.get("user_id")

    if request.method == "POST":
        _require_csrf()
        action = request.form.get("action")
        task_id = int(request.form.get("task_id", "0"))
        if action == "status":
            status = request.form.get("status", "open")
            update_task_status(task_id, status, completed_by=staff_id if status == "done" else None)
            logger.info("task_status_change", extra={"task_id": task_id, "status": status})

            task = get_task(task_id)
            if task and status == "done" and task["notify_guest_when_done"]:
                to_number = get_guest_phone_for_stay(hotel_id, task["stay_id"])
                guest_id = get_guest_id_for_stay(hotel_id, task["stay_id"])
                if to_number and guest_id and not is_opted_out(guest_id, hotel_id):
                    _send_sms(to_number, "Your request has been completed. Let us know if you need anything else.")
            next_url = request.form.get("next", "")
            if next_url and next_url.startswith("/admin/"):
                return redirect(next_url)
            return redirect(url_for("admin.admin_tasks"))

        if action == "update":
            assigned_to = request.form.get("assigned_to")
            assigned_to = int(assigned_to) if assigned_to else None
            priority = request.form.get("priority", "normal")
            notify = request.form.get("notify_guest_when_done") == "on"
            update_task_fields(task_id, assigned_to, priority, notify)
            logger.info("task_updated", extra={"task_id": task_id, "assigned_to": assigned_to, "priority": priority})
            return redirect(url_for("admin.admin_tasks"))

    status_filter = request.args.get("status")
    priority_filter = request.args.get("priority")
    assigned_filter = request.args.get("assigned")
    assigned_to = staff_id if assigned_filter == "me" else None

    rows = list_tasks(hotel_id, 200, status=status_filter, assigned_to=assigned_to, priority=priority_filter)
    staff_users = list_staff_users(hotel_id)
    stats = get_task_stats(hotel_id)
    csrf_token = _ensure_csrf_token()

    return render_template(
        "tasks.html",
        rows=rows,
        staff_users=staff_users,
        stats=stats,
        csrf_token=csrf_token,
        title="Tasks",
        active_page="tasks",
        status_filter=status_filter,
        assigned_filter=assigned_filter,
        priority_filter=priority_filter,
    )


@admin_bp.route("/admin/stay/<int:stay_id>", methods=["GET", "POST"])
@login_required
def admin_stay(stay_id: int):
    hotel_id = session.get("hotel_id")

    if request.method == "POST":
        _require_csrf()
        action = request.form.get("action")
        if action == "set_room":
            room_number = (request.form.get("room_number") or "").strip()
            set_stay_room_number(hotel_id, stay_id, room_number or None)
        elif action == "set_checkout":
            date_part = (request.form.get("check_out_date_part") or "").strip()
            time_part = (request.form.get("check_out_time_part") or "").strip()
            if date_part and time_part:
                check_out_date = f"{date_part}T{time_part}"
            elif date_part:
                check_out_date = date_part
            else:
                check_out_date = None
            set_stay_checkout_date(hotel_id, stay_id, check_out_date)
        next_url = request.form.get("next", "")
        if next_url and next_url.startswith("/admin/"):
            return redirect(next_url)
        return redirect(url_for("admin.admin_stay", stay_id=stay_id))

    rows = list_messages_for_stay(hotel_id, stay_id)
    guest_phone = rows[0]["guest_phone"] if rows else ""
    stay = get_stay(hotel_id, stay_id)
    room_number = stay["room_number"] if stay else None
    check_out_date = stay["check_out_date"] if stay else None
    csrf_token = _ensure_csrf_token()
    return render_template(
        "stay.html",
        rows=rows,
        stay_id=stay_id,
        guest_phone=guest_phone,
        room_number=room_number,
        check_out_date=check_out_date,
        csrf_token=csrf_token,
        title=f"Stay #{stay_id}",
        active_page="messages",
    )


@admin_bp.route("/admin/stay/<int:stay_id>/download")
@login_required
def admin_stay_download(stay_id: int):
    from flask import Response
    hotel_id = session.get("hotel_id")
    stay = get_stay(hotel_id, stay_id)
    if not stay:
        abort(404)
    rows = list_messages_for_stay(hotel_id, stay_id)
    hotel = get_hotel(hotel_id)
    hotel_name = hotel["name"] if hotel else "Hotel"
    room = stay.get("room_number") or f"Stay #{stay_id}"

    lines = [f"Conversation Export — {hotel_name}", f"Room: {room}", ""]
    for r in rows:
        direction = r["direction"]
        source = r.get("source", "")
        if direction == "inbound":
            sender = "Guest"
        elif source == "staff":
            sender = "Staff"
        elif source == "system":
            sender = "System"
        else:
            sender = "AI"
        ts = str(r["created_at"])[:16] if r["created_at"] else ""
        lines.append(f"[{ts}] {sender}: {r['body']}")

    content = "\n".join(lines) + "\n"
    filename = f"conversation-stay-{stay_id}.txt"
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@admin_bp.route("/admin/stay/<int:stay_id>/reply", methods=["POST"])
@login_required
def admin_stay_reply(stay_id: int):
    _require_csrf()
    hotel_id = session.get("hotel_id")
    body = (request.form.get("body") or "").strip()
    if not body:
        return redirect(url_for("admin.admin_stay", stay_id=stay_id))

    guest_id = get_guest_id_for_stay(hotel_id, stay_id)
    if guest_id and is_opted_out(guest_id, hotel_id):
        return redirect(url_for("admin.admin_stay", stay_id=stay_id))

    # Find the last guest (inbound) message to use as the "question"
    from db import list_messages_for_stay as _list_msgs
    msgs = _list_msgs(hotel_id, stay_id)
    guest_question = None
    for m in reversed(msgs):
        if m["direction"] == "inbound":
            guest_question = m["body"]
            break

    log_message(stay_id, "outbound", body, source="staff")

    to_number = get_guest_phone_for_stay(hotel_id, stay_id)
    if to_number:
        _send_reply_to_guest(hotel_id, to_number, body)
        logger.info("staff_reply_sent", extra={"stay_id": stay_id})

    # Trigger knowledge suggestion in the background (if we have a question)
    if guest_question:
        try:
            from knowledge_suggest import maybe_suggest_knowledge_entry
            maybe_suggest_knowledge_entry(
                hotel_id=hotel_id,
                stay_id=stay_id,
                guest_question=guest_question,
                staff_answer=body,
                app_context=current_app._get_current_object(),
            )
        except Exception as exc:
            logger.warning("knowledge_suggest_init_error", extra={"error": str(exc)})

    next_url = request.form.get("next", "")
    if next_url and next_url.startswith("/admin/"):
        return redirect(next_url)
    return redirect(url_for("admin.admin_stay", stay_id=stay_id))


@admin_bp.route("/admin/checkin", methods=["GET", "POST"])
@login_required
def admin_checkin():
    hotel_id = session.get("hotel_id")
    csrf_token = _ensure_csrf_token()
    error = None
    success = None

    if request.method == "POST":
        _require_csrf()
        raw_phone = (request.form.get("phone") or "").strip()
        room_number = (request.form.get("room_number") or "").strip()
        date_part = (request.form.get("check_out_date_part") or "").strip()
        time_part = (request.form.get("check_out_time_part") or "").strip()
        if date_part and time_part:
            check_out_date = f"{date_part}T{time_part}"
        elif date_part:
            check_out_date = date_part
        else:
            check_out_date = None

        # Normalize phone to E.164 using phonenumbers library
        # Derive default region from hotel timezone
        _tz_to_region = {
            "Asia/Taipei": "TW", "Asia/Tokyo": "JP", "Asia/Seoul": "KR",
            "Asia/Bangkok": "TH", "Asia/Singapore": "SG", "Asia/Hong_Kong": "HK",
            "Asia/Shanghai": "CN", "Asia/Jakarta": "ID", "Europe/London": "GB",
            "Europe/Paris": "FR", "Europe/Berlin": "DE", "Australia/Sydney": "AU",
        }
        _hotel = get_hotel(hotel_id)
        _tz = (_hotel.get("timezone") or "America/Los_Angeles") if _hotel else "America/Los_Angeles"
        _region = next((v for k, v in _tz_to_region.items() if _tz.startswith(k.split("/")[0] + "/") and k == _tz), "US")

        phone = None
        if not raw_phone:
            error = "Phone number is required."
        else:
            try:
                import phonenumbers
                parsed = phonenumbers.parse(raw_phone, _region)
                if phonenumbers.is_valid_number(parsed):
                    phone = phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164
                    )
                else:
                    error = "Invalid phone number. Check the number and try again."
            except Exception:
                error = "Could not parse phone number. For international numbers include the country code (e.g. +886 912 345678)."

        if not error and not phone:
            error = "Phone number is required."
        else:
            guest_id = get_or_create_guest(phone)
            stay_id = get_or_create_active_stay(guest_id, hotel_id)
            if room_number:
                set_stay_room_number(hotel_id, stay_id, room_number)
            if check_out_date:
                set_stay_checkout_date(hotel_id, stay_id, check_out_date)

            # Send welcome message (SMS or LINE depending on guest identifier)
            hotel = get_hotel(hotel_id)
            hotel_info = get_hotel_info(hotel_id)
            hotel_name = hotel_info.get("hotel_name") or (hotel["name"] if hotel else "the hotel")
            from_number = hotel["phone_number"] if hotel else ""
            room_str = f"Room {room_number}" if room_number else "your room"
            body = (
                f"Welcome to {hotel_name}! You're all set in {room_str}. "
                f"Text us anytime — we're here 24/7 for anything you need during your stay."
            )
            try:
                from outreach import _send_sms, _is_line_guest, _send_line_push, _line_user_id
                from db import log_message as _log_msg
                sent = False
                if _is_line_guest(phone):
                    line_creds = get_hotel_line_credentials(hotel_id) or {}
                    line_token = line_creds.get("token")
                    if line_token:
                        sent = _send_line_push(_line_user_id(phone), body, line_token)
                elif from_number:
                    sent = _send_sms(phone, from_number, body)
                if sent:
                    _log_msg(stay_id, "outbound", body)
                    mark_welcome_sent(stay_id)
            except Exception as exc:
                logger.error("checkin_welcome_failed", extra={"error": str(exc)})

            return redirect(url_for("admin.admin_messages") + f"?stay={stay_id}")

    return render_template(
        "checkin.html",
        csrf_token=csrf_token,
        error=error,
        title="Check In Guest",
        active_page="checkin",
    )


@admin_bp.route("/admin/rooms")
@login_required
@role_required("manager")
def admin_rooms():
    hotel_id = session.get("hotel_id")
    hotel = get_hotel(hotel_id)
    return render_template(
        "rooms.html",
        hotel=hotel,
        title="Room QR Codes",
        active_page="rooms",
    )


@admin_bp.route("/admin/hotel", methods=["GET", "POST"])
@login_required
@role_required("manager")
def admin_hotel():
    hotel_id = session.get("hotel_id")
    if request.method == "POST":
        _require_csrf()
        action = request.form.get("action", "upsert_info")

        # ── Quick Facts ──
        if action == "delete_info":
            info_id = int(request.form.get("info_id", "0"))
            if info_id:
                delete_hotel_info(hotel_id, info_id)
            return redirect(url_for("admin.admin_hotel"))

        if action == "upsert_info":
            key = request.form.get("key", "").strip()
            value = request.form.get("value", "").strip()
            if key and value:
                upsert_hotel_info(hotel_id, key, value)
            return redirect(url_for("admin.admin_hotel"))

        # ── Knowledge Base ──
        if action == "delete_doc":
            doc_id = int(request.form.get("doc_id", "0"))
            if doc_id:
                delete_hotel_doc(hotel_id, doc_id)
                invalidate_vector_index(hotel_id)
            return redirect(url_for("admin.admin_hotel"))

        if action == "edit_doc":
            doc_id = int(request.form.get("doc_id", "0"))
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if doc_id and title and content:
                update_hotel_doc(hotel_id, doc_id, title, content)
                invalidate_vector_index(hotel_id)
            return redirect(url_for("admin.admin_hotel"))

        if action == "add_doc":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if title and content:
                add_hotel_doc(hotel_id, title, content)
                invalidate_vector_index(hotel_id)
            return redirect(url_for("admin.admin_hotel"))

        if action == "approve_suggestion":
            suggestion_id = int(request.form.get("suggestion_id", "0"))
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if suggestion_id and title and content:
                add_hotel_doc(hotel_id, title, content)
                invalidate_vector_index(hotel_id)
                update_knowledge_suggestion_status(suggestion_id, "approved")
            return redirect(url_for("admin.admin_hotel"))

        if action == "dismiss_suggestion":
            suggestion_id = int(request.form.get("suggestion_id", "0"))
            if suggestion_id:
                update_knowledge_suggestion_status(suggestion_id, "dismissed")
            return redirect(url_for("admin.admin_hotel"))

        if action == "save_line_credentials":
            channel_id = request.form.get("line_channel_id", "").strip()
            token = request.form.get("line_channel_token", "").strip()
            secret = request.form.get("line_channel_secret", "").strip()
            update_hotel_line_credentials(hotel_id, channel_id, token, secret)
            return redirect(url_for("admin.admin_hotel"))

        if action == "save_staff_language":
            language = request.form.get("staff_language", "en").strip()
            update_hotel_staff_language(hotel_id, language)
            return redirect(url_for("admin.admin_hotel"))

        return redirect(url_for("admin.admin_hotel"))

    # Pre-fill edit forms
    edit_info_row = None
    edit_info_id = request.args.get("edit_info_id")
    if edit_info_id:
        for r in list_hotel_info(hotel_id):
            if str(r["id"]) == edit_info_id:
                edit_info_row = r
                break

    edit_doc = None
    edit_doc_id = request.args.get("edit_doc_id")
    if edit_doc_id:
        edit_doc = get_hotel_doc(hotel_id, int(edit_doc_id))

    csrf_token = _ensure_csrf_token()
    line_creds = get_hotel_line_credentials(hotel_id) or {}
    hotel = get_hotel(hotel_id)
    staff_language = (hotel["staff_language"] or "en") if hotel and "staff_language" in hotel.keys() else "en"
    return render_template(
        "hotel.html",
        info_rows=list_hotel_info(hotel_id),
        doc_rows=list_hotel_docs(hotel_id, 200),
        suggestions=list_knowledge_suggestions(hotel_id, status="pending"),
        csrf_token=csrf_token,
        edit_info_row=edit_info_row,
        edit_doc=edit_doc,
        line_creds=line_creds,
        staff_language=staff_language,
        title="Hotel Info & Knowledge",
        active_page="hotel",
    )


@admin_bp.route("/admin/knowledge")
@login_required
def admin_knowledge():
    return redirect(url_for("admin.admin_hotel"))


@admin_bp.route("/admin/users", methods=["GET", "POST"])
@login_required
@role_required("manager")
def admin_users():
    hotel_id = session.get("hotel_id")
    if request.method == "POST":
        _require_csrf()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "staff")
        if email and password:
            create_staff_user(hotel_id, email, generate_password_hash(password), role)
            logger.info("staff_user_created", extra={"email": email, "hotel_id": hotel_id})
        return redirect(url_for("admin.admin_users", tab="staff"))

    tab = request.args.get("tab", "guests")
    if tab not in ("guests", "staff"):
        tab = "guests"

    guests = []
    if tab == "guests":
        for g in list_guests_for_hotel(hotel_id):
            g = dict(g)
            g["last_active"] = _relative_time(g["last_message_at"]) if g["last_message_at"] else "—"
            guests.append(g)

    rows = list_staff_users(hotel_id)
    csrf_token = _ensure_csrf_token()
    return render_template(
        "users.html",
        rows=rows,
        guests=guests,
        tab=tab,
        csrf_token=csrf_token,
        title="Users",
        active_page="users",
    )


# ── Customizable dashboard ─────────────────────────────────────────────

# Widget catalogue. `kind` drives how the template renders each card;
# `chart` picks the baked-in mini chart for number widgets.
WIDGET_META = {
    "guest_messages":     {"title": "Guest messages",        "desc": "Total handled",           "kind": "number", "chart": "line"},
    "ai_resolved":        {"title": "AI resolved",           "desc": "Handled without a human", "kind": "number", "chart": "line", "unit": "%"},
    "escalations":        {"title": "Escalations to staff",  "desc": "Handed to a human",       "kind": "number", "chart": "bar"},
    "time_saved":         {"title": "Staff time saved",      "desc": "Hours + value",           "kind": "number", "chart": "bar", "unit": "h"},
    "open_conversations": {"title": "Open conversations",    "desc": "Waiting on a human",      "kind": "live"},
    "open_tasks":         {"title": "Open tasks",            "desc": "Live, with overdue",      "kind": "live"},
    "tasks_completed":    {"title": "Tasks completed",       "desc": "Closed by staff",         "kind": "number", "chart": "bar"},
    "tasks_by_department":{"title": "Tasks by department",   "desc": "Split by team",           "kind": "department"},
    "peak_hours":         {"title": "Peak request hours",    "desc": "When guests message",     "kind": "peak"},
    "top_questions":      {"title": "Top guest questions",   "desc": "Ranked by urgency",       "kind": "list_q"},
    "unanswered":         {"title": "Questions the AI couldn't answer", "desc": "Fix your guide", "kind": "list_u"},
}
DEFAULT_ORDER = list(WIDGET_META.keys())

# ── Staff time-saved model ─────────────────────────────────────────────
# Every value below is a tunable constant, not a magic number. When any of
# them change materially, bump TS_METHODOLOGY_UPDATED so the visible "last
# updated" date explains why historical figures shifted (we recompute all
# periods with the current constants rather than snapshotting).
TS_SIMPLE_MIN = 1.5          # wifi, checkout time, hours, directions
TS_STANDARD_MIN = 3.0        # towels, housekeeping, amenities, task ticket
TS_COMPLEX_MIN = 6.0         # bookings, coordination, multi-step
TS_INTERRUPTION_MIN = 1.0    # context-switch cost per AI-resolved interaction
TS_ESCALATION_CREDIT = 0.30  # partial credit for AI intake before a handoff
TS_LOADED_MULTIPLIER = 1.3   # payroll tax, benefits, overhead over base wage
TS_MIN_INTERACTIONS = 25     # below this many resolved interactions, gate the card
DEFAULT_BASE_WAGE = 22.0     # sensible front-desk base wage; works with no setup
TS_METHODOLOGY_UPDATED = "2026-07-28"

# Interaction type is inferred from the guest's message text — no input needed
# from the hotel. Complex is checked first (most specific), then simple; the
# middle "standard" tier is the default. Keyword lists are config, too.
TS_COMPLEX_KEYWORDS = [
    "book", "booking", "reserve", "reservation", "arrange", "coordinate",
    "car", "taxi", "uber", "airport", "sfo", "transfer", "shuttle", "pick up",
    "pickup", "hold my bag", "hold my bags", "luggage", "store my", "schedule",
    "appointment", "tour", "restaurant reservation", "tickets",
]
TS_SIMPLE_KEYWORDS = [
    "wifi", "wi-fi", "wi fi", "password", "checkout", "check out", "check-out",
    "check in time", "what time", "hours", "open", "close", "closing",
    "direction", "directions", "where is", "how do i get", "address", "parking",
    "pool hours", "breakfast time", "gym",
]


def _classify_interaction(body: str) -> str:
    b = (body or "").lower()
    if any(k in b for k in TS_COMPLEX_KEYWORDS):
        return "complex"
    if any(k in b for k in TS_SIMPLE_KEYWORDS):
        return "simple"
    return "standard"


def _time_saved_minutes(simple, standard, complex_, resolved, escalations):
    """Total staff-minutes saved for a set of interactions (the section-1
    formula). `resolved` = simple+standard+complex (each incurs one
    interruption-recovery minute)."""
    return (simple * TS_SIMPLE_MIN
            + standard * TS_STANDARD_MIN
            + complex_ * TS_COMPLEX_MIN
            + resolved * TS_INTERRUPTION_MIN
            + escalations * TS_ESCALATION_CREDIT * TS_STANDARD_MIN)


def _wage_fmt(w):
    return ("%g" % w)


def _valid_period(p):
    if p is None or p in ("7d", "30d", "90d"):
        return True
    if isinstance(p, dict) and "start" in p and "end" in p:
        try:
            datetime.fromisoformat(str(p["start"]))
            datetime.fromisoformat(str(p["end"]))
            return True
        except ValueError:
            return False
    return False


def _period_bounds(period):
    """Return (start_dt, end_dt_exclusive, label) for a period spec."""
    now = datetime.utcnow()
    if isinstance(period, dict):
        s = datetime.fromisoformat(str(period["start"]))
        e = datetime.fromisoformat(str(period["end"])) + timedelta(days=1)
        return s, e, f"{period['start']} to {period['end']}"
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period or "30d", 30)
    return now - timedelta(days=days), now, f"Last {days} days"


def _dashboard_buckets(start_dt, end_dt, granularity):
    endd = (end_dt - timedelta(seconds=1)).date()
    d = start_dt.date()
    days = []
    while d <= endd:
        days.append(d)
        d += timedelta(days=1)
    if granularity == "day":
        return [{"label": dd.strftime("%b %d").replace(" 0", " "), "keys": [dd.strftime("%Y-%m-%d")]} for dd in days]
    groups, order = {}, []
    for dd in days:
        wk = dd - timedelta(days=dd.weekday())
        k = wk.strftime("%Y-%m-%d")
        if k not in groups:
            groups[k] = {"label": wk.strftime("%b %d").replace(" 0", " "), "keys": []}
            order.append(k)
        groups[k]["keys"].append(dd.strftime("%Y-%m-%d"))
    return [groups[k] for k in order]


def _sum_over(daymap, keys):
    return sum(daymap.get(k, 0) for k in keys)


def _load_dashboard_config(user_id):
    """Merge the user's saved layout with the widget catalogue so new widgets
    surface and removed ones drop out. Returns ordered list of
    {id, enabled, period}."""
    raw = get_dashboard_prefs(user_id)
    saved = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                saved = parsed
        except (ValueError, TypeError):
            saved = []
    result, seen = [], set()
    for x in saved:
        if not isinstance(x, dict):
            continue
        wid = x.get("id")
        if wid in WIDGET_META and wid not in seen:
            period = x.get("period")
            if not _valid_period(period):
                period = None
            result.append({"id": wid, "enabled": bool(x.get("enabled", True)), "period": period})
            seen.add(wid)
    for wid in DEFAULT_ORDER:
        if wid not in seen:
            result.append({"id": wid, "enabled": True, "period": None})
    return result


def _time_saved_categories(hotel_id, s, e):
    """Classify AI-resolved interactions over [s, e). Returns
    (per_day {'YYYY-MM-DD': {simple,standard,complex}}, totals dict,
    per_day escalation counts)."""
    rows = dashboard_ai_resolved_rows(hotel_id, s, e)
    esc_daymap = dashboard_daily_counts(hotel_id, "escalations", s, e)
    per_day, tot = {}, {"simple": 0, "standard": 0, "complex": 0}
    for row in rows:
        cat = _classify_interaction(row["body"])
        day = row["day"]
        per_day.setdefault(day, {"simple": 0, "standard": 0, "complex": 0})
        per_day[day][cat] += 1
        tot[cat] += 1
    return per_day, tot, esc_daymap


def _build_time_saved_widget(hotel_id, s, e, prev_s, prev_e, granularity, base_wage):
    per_day, tot, esc_daymap = _time_saved_categories(hotel_id, s, e)
    resolved_total = tot["simple"] + tot["standard"] + tot["complex"]

    # Section 5: gate at low volume with an honest empty state (same size/slot).
    if resolved_total < TS_MIN_INTERACTIONS:
        return {
            "insufficient": True,
            "threshold": TS_MIN_INTERACTIONS,
            "resolved_total": resolved_total,
            "has_delta": False, "delta_display": None,
            "chart_type": "bar", "chart_labels": [], "chart_values": [], "unit": "h",
        }

    esc_total = sum(esc_daymap.values())
    total_min = _time_saved_minutes(tot["simple"], tot["standard"], tot["complex"], resolved_total, esc_total)
    hours = round(total_min / 60, 1)
    dollars = round(hours * base_wage * TS_LOADED_MULTIPLIER)

    # Previous period — totals only, for the delta chip.
    _, ptot, pesc = _time_saved_categories(hotel_id, prev_s, prev_e)
    p_resolved = ptot["simple"] + ptot["standard"] + ptot["complex"]
    prev_hours = round(_time_saved_minutes(ptot["simple"], ptot["standard"], ptot["complex"],
                                           p_resolved, sum(pesc.values())) / 60, 1)
    delta = round((hours - prev_hours) / prev_hours * 100) if prev_hours else None

    # Trend chart: hours saved per bucket, using the same per-interaction model.
    series = []
    for b in _dashboard_buckets(s, e, granularity):
        sm = st = cx = 0
        for k in b["keys"]:
            c = per_day.get(k)
            if c:
                sm += c["simple"]; st += c["standard"]; cx += c["complex"]
        resolved_b = sm + st + cx
        esc_b = sum(esc_daymap.get(k, 0) for k in b["keys"])
        series.append({"label": b["label"],
                       "value": round(_time_saved_minutes(sm, st, cx, resolved_b, esc_b) / 60, 1)})

    esc_min = round(esc_total * TS_ESCALATION_CREDIT * TS_STANDARD_MIN, 1)
    breakdown = {
        "rows": [
            {"label": "simple info requests", "count": tot["simple"], "unit": TS_SIMPLE_MIN,
             "minutes": round(tot["simple"] * TS_SIMPLE_MIN, 1)},
            {"label": "standard requests", "count": tot["standard"], "unit": TS_STANDARD_MIN,
             "minutes": round(tot["standard"] * TS_STANDARD_MIN, 1)},
            {"label": "complex / multi-step", "count": tot["complex"], "unit": TS_COMPLEX_MIN,
             "minutes": round(tot["complex"] * TS_COMPLEX_MIN, 1)},
            {"label": "interruption recoveries", "count": resolved_total, "unit": TS_INTERRUPTION_MIN,
             "minutes": round(resolved_total * TS_INTERRUPTION_MIN, 1)},
            {"label": "escalations (%d%% credit)" % round(TS_ESCALATION_CREDIT * 100),
             "count": esc_total, "unit": None, "minutes": esc_min},
        ],
        "hours": hours,
        "base_wage": _wage_fmt(base_wage),
        "multiplier": TS_LOADED_MULTIPLIER,
        "dollars": f"{dollars:,}",
    }

    return {
        "insufficient": False,
        "value_fmt": f"{hours}h",
        "secondary": f"${dollars:,} saved",
        "caption": f"Based on ${_wage_fmt(base_wage)}/hr base wage",
        "breakdown": breakdown,
        "has_delta": delta is not None,
        "delta_display": (f"{'+' if delta >= 0 else ''}{delta}%") if delta is not None else None,
        "trend_up": (hours - prev_hours) >= 0,
        "chart_type": "bar",
        "chart_labels": [x["label"] for x in series],
        "chart_values": [x["value"] for x in series],
        "unit": "h",
    }


def _build_number_widget(hotel_id, metric, s, e, prev_s, prev_e, granularity):
    buckets = _dashboard_buckets(s, e, granularity)
    unit = WIDGET_META[metric].get("unit", "")
    secondary, delta_unit = None, "%"

    if metric in ("guest_messages", "escalations", "tasks_completed"):
        daymap = dashboard_daily_counts(hotel_id, metric, s, e)
        prevmap = dashboard_daily_counts(hotel_id, metric, prev_s, prev_e)
        value = sum(daymap.values())
        prev = sum(prevmap.values())
        series = [{"label": b["label"], "value": _sum_over(daymap, b["keys"])} for b in buckets]
        value_fmt = f"{value:,}"

    else:  # ai_resolved
        aimap = dashboard_daily_counts(hotel_id, "ai_resolved", s, e)
        inmap = dashboard_daily_counts(hotel_id, "guest_messages", s, e)
        ai_prev = sum(dashboard_daily_counts(hotel_id, "ai_resolved", prev_s, prev_e).values())
        in_prev = sum(dashboard_daily_counts(hotel_id, "guest_messages", prev_s, prev_e).values())
        ai_tot, in_tot = sum(aimap.values()), sum(inmap.values())
        value = round(ai_tot / in_tot * 100) if in_tot else 0
        prev = round(ai_prev / in_prev * 100) if in_prev else 0
        series = []
        for b in buckets:
            a, i = _sum_over(aimap, b["keys"]), _sum_over(inmap, b["keys"])
            series.append({"label": b["label"], "value": round(a / i * 100) if i else 0})
        value_fmt = f"{value}%"
        secondary = f"{ai_tot:,} of {in_tot:,} messages"
        delta_unit = "pt"

    if metric == "ai_resolved":
        delta = value - prev
        delta_display = f"{'+' if delta >= 0 else ''}{delta}{delta_unit}"
        has_delta = prev > 0 or value > 0
    else:
        delta = round((value - prev) / prev * 100) if prev else None
        delta_display = (f"{'+' if delta >= 0 else ''}{delta}%") if delta is not None else None
        has_delta = delta is not None
    trend_up = (value - prev) >= 0

    return {
        "value_fmt": value_fmt,
        "secondary": secondary,
        "delta_display": delta_display,
        "has_delta": has_delta,
        "trend_up": trend_up,
        "chart_type": WIDGET_META[metric].get("chart", "line"),
        "chart_labels": [x["label"] for x in series],
        "chart_values": [x["value"] for x in series],
        "unit": unit,
    }


def _build_widget(hotel_id, wid, s, e, base_wage, live, pinned, period_label, period_value):
    meta = WIDGET_META[wid]
    span = max(1, (e - s).days)
    granularity = "day" if span <= 31 else "week"
    prev_s, prev_e = s - (e - s), s
    w = {
        "id": wid, "title": meta["title"], "desc": meta["desc"], "kind": meta["kind"],
        "pinned": pinned, "period_label": period_label, "period_value": period_value or "",
    }
    if meta["kind"] == "number":
        if wid == "time_saved":
            w.update(_build_time_saved_widget(hotel_id, s, e, prev_s, prev_e, granularity, base_wage))
        else:
            w.update(_build_number_widget(hotel_id, wid, s, e, prev_s, prev_e, granularity))
    elif meta["kind"] == "live":
        if wid == "open_conversations":
            w["value_fmt"] = f"{live['open_conversations']:,}"
            w["sub"] = "Waiting on a human right now"
            w["sub_danger"] = False
        else:
            w["value_fmt"] = f"{live['open_tasks']:,}"
            od = live["overdue"]
            w["sub"] = (f"{od} overdue" if od else "None overdue")
            w["sub_danger"] = od > 0
    elif meta["kind"] == "department":
        rows = dashboard_department(hotel_id, s, e)
        total = sum(r["count"] for r in rows) or 1
        w["rows"] = [{"department": r["department"].capitalize(), "count": r["count"],
                      "pct": round(r["count"] / total * 100)} for r in rows]
    elif meta["kind"] == "peak":
        hours = dashboard_peak_hours(hotel_id, s, e)
        labels = [(f"{(h % 12) or 12}{'a' if h < 12 else 'p'}") for h in range(24)]
        w["chart_labels"], w["chart_values"] = labels, hours
        w["chart_type"], w["unit"] = "bar", ""
    elif meta["kind"] == "list_q":
        w["entries"] = dashboard_top_questions(hotel_id, s, e)
    elif meta["kind"] == "list_u":
        w["entries"] = dashboard_unanswered(hotel_id)
    return w


@admin_bp.route("/admin/analytics")
@login_required
@role_required("manager")
def admin_analytics():
    hotel_id = session.get("hotel_id")
    user_id = session.get("user_id")

    # Page-level period: custom range wins, else preset (default 30d).
    if request.args.get("start") and request.args.get("end"):
        page_period = {"start": request.args["start"], "end": request.args["end"]}
    else:
        page_period = request.args.get("range", "30d")
    if not _valid_period(page_period):
        page_period = "30d"
    p_s, p_e, p_label = _period_bounds(page_period)

    config = _load_dashboard_config(user_id)
    base_wage = _get_base_wage(hotel_id)

    live = dashboard_live(hotel_id)
    widgets = []
    for item in config:
        if not item.get("enabled"):
            continue
        wid = item["id"]
        pin = item.get("period")
        if pin and WIDGET_META[wid]["kind"] not in ("live",):
            w_s, w_e, w_label = _period_bounds(pin)
            pinned = True
        else:
            w_s, w_e, w_label, pin = p_s, p_e, p_label, None
            pinned = False
        widgets.append(_build_widget(hotel_id, wid, w_s, w_e, base_wage, live, pinned, w_label, pin))

    if isinstance(page_period, dict):
        page_kind, cstart, cend = "custom", page_period["start"], page_period["end"]
    else:
        page_kind, cstart, cend = page_period, "", ""

    catalogue = [
        {"id": x["id"], "title": WIDGET_META[x["id"]]["title"], "enabled": x["enabled"],
         "period": x.get("period") or "", "kind": WIDGET_META[x["id"]]["kind"]}
        for x in config
    ]

    return render_template(
        "analytics.html",
        widgets=widgets,
        catalogue=catalogue,
        page_kind=page_kind,
        page_label=p_label,
        custom_start=cstart,
        custom_end=cend,
        title="Home",
        active_page="analytics",
    )


@admin_bp.route("/admin/analytics/prefs", methods=["POST"])
@login_required
@role_required("manager")
def admin_dashboard_prefs():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    config = payload.get("config")
    if not isinstance(config, list):
        return jsonify({"ok": False, "error": "invalid config"}), 400
    cleaned, seen = [], set()
    for x in config:
        if not isinstance(x, dict):
            continue
        wid = x.get("id")
        if wid not in WIDGET_META or wid in seen:
            continue
        period = x.get("period")
        if not _valid_period(period):
            period = None
        cleaned.append({"id": wid, "enabled": bool(x.get("enabled", True)), "period": period})
        seen.add(wid)
    for wid in DEFAULT_ORDER:
        if wid not in seen:
            cleaned.append({"id": wid, "enabled": True, "period": None})
    save_dashboard_prefs(user_id, json.dumps(cleaned))
    return jsonify({"ok": True})


def _get_base_wage(hotel_id):
    try:
        return float(get_hotel_info(hotel_id).get("base_wage") or DEFAULT_BASE_WAGE)
    except (ValueError, TypeError):
        return DEFAULT_BASE_WAGE


@admin_bp.route("/admin/methodology", methods=["GET", "POST"])
@login_required
@role_required("manager")
def admin_methodology():
    """Tier 3: how 'Staff time saved' is calculated, plus the base-wage setting."""
    hotel_id = session.get("hotel_id")
    saved = False
    if request.method == "POST":
        try:
            bw = float(request.form.get("base_wage"))
            bw = max(0.0, min(bw, 100000.0))
            upsert_hotel_info(hotel_id, "base_wage", str(bw))
            saved = True
        except (ValueError, TypeError):
            pass

    constants = [
        {"name": "Simple info request", "value": f"{_wage_fmt(TS_SIMPLE_MIN)} min",
         "rationale": "Wifi, checkout time, hours, directions — a one-line answer."},
        {"name": "Standard request", "value": f"{_wage_fmt(TS_STANDARD_MIN)} min",
         "rationale": "Towels, housekeeping, amenities, a task ticket — a bit of legwork."},
        {"name": "Complex / multi-step", "value": f"{_wage_fmt(TS_COMPLEX_MIN)} min",
         "rationale": "Bookings, coordination, anything with follow-up."},
        {"name": "Interruption recovery", "value": f"+{_wage_fmt(TS_INTERRUPTION_MIN)} min each",
         "rationale": "The context-switching cost of every interruption. Deliberately conservative."},
        {"name": "Escalated interactions", "value": f"{round(TS_ESCALATION_CREDIT * 100)}% credit",
         "rationale": "The AI still triaged and gathered context before handing off, so it earns partial credit."},
        {"name": "Loaded wage multiplier", "value": f"{TS_LOADED_MULTIPLIER}×",
         "rationale": "Payroll tax, benefits and overhead sit on top of the base wage. You tell us the base; we add the burden."},
        {"name": "Minimum data", "value": f"{TS_MIN_INTERACTIONS} interactions",
         "rationale": "Below this we show 'not enough data yet' instead of a number too small to mean anything."},
    ]
    return render_template(
        "methodology.html",
        base_wage=_wage_fmt(_get_base_wage(hotel_id)),
        constants=constants,
        updated=TS_METHODOLOGY_UPDATED,
        saved=saved,
        title="Methodology",
        active_page="analytics",
    )


@admin_bp.route("/admin/support")
@login_required
def admin_support():
    return render_template(
        "support.html",
        title="Support",
        active_page="support",
    )


@admin_bp.route("/admin/settings")
@login_required
def admin_settings():
    hotel = get_hotel(session.get("hotel_id"))
    return render_template(
        "settings.html",
        hotel=hotel,
        title="Settings",
        active_page="settings",
    )


def _send_sms(to_number: str, body: str):
    sid = current_app.config["TWILIO_SID"]
    token = current_app.config["TWILIO_TOKEN"]
    from_number = current_app.config["TWILIO_NUMBER"]
    if not (sid and token and from_number and to_number):
        return
    try:
        client = Client(sid, token)
        client.messages.create(to=to_number, from_=from_number, body=body)
    except Exception as exc:
        logger.exception("twilio_send_error", extra={"error": str(exc)})


def _send_line_push(line_user_id: str, body: str, channel_token: str):
    import requests as _requests
    try:
        resp = _requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {channel_token}",
                "Content-Type": "application/json",
            },
            json={"to": line_user_id, "messages": [{"type": "text", "text": body}]},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("line_push_failed", extra={"status": resp.status_code})
    except Exception as exc:
        logger.exception("line_push_error", extra={"error": str(exc)})


def _send_reply_to_guest(hotel_id: int, to_number: str, body: str):
    """Send a staff reply via the correct channel (LINE or SMS)."""
    if to_number and to_number.startswith("line:"):
        from db import get_hotel_line_credentials
        creds = get_hotel_line_credentials(hotel_id)
        if creds and creds.get("token"):
            _send_line_push(to_number[5:], body, creds["token"])
    else:
        _send_sms(to_number, body)
