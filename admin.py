import logging
import secrets
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, current_app
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
        _send_sms(to_number, body)
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
        phone = None
        if not raw_phone:
            error = "Phone number is required."
        else:
            try:
                import phonenumbers
                parsed = phonenumbers.parse(raw_phone, "US")
                if phonenumbers.is_valid_number(parsed):
                    phone = phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164
                    )
                else:
                    error = "Invalid phone number. Check the number and try again."
            except Exception:
                error = "Could not parse phone number. For international numbers include the country code (e.g. +44 7911 123456)."

        if not error and not phone:
            error = "Phone number is required."
        else:
            guest_id = get_or_create_guest(phone)
            stay_id = get_or_create_active_stay(guest_id, hotel_id)
            if room_number:
                set_stay_room_number(hotel_id, stay_id, room_number)
            if check_out_date:
                set_stay_checkout_date(hotel_id, stay_id, check_out_date)

            # Send welcome SMS
            hotel = get_hotel(hotel_id)
            hotel_info = get_hotel_info(hotel_id)
            hotel_name = hotel_info.get("hotel_name") or (hotel["name"] if hotel else "the hotel")
            from_number = hotel["phone_number"] if hotel else ""
            room_str = f"Room {room_number}" if room_number else "your room"
            body = (
                f"Welcome to {hotel_name}! You're all set in {room_str}. "
                f"Text us anytime — we're here 24/7 for anything you need during your stay."
            )
            if from_number:
                try:
                    from outreach import _send_sms
                    from db import log_message as _log_msg
                    if _send_sms(phone, from_number, body):
                        _log_msg(stay_id, "outbound", body)
                        mark_welcome_sent(stay_id)
                except Exception as exc:
                    logger.error("checkin_welcome_sms_failed", extra={"error": str(exc)})

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
    return render_template(
        "hotel.html",
        info_rows=list_hotel_info(hotel_id),
        doc_rows=list_hotel_docs(hotel_id, 200),
        suggestions=list_knowledge_suggestions(hotel_id, status="pending"),
        csrf_token=csrf_token,
        edit_info_row=edit_info_row,
        edit_doc=edit_doc,
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
        return redirect(url_for("admin.admin_users"))

    rows = list_staff_users(hotel_id)
    csrf_token = _ensure_csrf_token()
    return render_template(
        "users.html",
        rows=rows,
        csrf_token=csrf_token,
        title="Users",
        active_page="users",
    )


@admin_bp.route("/admin/analytics")
@login_required
@role_required("manager")
def admin_analytics():
    hotel_id = session.get("hotel_id")
    days = int(request.args.get("days", 30))
    data = get_analytics(hotel_id, days=days)
    return render_template(
        "analytics.html",
        data=data,
        days=days,
        title="Analytics",
        active_page="analytics",
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
