import datetime
import gzip
import os

from flask import render_template, request, send_from_directory

from pb.config import ROOT
from pb.web import app


@app.route("/health", methods=["GET"])
def health():
    return "Phone Book USSD service is running"


# ---------------------------------------------------------------- website
# phone-book.odumacorp.com/ is the public landing page (templates/site/index.html, files in site/).
# The web app (the mobile app built for the web: npm run build:web in mobile/) lives at /app; /app?start=register
# opens the sign-up screen.

WEB_DIR = os.path.join(ROOT, "web")
SITE_DIR = os.path.join(ROOT, "site")
PHOTOS_DIR = os.path.join(SITE_DIR, "img", "photos")


@app.route("/", methods=["GET"])
def landing():
    """The public website. Photos appear once their files are in site/img/photos/ (<name>.webp); until then a styled
    placeholder describes the shot."""
    photos = {f[:-5] for f in os.listdir(PHOTOS_DIR) if f.endswith(".webp")} if os.path.isdir(PHOTOS_DIR) else set()
    body = render_template("site/index.html", year=datetime.date.today().year, photos=photos).encode()
    response = app.response_class(body, mimetype="text/html")
    if "gzip" in request.headers.get("Accept-Encoding", ""):  # ~90 KB of HTML becomes ~20 KB on slow mobile data
        response.set_data(gzip.compress(body, 6))
        response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.route("/site/<path:filename>", methods=["GET"])
def site_files(filename):
    """The landing page's font and images."""
    response = send_from_directory(SITE_DIR, filename)
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.route("/app", methods=["GET"])
@app.route("/app/", methods=["GET"])
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


ANDROID_APK_URL = "https://github.com/odumacorp/phone-book/releases/latest/download/remote-phonebook.apk"


@app.route("/android", methods=["GET"])
@app.route("/download/android", methods=["GET"])
def android_download():
    """phone-book.odumacorp.com/android: the newest Android app (APK, published as a GitHub release)."""
    from flask import redirect
    return redirect(ANDROID_APK_URL, code=302)
