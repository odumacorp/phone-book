"""The Flask app object. Every module registers its routes on it."""
import os
from flask import Flask

from pb.config import ROOT, SECRET_KEY, log


app = Flask(__name__, root_path=ROOT)  # templates/ at the repository root, as before
app.secret_key = SECRET_KEY or None  # signs the admin login cookie
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),  # https-only cookie on Render; plain http works locally
    PERMANENT_SESSION_LIFETIME=8 * 3600,
)


if not SECRET_KEY:
    log.warning("SECRET_KEY not set - mobile app login is disabled")
