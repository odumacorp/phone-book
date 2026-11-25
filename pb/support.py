"""Help and support (/help): contact details, FAQ, a chat assistant and a contact form.

- The contact details are edited by the Super Admin at /platform/settings (settings table, support_*).
- The form files a report into the same list the admin answers at /admin (Reports and complaints).
- The chat assistant always works from the FAQ below. If ANTHROPIC_API_KEY is set (and CHATBOT_AI isn't "off"),
  answers come from Claude instead, grounded on the same FAQ; any API error falls back to the FAQ answer.
  Chat messages are sent to Anthropic in that case, so the page tells people not to type PINs or passwords.
"""
import hmac
import os
import re
import time

from flask import abort, jsonify, render_template, request, session

from pb.api.complaints import client_ip, submit_complaint
from pb.billing.ussd_codes import main_code
from pb.config import EMAIL_FROM, log
from pb.db import execute, fetch_one
from pb.web import app

CHAT_MODEL = "claude-opus-5-5"
CHAT_MAX_INPUT = 500  # characters per message from the browser
CHAT_MAX_TURNS = 12  # history kept per request
CHAT_PER_WINDOW, CHAT_WINDOW = 20, 600  # messages per IP per 10 minutes (per server process)
_chat_hits = {}
_client = None

SUPPORT_FIELDS = {  # key: (label, help)
    "support_email": ("Support email", "Where people can email you"),
    "support_phone": ("Support phone", "e.g. 0712 345 678"),
    "support_whatsapp": ("WhatsApp", "Number for WhatsApp chats, e.g. 0712 345 678"),
    "support_hours": ("Opening hours", "e.g. Mon-Fri 8am-5pm, Sat 9am-1pm"),
    "support_note": ("Office or extra note", "e.g. physical address, or how fast you reply"),
}

FAQ = [
    # (question, answer, keywords). {code} is replaced by the main USSD code.
    ("What is Remote Phonebook?",
     "Remote Phonebook keeps a backup of your contacts so you can reach them from any phone, even without internet: dial "
     "{code}, log in with your phone number and PIN, and your contacts are there. The app and website let you back up "
     "contacts from your phone and manage your account.",
     "what is remote phonebook about service"),
    ("How do I use it on USSD?",
     "Dial {code} from any phone. Enter your phone number, then your 4-digit PIN. You'll see: 1. My contacts, "
     "2. Emergency numbers, 3. Add contact, 4. Search, 5. Delete, 6. SMS me my contacts. If you belong to an organisation, "
     "3 is My organisations and the rest move down one (4. Add contact ... 7. SMS me my contacts). Type a name to search straight away. "
     "On every screen 0 goes back, 00 goes home and 000 logs out.",
     "ussd dial code menu use how phone without internet"),
    ("How do I register?",
     "Open the Remote Phonebook app or website and tap \"New here? Create an account\". Enter your name and phone number, "
     "type the code we text you, then choose a password (for the app) and a 4-digit PIN (for USSD).",
     "register sign up create account new join"),
    ("I forgot my USSD PIN",
     "Log in to the app or website with your phone number and password, then tap Change USSD PIN. You can set a new PIN "
     "there without the old one. If you also forgot your password, use Forgot password first.",
     "forgot pin reset pin change pin new pin"),
    ("I forgot my password",
     "On the app or website login screen tap \"Forgot password?\", enter your phone number or email, type the code we "
     "text you, and choose a new password.",
     "forgot password reset password change password login"),
    ("My account is locked",
     "After 5 wrong PINs (or passwords) the account locks for 15 minutes, to protect it. Wait 15 minutes and try again. "
     "If you don't remember the PIN, set a new one in the app (Change USSD PIN).",
     "locked lock blocked too many wrong attempts"),
    ("How do I back up my contacts?",
     "In the app tap \"Choose from phone\" to pick contacts, or \"Sync all\" to back up every contact on your phone. "
     "On the website you can import a .vcf file exported from your phone's Contacts app. You can also add contacts one "
     "by one, on USSD (Add contact: 3, or 4 if you're in an organisation) or in the app.",
     "backup back up save sync upload import contacts phone vcf"),
    ("How do I get my contacts on a new phone?",
     "Dial {code} on the new phone and choose SMS me my contacts (6, or 7 if you're in an organisation): we text your contacts to that phone (your network "
     "may charge for the SMS). You can also log in to the app on the new phone to see all your contacts.",
     "new phone restore lost stolen get contacts back sms me"),
    ("What are priority contacts?",
     "In the app, tap the star next to up to 5 contacts to make them priority contacts. They're listed first on USSD, and "
     "you can choose to show only them on USSD or to SMS only them.",
     "priority star favourite important"),
    ("Emergency numbers",
     "Dial {code}, log in and choose 2. Emergency numbers: 999, 112, 911, 1199 (Red Cross), 1195 (GBV) and 116 "
     "(Childline). They're free to call. In the app tap Emergency numbers.",
     "emergency police ambulance red cross gbv childline 999 112 help urgent"),
    ("My organisation (school, SACCO, employer)",
     "If your school, SACCO, insurer, law firm or employer uses Remote Phonebook, they add your phone number. Then on "
     "{code} choose 3. My organisations to see their notices, contacts and emergency contacts, or tap My organisations "
     "in the app. Not there? Ask your organisation to add your number.",
     "organisation organization school sacco employer company notices members"),
    ("I manage an organisation",
     "Organisation owners, admins and operators log in at phone-book.odumacorp.com/platform with their Remote Phonebook "
     "phone number (or email) and password. There you post notices and add contacts, emergency contacts and members. "
     "To bring a new organisation on board, contact us using the details on this page.",
     "admin operator manage platform organisation owner post notice enterprise business sign up organisation"),
    ("Is my data safe?",
     "Your contacts are private to your account: nobody else can see them, and they can be stored encrypted. Your PIN and "
     "password are stored scrambled, never as plain text. We'll never ask for your PIN, password or codes, so never share "
     "them with anyone, including us.",
     "safe secure privacy private data security encrypted share"),
    ("Someone else used my account",
     "In the app open \"Where you are logged in\" to see and end other logins, or tap Log out and choose Log out everywhere. "
     "Then change your password and USSD PIN. Login attempts shows who tried to log in on USSD.",
     "hacked someone else stolen account logout everywhere sessions suspicious"),
    ("How much does it cost?",
     "Using Remote Phonebook on USSD and in the app is free for personal use. Your network may charge for SMS, for example "
     "\"SMS me my contacts\". For organisation packages, contact us using the details on this page.",
     "cost price pay charge free money fee"),
]

