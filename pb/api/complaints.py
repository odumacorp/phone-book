import time
from flask import g, jsonify, request
from itsdangerous import BadSignature

from pb.api.auth import api_error, body_json, require_auth, serializer, token_version
from pb.config import SECRET_KEY, TOKEN_MAX_AGE
from pb.db import execute, fetch_all, fetch_one
from pb.users import get_user
from pb.web import app


# ---------------------------------------------------------------- complaints
# Users report problems from the app (logged in) or the login screen (with a phone or email to
# reply to). The admin reads and answers them at /admin; logged-in users see the answer in the app.

COMPLAINT_MAX = 1000
COMPLAINTS_PER_HOUR = 5


def client_ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:64]


def optional_user():
    """The logged-in user's phone if a valid app token was sent, else None."""
    header = request.headers.get("Authorization", "")
    if not SECRET_KEY or not header.startswith("Bearer "):
        return None
    try:
        data = serializer().loads(header[7:], max_age=TOKEN_MAX_AGE)
    except BadSignature:
        return None
    user = get_user(data.get("phone", ""))
    return user["phone"] if user and token_version(user) == data.get("v") else None


def complaint_json(r):
    return {
        "id": r["id"], "message": r["message"], "status": r["status"], "reply": r["reply"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


@app.route("/api/complaints", methods=["POST"])
def api_add_complaint():
    body = body_json()
    message = str(body.get("message") or "").strip()[:COMPLAINT_MAX]
    owner = optional_user()
    contact = " ".join(str(body.get("contact") or "").split())[:120] or None
    if len(message) < 5:
        return api_error("Describe the problem in a few words.", 400)
    if not owner and not contact:
        return api_error("Enter a phone number or email so we can reply.", 400)
    now, ip = int(time.time()), client_ip()
    recent = fetch_one(
        "SELECT COUNT(*) AS n FROM complaints WHERE (ip = ? OR owner = ?) AND created_at > ?", (ip, owner or "", now - 3600)
    )
    if recent["n"] >= COMPLAINTS_PER_HOUR:
        return api_error("You've sent several reports already. Please wait a while.", 429)
    execute(
        "INSERT INTO complaints (owner, contact, message, ip, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (owner, contact, message, ip, now, now),
    )
    return jsonify({"sent": True}), 201


@app.route("/api/complaints", methods=["GET"])
@require_auth
def api_complaints():
    rows = fetch_all(
        "SELECT id, message, status, reply, created_at, updated_at FROM complaints WHERE owner = ? "
        "ORDER BY created_at DESC LIMIT 50",
        (g.phone,),
    )
    return jsonify({"complaints": [complaint_json(r) for r in rows]})
