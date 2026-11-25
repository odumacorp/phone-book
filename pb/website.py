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


@app.route("/logos/<int:tenant_id>.<any(jpg, png, webp):ext>", methods=["GET"])
def org_logo(tenant_id, ext):
    """An organisation's logo (public branding, like a website logo). The ?v= in links changes when it does."""
    import base64

    from flask import abort, request

    from pb.org import profile
    from pb.tenancy import tenants as tenancy
    t = tenancy.tenant(tenant_id)
    found = profile.logo(tenant_id) if t and t["status"] != "closed" else None
    if not found:
        abort(404)
    mime, data = found
    response = app.response_class(base64.b64decode(data), mimetype=mime)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.args.get("v") else "public, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response
