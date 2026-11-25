import threading
from flask import g

from pb.config import AT_API_KEY, AT_SENDER_ID, AT_USERNAME, AT_VOICE_NUMBER, log


sms = None
voice = None
if AT_API_KEY:
    import africastalking

    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    sms = africastalking.SMS
    voice = africastalking.Voice
else:
    log.warning("AT_API_KEY not set - SMS features are disabled")

def is_test_account(phone):
    """Test accounts (scripts/seed_test_accounts.py) use made-up numbers that may belong to real people: never text or
    call them."""
    from pb.db import fetch_one
    return bool(fetch_one("SELECT 1 AS x FROM users WHERE phone = ? AND is_test = 1", (phone,)))


def without_test_accounts(phones):
    return [p for p in phones if not is_test_account(p)]


def send_sms_async(to, message):
    g.action_done = True  # a USSD path that sent an SMS must not be resumed (it would send again)
    if is_test_account(to):
        log.info("SMS to test account %s not sent: %s", to, message[:60])
        return

    def _send():
        try:
            resp = sms.send(message, [to], AT_SENDER_ID)
            log.info("SMS sent to %s: %s", to, resp)
        except Exception:
            log.exception("SMS to %s failed", to)

    threading.Thread(target=_send, daemon=True).start()


def start_call_async(to):
    if is_test_account(to):
        log.info("Call to test account %s not started", to)
        return
    def _call():
        try:
            resp = voice.call(callFrom=AT_VOICE_NUMBER, callTo=[to])
            log.info("Call to %s started: %s", to, resp)
        except Exception:
            log.exception("Call to %s failed", to)

    threading.Thread(target=_call, daemon=True).start()
