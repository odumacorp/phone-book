import os
from flask import send_from_directory

from pb.config import ROOT
from pb.web import app


@app.route("/health", methods=["GET"])
def health():
    return "Phone Book USSD service is running"


# ---------------------------------------------------------------- website
# The Remote Phonebook website: the mobile app built for the web (npm run build:web in mobile/),
# served from the same address as the API.

WEB_DIR = os.path.join(ROOT, "web")


@app.route("/", methods=["GET"])
def website():
    if not os.path.exists(os.path.join(WEB_DIR, "index.html")):
        return health()
    response = send_from_directory(WEB_DIR, "index.html")
    response.headers["Cache-Control"] = "no-cache"  # always pick up a new build
    return response


@app.route("/_expo/<path:filename>", methods=["GET"])
def website_assets(filename):
    response = send_from_directory(os.path.join(WEB_DIR, "_expo"), filename)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # file names change with each build
    return response


@app.route("/assets/<path:filename>", methods=["GET"])
def website_images(filename):
    """Images bundled into the website (e.g. the logo in the header); names change with their content."""
    response = send_from_directory(os.path.join(WEB_DIR, "assets"), filename)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.route("/<any('favicon.ico', 'favicon.png', 'apple-touch-icon.png'):filename>", methods=["GET"])
def website_icons(filename):
    """The Oduma Corp icon: browser tab and iPhone home screen."""
    response = send_from_directory(WEB_DIR, filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
