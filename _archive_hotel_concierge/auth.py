from functools import wraps
from flask import session, redirect, url_for, g

from db import get_staff_user_by_id


def load_current_user():
    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return
    user = get_staff_user_by_id(user_id)
    if not user:
        session.clear()
        g.current_user = None
        return
    g.current_user = user


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_staff_user_by_id(session.get("user_id", -1))
            role = user["role"] if user and "role" in user.keys() else None
            if not user or role not in roles:
                return redirect(url_for("admin.admin_messages"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def login_user(user):
    session["user_id"] = user["id"]
    session["hotel_id"] = user["hotel_id"]
    session["role"] = user["role"]
    session["email"] = user["email"]


def logout_user():
    session.clear()
