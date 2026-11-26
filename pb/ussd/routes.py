import time
from flask import Response, request
from xml.sax.saxutils import quoteattr

from pb.config import log
from pb.contacts import pop_pending_call
from pb.text import PIN_RE, end, normalize_phone
from pb.ussd.session import handle
from pb.web import app


@app.route("/voice", methods=["POST"])
def voice_callback():
    """Africa's Talking voice callback: when the caller answers, dial the contact they picked."""
    if request.values.get("isActive") != "1":
        return Response("", mimetype="text/plain")
    contact_phone = None
    for number in (request.values.get("destinationNumber"), request.values.get("callerNumber")):
        if number and not contact_phone:
            contact_phone = pop_pending_call(number)
    if contact_phone:
        body = f"<Dial phoneNumbers={quoteattr(contact_phone)} />"
    else:
        body = "<Say>Sorry, this call could not be connected.</Say>"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
    return Response(xml, mimetype="application/xml")


@app.route("/ussd", methods=["POST"])
def ussd():
    session_id = request.values.get("sessionId", "")
    service_code = request.values.get("serviceCode", "")
    phone_number = request.values.get("phoneNumber", "")
    text = request.values.get("text", "")
    # PINs (login, setup, reset, change) are 4-digit inputs - never log them
    logged = ["<pin>" if PIN_RE.match(value) else value for value in text.split("*")]
    log.info("session=%s code=%s phone=%s text=%r", session_id, service_code, phone_number, "*".join(logged))

    caller = normalize_phone(phone_number)
    if not caller:
        body = end("Could not identify your phone number.")
    else:
        from pb.dashboard import track
        started = time.monotonic()
        try:
            body = handle(caller, text, session_id, request.values.get("networkCode", ""))
        except Exception:
            log.exception("Error handling USSD request")
            track.ussd_failure(caller, "error")
            body = end("Remote Phone-Book is busy right now. Please dial again.")
        else:
            if time.monotonic() - started > track.USSD_TIMEOUT_SECONDS:
                track.ussd_failure(caller, "timeout")  # too slow: the network will have given up

    return Response(body, mimetype="text/plain")


@app.route("/sms/delivery/<token>", methods=["POST"])
def sms_delivery_report(token):
    """Africa's Talking SMS delivery reports (set this URL in the Africa's Talking dashboard: SMS > Delivery reports).
    The token in the address keeps anyone else from posting fake reports."""
    import hmac as _hmac
    from pb.dashboard import track
    if not _hmac.compare_digest(token, track.delivery_report_token()):
        return Response("", status=404)
    track.delivery_report(request.values.get("id"), request.values.get("status"))
    return Response("OK", mimetype="text/plain")