GREETING = ("Hi! I can help with Remote Phonebook: USSD, PINs and passwords, backing up contacts, your organisation, and more. "
            "What do you need help with?")
NO_MATCH = ("I'm not sure about that one. Try asking another way, check the questions below, or send us a message using "
            "the form on this page and we'll get back to you.")


# ---------------------------------------------------------------- contact details

def support_details():
    details = {}
    for key in SUPPORT_FIELDS:
        row = fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        details[key] = row["value"] if row else ""
    if not details["support_email"] and EMAIL_FROM:
        details["support_email"] = EMAIL_FROM
    return details


def save_support_details(values):
    for key in SUPPORT_FIELDS:
        value = " ".join(str(values.get(key) or "").split())[:200]
        execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def wa_link(number):
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    return f"https://wa.me/{digits}" if digits else ""


# ---------------------------------------------------------------- FAQ assistant (no AI needed)

WORD = re.compile(r"[a-z0-9]+")
STOP = {"i", "my", "the", "a", "an", "to", "do", "how", "is", "it", "can", "what", "on", "in", "of", "for", "me", "and", "you"}


def faq(code=None):
    code = code or main_code()
    return [(q, a.replace("{code}", code), k) for q, a, k in FAQ]


def faq_answer(question):
    words = {w for w in WORD.findall(question.lower()) if w not in STOP}
    if not words or words <= {"hi", "hello", "hey", "habari", "jambo", "mambo", "hujambo", "sasa", "niaje", "thanks", "thank", "asante"}:
        return GREETING
    best, best_score = None, 0
    for q, a, keys in faq():
        vocabulary = set(WORD.findall((q + " " + keys).lower())) - STOP
        score = len(words & vocabulary) + sum(0.5 for w in words for v in vocabulary if len(w) > 3 and w != v and (w in v or v in w))
        if score > best_score:
            best, best_score = a, score
    return best if best_score >= 1 else NO_MATCH


# ---------------------------------------------------------------- Claude assistant (optional)

def ai_enabled():
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("CHATBOT_AI", "on").lower() != "off"


def system_prompt():
    """Stable text (no timestamps), so the prompt cache can reuse it across requests."""
    details = support_details()
    contacts = "\n".join(f"- {SUPPORT_FIELDS[k][0]}: {v}" for k, v in details.items() if v) or "- (none listed)"
    knowledge = "\n\n".join(f"Q: {q}\nA: {a}" for q, a, _k in faq())
    return f"""You are the support assistant on the Remote Phonebook help page (phone-book.odumacorp.com/help), run by Oduma Corp in Kenya.
Remote Phonebook backs up people's contacts and lets them reach them from any phone over USSD, plus an app and website.

Answer only from the facts below. Keep answers short: two to four plain sentences or a few short steps, no markdown headings.
Reply in the language the person writes in (English or Kiswahili).
If the facts don't cover the question, or it needs someone to look at their account, say you can't help with that here and
point them to the contact form on this page or the contact details below. Don't make up features, prices or phone numbers.
Never ask for, or accept, a PIN, password or verification code; if someone shares one, tell them to change it.
You can't see or change anyone's account. Stay on Remote Phonebook topics.

Support contact details:
{contacts}

Facts:
{knowledge}"""


