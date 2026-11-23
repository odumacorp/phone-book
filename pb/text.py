import re
import unicodedata

from pb.config import LIST_SCREEN_LEN, MORE, NAME_MAX, NAV, USSD_MAX_LEN


# ---------------------------------------------------------------- helpers

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
PHONE_JUNK_RE = re.compile(r"[\s\-().]")
PIN_RE = re.compile(r"^\d{4}$")
LOGIN_PHONE_RE = re.compile(r"^(?:\+254\d{9}|0\d{9})$")
LOGIN_PHONE_FORMAT = "+254XXXXXXXXX or 0XXXXXXXXX"


def login_phone(raw):
    """The number someone logs in with must be +254XXXXXXXXX or 0XXXXXXXXX (07.., 01..); returns +254... or None."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
    if not LOGIN_PHONE_RE.match(phone):
        return None
    return "+254" + phone[1:] if phone.startswith("0") else phone


def normalize_phone(raw):
    """Accept 0712345678 / 254712345678 / +254 712-345-678 and return +254... form."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
    if not PHONE_RE.match(phone):
        return None
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return "+254" + phone[1:]
    return "+" + phone


SERVICE_NUMBER_RE = re.compile(r"^\d{3,6}$")  # 999, 112, 1199, 116...

# Free emergency and help lines (Kenya). Calls to these are always dialled from the user's own
# phone, never connected through Africa's Talking.
EMERGENCY_NUMBERS = [  # (number, name for contacts and the app, short name for the USSD list)
    ("999", "Police, fire, ambulance", "Police/Fire/Ambulance"),
    ("112", "Emergency (any mobile)", "Emergency"),
    ("911", "Emergency (any mobile)", "Emergency"),
    ("1199", "Kenya Red Cross", "Red Cross"),
    ("1195", "Gender violence helpline", "GBV helpline"),
    ("116", "Childline Kenya", "Childline"),
]


def is_service_number(phone):
    return bool(SERVICE_NUMBER_RE.match(phone or ""))


def normalize_contact_phone(raw):
    """A contact's number: a full phone number (normalised to +...) or a short service number kept as is."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
    return phone if is_service_number(phone) else normalize_phone(phone)


# Characters that make the network send the whole screen in UCS-2, which fits about half as much
# text: the network then rejects a full screen ("the network is experiencing technical problems").
# Phone contacts often have them (iPhone's ’ in "Mama’s", accents, emoji), so screens use plain text.
PLAIN_SWAPS = str.maketrans({"’": "'", "‘": "'", "‚": ",", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "\u00a0": " "})
GSM_SAFE_RE = re.compile(r"[^A-Za-z0-9 \n!\"#%&'()*+,\-./:;<=>?@_£$¥]")


def plain_text(msg):
    msg = unicodedata.normalize("NFKD", msg.translate(PLAIN_SWAPS))
    return re.sub(r"[ \t]+", " ", GSM_SAFE_RE.sub("", msg))


def con(msg):
    return "CON " + plain_text(msg)[: USSD_MAX_LEN - 4]


def end(msg):
    return "END " + plain_text(msg)[: USSD_MAX_LEN - 4]


def label(position):
    """Number shown for the Nth contact in a list. Numbering continues across pages and skips 98 (More)."""
    return position if position < int(MORE) else position + 1


def position_of(choice, first, count):
    """Index into a page whose first contact is number `first`, or None if `choice` isn't on it."""
    if not choice.isdigit() or choice == MORE:
        return None
    n = int(choice)
    position = n if n < int(MORE) else n - 1
    index = position - first
    return index if 0 <= index < count else None


def format_contacts(rows, numbered=False, short=False, first=1, extra=""):
    """Contacts one per line. short: for a menu screen, where the list plus `extra` (the lines shown
    under it) must stay within LIST_SCREEN_LEN, so names are shortened (more on pages with long numbers)."""
    def build(name_max):
        lines = []
        for i, row in enumerate(rows, first):
            prefix = f"{label(i)}. " if numbered else ""
            name = plain_text(row["name"])[:name_max].rstrip() if short else row["name"]
            phone = local_phone(row["phone"]) if short else row["phone"]
            lines.append(f"{prefix}{name} {phone}")
        return "\n".join(lines)

    if not short:
        return build(None)
    room = LIST_SCREEN_LEN - len("CON ") - len("\n" + NAV) - len(extra)
    for name_max in range(NAME_MAX, 3, -1):
        text = build(name_max)
        if len(text) <= room:
            break
    return text


def local_phone(phone):
    """+254712345678 -> 0712345678, shorter on list screens; other countries keep their + code."""
    return "0" + phone[4:] if phone.startswith("+254") and len(phone) == 13 else phone


NAME_MAX_LEN = 30


def clean_name(raw):
    return " ".join(str(raw or "").split())[:NAME_MAX_LEN]


def local_number(phone):
    return "0" + phone[4:] if phone.startswith("+254") else phone


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
