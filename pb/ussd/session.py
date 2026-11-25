import hashlib
import hmac
import json
import time
from flask import g
from werkzeug.security import check_password_hash

from pb.config import BACK, DATABASE_URL, LOCKOUT_SECONDS, MAX_PIN_ATTEMPTS, RESUME_SECONDS, SECRET_KEY, duration_text
from pb.db import execute, fetch_one, get_db, sql
from pb.notify import notify_event
from pb.text import LOGIN_PHONE_FORMAT, con, end, login_phone
from pb.users import DISABLED_MESSAGE, get_user
from pb.ussd.personal import menu
from pb.ussd.screens import resolve_nav


# ---------------------------------------------------------------- login

def check_pin(phone, pin):
    """Verify a PIN with lockout. Returns (user, None) on success or (None, error message)."""
    user = get_user(phone)
    if not user:
        return None, "Wrong phone number or PIN."
    now = int(time.time())
    if user["locked_until"] > now:
        minutes = (user["locked_until"] - now + 59) // 60  # round up
        return None, f"Too many wrong PINs. Try again in {minutes} min."
    if not check_password_hash(user["pin_hash"], pin):
        attempts = user["failed_attempts"] + 1
        locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_PIN_ATTEMPTS else 0
        execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE phone = ?",
            (0 if locked_until else attempts, locked_until, phone),
        )
        get_user(phone, fresh=True)  # a second try in this same request must see the new count
        if locked_until:
            notify_event(phone, "pin_lockout", attempts=MAX_PIN_ATTEMPTS, minutes=LOCKOUT_SECONDS // 60)
            return None, "Too many wrong PINs. Try again in 15 min."
        return None, "Wrong phone number or PIN."
    if user["failed_attempts"]:
        execute("UPDATE users SET failed_attempts = 0 WHERE phone = ?", (phone,))
    if user["disabled"]:
        return None, DISABLED_MESSAGE
    return user, None


# ---------------------------------------------------------------- resume after a timeout
# Networks end USSD sessions after a few minutes (and sooner when a screen sits idle); we can't
# extend that. Instead each phone's last menu screen is saved, and redialling within
# RESUME_SECONDS asks only for the PIN and returns there. The session that was offered the
# resume is remembered (offered_in) so its later inputs are read the same way.

def get_resume(caller):
    return fetch_one(
        "SELECT owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, logged_out, login_id "
        "FROM ussd_resume WHERE caller = ?",
        (caller,),
    )


def save_resume(caller, owner, path, session_id, state, mac, login_id):
    keep = state is not None and state["offered_in"] == session_id
    now = int(time.time())
    execute("UPDATE ussd_sessions SET last_active = ? WHERE id = ?", (now, login_id))
    execute(
        "INSERT INTO ussd_resume (caller, owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, "
        "logged_out, login_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
        "ON CONFLICT (caller) DO UPDATE SET owner = excluded.owner, path = excluded.path, saved_at = excluded.saved_at, "
        "session_id = excluded.session_id, offered_in = excluded.offered_in, resume_owner = excluded.resume_owner, "
        "resume_from = excluded.resume_from, pin_mac = excluded.pin_mac, logged_out = 0, login_id = excluded.login_id",
        (
            caller,
            owner,
            json.dumps(path),
            int(time.time()),
            session_id,
            session_id if keep else None,
            state["resume_owner"] if keep else None,
            state["resume_from"] if keep else None,
            mac,
            login_id,
        ),
    )


def session_mac(session_id, owner, pin):
    """Proof that this PIN already passed the (deliberately slow) check earlier in this USSD session."""
    if not SECRET_KEY or not session_id:
        return None
    msg = f"{session_id}|{owner}|{pin}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


PIN_TRIES = 2  # per USSD session: a wrong first PIN gets one more try
RESET_PIN_HELP = (
    "Reset your PIN in the Remote Phonebook app or at phone-book.odumacorp.com: "
    "log in and tap Change USSD PIN."
)


def wrong_pin_mac(session_id, owner, pin):
    msg = f"wrong|{session_id}|{owner}|{pin}".encode()
    return hmac.new((SECRET_KEY or "dev").encode(), msg, hashlib.sha256).hexdigest()


def known_wrong(session_id, owner, pin):
    """Every screen resends the earlier inputs, so a PIN already rejected in this session is
    remembered rather than checked (and counted toward the lockout) again on each screen."""
    return bool(session_id) and bool(fetch_one(
        "SELECT 1 AS x FROM ussd_wrong_pins WHERE session_id = ? AND mac = ?",
        (session_id, wrong_pin_mac(session_id, owner, pin)),
    ))


def remember_wrong(session_id, owner, pin):
    if not session_id:
        return
    now = int(time.time())
    db = get_db()
    db.execute(sql("DELETE FROM ussd_wrong_pins WHERE created_at < ?"), (now - 3600,))
    db.execute(
        sql("INSERT INTO ussd_wrong_pins (session_id, mac, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING"),
        (session_id, wrong_pin_mac(session_id, owner, pin), now),
    )
    db.commit()


def authenticate(owner, inputs, session_id, state, caller, network):
    """PIN entry with a second try. `inputs` starts at the first PIN.

    Returns (mac, inputs after the PIN that worked, None), or (None, None, a screen to show).
    """
    for attempt in range(PIN_TRIES):
        if len(inputs) <= attempt:
            return None, None, con(f"Wrong PIN. You have {PIN_TRIES - attempt} more try. Enter your PIN:")
        pin = inputs[attempt]
        error = "Wrong phone number or PIN." if known_wrong(session_id, owner, pin) else None
        if error is None:
            mac, error = login(owner, pin, session_id, state, caller, network)
            if error is None:
                return mac, inputs[attempt + 1:], None
            if error == DISABLED_MESSAGE:
                return None, None, end(error)
            if not error.startswith("Too many"):
                remember_wrong(session_id, owner, pin)
        if error.startswith("Too many"):
            return None, None, end(f"{error} {RESET_PIN_HELP}")
    return None, None, end(f"Wrong PIN again. {RESET_PIN_HELP}")


def login(owner, pin, session_id, state, caller, network):
    """check_pin, but only the first time in a session: every screen resends the PIN."""
    mac = session_mac(session_id, owner, pin)
    if mac and state and state["session_id"] == session_id and state["owner"] == owner and state["pin_mac"]:
        user = get_user(owner)
        unlocked = user and user["locked_until"] <= time.time()  # a lockout still applies mid-session
        if unlocked and hmac.compare_digest(state["pin_mac"], mac):
            return mac, None
    _user, error = check_pin(owner, pin)
    log_login(owner, caller, network, session_id, error)
    return mac, error


# ---------------------------------------------------------------- USSD login history
# One row per login attempt (not per screen), shown in the app. USSD only tells us the dialling
# number and its network, so that's what identifies the device.

LOGIN_LOG_DAYS = 90
NETWORKS = {"63902": "Safaricom", "63903": "Airtel", "63907": "Telkom", "99999": "Sandbox"}


def log_login(owner, caller, network, session_id, error, resumed=False):
    success = error is None
    if success and session_id and fetch_one(
        "SELECT 1 AS x FROM login_logs WHERE owner = ? AND session_id = ? AND success = 1", (owner, session_id)
    ):
        return  # already logged this session (only happens without SECRET_KEY, when every screen re-checks)
    if success and not resumed and caller != owner:
        notify_event(owner, "ussd_other_phone", caller=masked(caller))
    if resumed:
        reason = "resumed"  # own phone, within the inactivity window: no PIN asked
    elif success:
        reason = "ok"
    elif error.startswith("Too many"):
        reason = "locked"
    else:
        reason = "wrong_pin"
    now = int(time.time())
    db = get_db()
    db.execute(
        sql(
            "INSERT INTO login_logs (owner, caller, network, session_id, success, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        ),
        (owner, caller, network or None, session_id or None, int(success), reason, now),
    )
    db.execute(
        sql("DELETE FROM login_logs WHERE owner = ? AND created_at < ?"), (owner, now - LOGIN_LOG_DAYS * 86400)
    )
    db.commit()


def offer_resume(caller, session_id):
    execute(
        "UPDATE ussd_resume SET offered_in = ?, resume_owner = owner, resume_from = path WHERE caller = ?",
        (session_id, caller),
    )


def trusted_resume(state, caller, offered=False):
    """Continue without the PIN only on the owner's own phone, and only if the account isn't locked.

    A borrowed phone always asks for the PIN again, so the next person to use it can't get in.
    """
    owner = state["resume_owner"] if offered else state["owner"]
    if owner != caller:
        return False
    user = get_user(owner)
    return bool(user) and user["locked_until"] <= time.time()


def masked(phone):
    return "0" + phone[4:7] + "***" + phone[-3:] if phone.startswith("+254") else phone


# ---- USSD session history: one ussd_sessions row per login (a resume continues the same one)

def start_ussd_login(owner, caller, network, state):
    """A fresh phone-number + PIN login. Any login still open on this phone is ended first."""
    if state and state["login_id"]:
        end_ussd_login(state["login_id"], "new login")
    now = int(time.time())
    db = get_db()
    cur = db.cursor()
    query = (
        "INSERT INTO ussd_sessions (owner, caller, network, started_at, last_active) VALUES (?, ?, ?, ?, ?)"
        + (" RETURNING id" if DATABASE_URL else "")
    )
    cur.execute(sql(query), (owner, caller, network or None, now, now))
    login_id = cur.fetchone()["id"] if DATABASE_URL else cur.lastrowid
    db.commit()
    return login_id


def end_ussd_login(login_id, reason):
    if login_id:
        execute(
            "UPDATE ussd_sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
            (int(time.time()), reason, login_id),
        )


def close_idle_ussd_logins():
    """Logins idle past RESUME_SECONDS ended when they expired, not when we noticed."""
    execute(
        "UPDATE ussd_sessions SET ended_at = last_active + ?, end_reason = 'timed out' "
        "WHERE ended_at IS NULL AND last_active < ?",
        (RESUME_SECONDS, int(time.time()) - RESUME_SECONDS),
    )


def current_login(owner, caller, network, session_id, state):
    """The ussd_sessions row this request belongs to: the same one while this network session
    (or a resume of it) goes on, otherwise a new login."""
    if state and state["login_id"] and state["owner"] == owner and session_id in (state["session_id"], state["offered_in"]):
        return state["login_id"]
    return start_ussd_login(owner, caller, network, state)


def run_menu(owner, caller, path, session_id, state, mac, login_id):
    """Show a menu screen and remember it for resuming (never a path that just ran an action)."""
    if path is None:  # 000: log out
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        end_ussd_login(login_id, "logged out on USSD")
        return end("You are logged out of Remote Phone-Book. Dial again to log in.")
    g.action_done = False
    body = menu(owner, caller, path)
    if body.startswith("CON "):
        save_resume(caller, owner, [] if g.action_done else path, session_id, state, mac, login_id)
    else:
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        end_ussd_login(login_id, "session ended")
    return body


def handle(caller, text, session_id="", network=""):
    """Every session starts with: phone number -> PIN -> main menu (numbers register in the app).

    The number entered decides whose phone book opens, so users can reach their contacts
    from any phone. SMS replies and calls go to `caller`, the phone actually in use.
    """
    parts = text.split("*") if text else []
    state = get_resume(caller)

    if state and state["logged_out"]:
        # Logged out from the app. Every screen resends the phone number and PIN, so an open
        # session would otherwise carry on: end it, and don't offer to continue on the next dial.
        if session_id and session_id in (state["session_id"], state["offered_in"]):
            return end("You were logged out from the Remote Phonebook app. Dial again to log in.")
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        state = None

    if not parts:
        fresh = state and time.time() - state["saved_at"] <= RESUME_SECONDS
        if fresh and state["session_id"] != session_id:
            offer_resume(caller, session_id)
            if trusted_resume(state, caller):
                return con(f"Welcome back {masked(state['owner'])}\n1. Continue where you left off\n2. Main menu\n0. Log out")
            return con(f"Welcome back. Enter PIN for {masked(state['owner'])} to continue where you left off:\n0. Start over")
        # Logged-in state that went RESUME_SECONDS without activity is a logout: delete it (and any
        # other expired ones) so nothing about the old login stays usable
        expired = state and state["saved_at"] and not fresh
        close_idle_ussd_logins()
        execute("DELETE FROM ussd_resume WHERE saved_at < ?", (int(time.time()) - RESUME_SECONDS,))
        if expired:
            return con(
                f"Your session expired after {duration_text(RESUME_SECONDS)} of inactivity. "
                "You were logged out for security.\nEnter your phone number:"
            )
        return con("Welcome to Remote Phone-Book\nEnter your phone number:")

    if state and state["offered_in"] == session_id and trusted_resume(state, caller, offered=True):
        # Own phone, still within the inactivity window: continue / main menu (each needs the PIN) / log out
        if parts[0] in ("1", "2"):
            owner = state["resume_owner"]
            if len(parts) == 1:
                return con(f"Enter PIN for {masked(owner)}:")
            mac, rest, screen_ = authenticate(owner, parts[1:], session_id, state, caller, network)
            if screen_:
                return screen_
            start = json.loads(state["resume_from"]) if parts[0] == "1" else []
            login_id = current_login(owner, caller, network, session_id, state)
            return run_menu(owner, caller, resolve_nav(start + rest), session_id, state, mac, login_id)
        if parts[0] != BACK:
            return end("Invalid choice. Please dial again.")
        # Log out: end the active login but keep offered_in, so this session's later inputs
        # ("0*<phone>*<PIN>...") are still read as "start over" below
        end_ussd_login(state["login_id"], "logged out on USSD")
        execute("UPDATE ussd_resume SET resume_owner = NULL, resume_from = NULL, saved_at = 0 WHERE caller = ?", (caller,))
        state = get_resume(caller)
        parts = parts[1:]
        if not parts:
            return con("Logged out.\nEnter your phone number:")

    if state and state["offered_in"] == session_id:
        # This session started with the resume offer: first input is the PIN, or 0 to start over
        if parts[0] != BACK:
            owner = state["resume_owner"]
            if not owner:  # logged out earlier in this session; only "0 ..." (start over) is valid
                return end("Invalid choice. Please dial again.")
            mac, rest, screen_ = authenticate(owner, parts, session_id, state, caller, network)
            if screen_:
                return screen_
            path = resolve_nav(json.loads(state["resume_from"]) + rest)
            login_id = current_login(owner, caller, network, session_id, state)
            return run_menu(owner, caller, path, session_id, state, mac, login_id)
        parts = parts[1:]
        if not parts:
            return con("Enter your phone number:")

    owner = login_phone(parts[0])
    if not owner:
        return end(f"Invalid phone number. Use {LOGIN_PHONE_FORMAT}.")

    if not get_user(owner):
        return end(f"{owner} is not registered. Register in the Remote Phonebook app, then dial again.")

    if len(parts) == 1:
        return con("Enter your PIN:")
    mac, rest, screen_ = authenticate(owner, parts[1:], session_id, state, caller, network)
    if screen_:
        return screen_  # second try, or (after two wrong PINs) how to reset it in the app/website
    login_id = current_login(owner, caller, network, session_id, state)
    return run_menu(owner, caller, resolve_nav(rest), session_id, state, mac, login_id)