def _anthropic():
    global _client
    if _client is None:
        import anthropic

        # Kept well under gunicorn's 30 s worker timeout: 12 s x (1 try + 1 retry)
        _client = anthropic.Anthropic(timeout=12.0, max_retries=1)
    return _client


def ai_answer(history):
    """Claude's answer to the conversation, or None (caller falls back to the FAQ)."""
    import anthropic

    try:
        response = _anthropic().beta.messages.create(
            model=CHAT_MODEL,
            max_tokens=1024,  # short answers on purpose
            system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},  # simple support chat
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if the model declines, the API retries on a fallback model in the same call
            messages=history,
        )
    except anthropic.RateLimitError:
        log.warning("help chat: rate limited by the API")
        return None
    except anthropic.APITimeoutError:
        log.warning("help chat: API timeout")
        return None
    except anthropic.APIStatusError as e:
        log.warning("help chat: API error %s (request %s)", e.status_code, getattr(e, "request_id", None))
        return None
    except anthropic.APIConnectionError:
        log.warning("help chat: can't reach the API")
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


# ---------------------------------------------------------------- pages

def _csrf_ok(token):
    return bool(session.get("csrf")) and hmac.compare_digest(str(token or ""), session["csrf"])


def _rate_limited(ip):
    now = time.time()
    hits = [t for t in _chat_hits.get(ip, []) if now - t < CHAT_WINDOW]
    if len(hits) >= CHAT_PER_WINDOW:
        _chat_hits[ip] = hits
        return True
    hits.append(now)
    _chat_hits[ip] = hits
    if len(_chat_hits) > 5000:  # forget old visitors
        for key in [k for k, v in _chat_hits.items() if not v or now - v[-1] > CHAT_WINDOW]:
            _chat_hits.pop(key, None)
    return False


@app.route("/help", methods=["GET"])
@app.route("/help/", methods=["GET"])
def help_page():
    from pb.admin import csrf_token

    details = support_details()
    return render_template(
        "help.html", details=details, labels=SUPPORT_FIELDS, wa=wa_link(details.get("support_whatsapp")),
        faq=faq(), code=main_code(), csrf=csrf_token(), sent=request.args.get("sent") == "1",
        error=request.args.get("error"), ai=ai_enabled(), greeting=GREETING,
    )


@app.route("/help", methods=["POST"])
@app.route("/help/", methods=["POST"])
def help_submit():
    from flask import redirect, url_for

    form = request.form
    if not _csrf_ok(form.get("csrf")):
        return redirect(url_for("help_page", error="Your form expired. Please try again.") + "#contact")
    if form.get("website"):  # hidden from people; bots fill it in
        return redirect(url_for("help_page", sent="1") + "#contact")
    name = " ".join(str(form.get("name") or "").split())[:60]
    topic = " ".join(str(form.get("topic") or "").split())[:40] or "Other"
    message = str(form.get("message") or "").strip()
    contact = " ".join(str(form.get("contact") or "").split())[:120]
    full = f"[Help page] {topic}" + (f" - from {name}" if name else "") + f"\n\n{message}"
    failed = submit_complaint(None, contact, full if len(message) >= 5 else message, client_ip())
    if failed:
        return redirect(url_for("help_page", error=failed[0]) + "#contact")
    return redirect(url_for("help_page", sent="1") + "#contact")


@app.route("/help/chat", methods=["POST"])
def help_chat():
    body = request.get_json(silent=True) or {}
    if not _csrf_ok(request.headers.get("X-CSRF-Token")):
        abort(403)
    if _rate_limited(client_ip()):
        return jsonify({"reply": "You've sent a lot of messages. Please wait a few minutes, or use the contact form.", "source": "limit"}), 429
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw:
        return jsonify({"error": "No message."}), 400
    history = []
    for m in raw[-CHAT_MAX_TURNS:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        text = str(m.get("content") or "").strip()[:CHAT_MAX_INPUT]
        if text:
            history.append({"role": m["role"], "content": text})
    while history and history[0]["role"] != "user":  # must start with the user
        history.pop(0)
    if not history or history[-1]["role"] != "user":
        return jsonify({"error": "No message."}), 400
    question = history[-1]["content"]
    if ai_enabled():
        reply = ai_answer(history)
        if reply:
            return jsonify({"reply": reply, "source": "ai"})
    return jsonify({"reply": faq_answer(question), "source": "faq"})
