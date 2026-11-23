"""
Phone Book USSD service for Africa's Talking (*384*88041#).

Africa's Talking POSTs to /ussd with form fields:
  sessionId, serviceCode, phoneNumber, networkCode, text
`text` is every input in the session joined by "*" (e.g. "1*John*0712345678").
We reply with plain text starting with "CON " (keep session open) or "END " (close it).

The mobile app and website log in with email or phone number + password and
uploads the device's contacts to /api/contacts/sync.

The code lives in the pb/ package; this file is the entry point (gunicorn app:app, python app.py).
"""
import os

from pb.web import app
from pb import admin, jobs, website  # noqa: F401  (importing a module registers its routes)
from pb.api import account, auth, complaints, contacts, notifications  # noqa: F401
from pb.ussd import routes  # noqa: F401
from pb.schema import init_db

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
