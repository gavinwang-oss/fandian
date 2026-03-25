import logging
import secrets
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, current_app
from twilio.rest import Client

from auth import login_required, role_required
from vector_index import invalidate_vector_index
from db import (
    list_messages_for_hotel,
    list_tasks,
    get_task_stats,
    update_task_status,
    update_task_fields,
    list_hotel_info,
    upsert_hotel_info,
    delete_hotel_info,
    list_messages_for_stay,
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
)
from werkzeug.security import generate_password_hash

admin_bp = Blueprint("admin", __name__)


def _relative_time(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return str(value)
    diff = datetime.utcnow() - value
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
    rows = list_messages_for_hotel(hotel_id, 200)
    return render_template(
        "messages.html",
        rows=rows,
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
            if task and status == "done" and task.get("notify_guest_when_done"):
                to_number = get_guest_phone_for_stay(hotel_id, task["stay_id"])
                guest_id = get_guest_id_for_stay(hotel_id, task["stay_id"])
                if to_number and guest_id and not is_opted_out(guest_id, hotel_id):
                    _send_sms(to_number, "Your request has been completed. Let us know if you need anything else.")
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


@admin_bp.route("/admin/stay/<int:stay_id>")
@login_required
def admin_stay(stay_id: int):
    hotel_id = session.get("hotel_id")
    rows = list_messages_for_stay(hotel_id, stay_id)
    guest_phone = rows[0]["guest_phone"] if rows else ""
    csrf_token = _ensure_csrf_token()
    return render_template(
        "stay.html",
        rows=rows,
        stay_id=stay_id,
        guest_phone=guest_phone,
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

    log_message(stay_id, "outbound", body)

    to_number = get_guest_phone_for_stay(hotel_id, stay_id)
    if to_number:
        _send_sms(to_number, body)
        logger.info("staff_reply_sent", extra={"stay_id": stay_id})

    return redirect(url_for("admin.admin_stay", stay_id=stay_id))


@admin_bp.route("/admin/hotel", methods=["GET", "POST"])
@login_required
@role_required("manager")
def admin_hotel():
    hotel_id = session.get("hotel_id")
    if request.method == "POST":
        _require_csrf()
        action = request.form.get("action", "upsert")

        if action == "delete":
            info_id = int(request.form.get("info_id", "0"))
            if info_id:
                delete_hotel_info(hotel_id, info_id)
                logger.info("hotel_info_deleted", extra={"info_id": info_id, "hotel_id": hotel_id})
            return redirect(url_for("admin.admin_hotel"))

        # upsert (add or edit)
        key = request.form.get("key", "").strip()
        value = request.form.get("value", "").strip()
        if key and value:
            upsert_hotel_info(hotel_id, key, value)
        return redirect(url_for("admin.admin_hotel"))

    # Pre-fill form if editing an existing entry
    edit_row = None
    edit_id = request.args.get("edit_id")
    if edit_id:
        rows_all = list_hotel_info(hotel_id)
        for r in rows_all:
            if str(r["id"]) == edit_id:
                edit_row = r
                break

    rows = list_hotel_info(hotel_id)
    csrf_token = _ensure_csrf_token()
    return render_template(
        "hotel.html",
        rows=rows,
        csrf_token=csrf_token,
        edit_row=edit_row,
        title="Hotel Info",
        active_page="hotel",
    )


@admin_bp.route("/admin/knowledge", methods=["GET", "POST"])
@login_required
@role_required("manager")
def admin_knowledge():
    hotel_id = session.get("hotel_id")
    if request.method == "POST":
        _require_csrf()
        action = request.form.get("action", "add")

        if action == "delete":
            doc_id = int(request.form.get("doc_id", "0"))
            if doc_id:
                delete_hotel_doc(hotel_id, doc_id)
                invalidate_vector_index(hotel_id)
                logger.info("hotel_doc_deleted", extra={"doc_id": doc_id, "hotel_id": hotel_id})
            return redirect(url_for("admin.admin_knowledge"))

        if action == "edit":
            doc_id = int(request.form.get("doc_id", "0"))
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if doc_id and title and content:
                update_hotel_doc(hotel_id, doc_id, title, content)
                invalidate_vector_index(hotel_id)
                logger.info("hotel_doc_updated", extra={"doc_id": doc_id, "hotel_id": hotel_id})
            return redirect(url_for("admin.admin_knowledge"))

        # add new doc
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if title and content:
            add_hotel_doc(hotel_id, title, content)
            invalidate_vector_index(hotel_id)
        return redirect(url_for("admin.admin_knowledge"))

    # Pre-fill form if editing an existing doc
    edit_doc = None
    edit_id = request.args.get("edit_id")
    if edit_id:
        edit_doc = get_hotel_doc(hotel_id, int(edit_id))

    rows = list_hotel_docs(hotel_id, 200)
    csrf_token = _ensure_csrf_token()
    return render_template(
        "knowledge.html",
        rows=rows,
        csrf_token=csrf_token,
        edit_doc=edit_doc,
        title="Knowledge Base",
        active_page="knowledge",
    )


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
