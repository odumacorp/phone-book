"""An organisation's profile: about, phone, email, website, address and logo, set by its admins (Settings) and shown to
its members (app: My organisations, sign-up list) and on the platform (sidebar, Organisations pages).

Logos are public branding: served at /logos/<tenant id>.<ext>?v=<updated_at> so pages and the app can cache them.
"""
import re
import time

from pb.audit import audit
from pb.org import content
from pb.photos import clean
from pb.tenancy.repo import TenantRepo
from pb.text import EMAIL_RE

ABOUT_MAX, LINE_MAX = 600, 120
LOGO_MAX_BYTES = 400 * 1024
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
FIELDS = ("about", "phone", "email", "website", "address")


def _require(principal, tenant_id):
    if not content.can_manage(principal, tenant_id, "tenant.settings.manage"):
        raise content.NotAllowed("You don't have permission to change the organisation's profile.")


def get(tenant_id):
    """The profile ({} values are None when not set), plus logo_url (None: no logo)."""
    found = TenantRepo(tenant_id).one("org_profiles")
    row = dict(found) if found else {}
    out = {k: row.get(k) for k in FIELDS}
    out["logo_url"] = logo_url(tenant_id, row) if row.get("logo_data") else None
    out["updated_at"] = row.get("updated_at") or 0
    return out


def logo_url(tenant_id, row=None):
    if row is None:
        found = TenantRepo(tenant_id).one("org_profiles", columns="logo_mime, logo_data, updated_at")
        row = dict(found) if found else {}
    if not row.get("logo_data"):
        return None
    return f"/logos/{tenant_id}.{EXT.get(row['logo_mime'], 'png')}?v={row.get('updated_at') or 0}"


def logo(tenant_id):
    """(mime, base64) for serving, or None."""
    row = TenantRepo(tenant_id).one("org_profiles", columns="logo_mime, logo_data")
    return (row["logo_mime"], row["logo_data"]) if row and row["logo_data"] else None


def _line(value, limit=LINE_MAX):
    return " ".join(str(value or "").split())[:limit] or None


def save(principal, tenant_id, form):
    """Save the text details (form: about, phone, email, website, address)."""
    _require(principal, tenant_id)
    about = re.sub(r"\n{3,}", "\n\n", str(form.get("about") or "").replace("\r", "").strip())[:ABOUT_MAX] or None
    email = _line(form.get("email"))
    if email and not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email, e.g. info@yourorganisation.co.ke.")
    website = _line(form.get("website"))
    if website:
        if not re.match(r"^https?://", website, re.IGNORECASE):
            website = "https://" + website
        if not re.match(r"^https?://[\w.-]+\.[a-z]{2,}(/\S*)?$", website, re.IGNORECASE):
            raise ValueError("Enter a website like www.yourorganisation.co.ke.")
    phone = _line(form.get("phone"), 40)
    if phone and not re.match(r"^[+\d][\d\s()-]{2,39}$", phone):
        raise ValueError("Enter a phone number like 0712 345 678 or +254 20 123 4567.")
    values = {"about": about, "phone": phone, "email": email, "website": website, "address": _line(form.get("address"), 200)}
    TenantRepo(tenant_id).upsert("org_profiles", [], updated_at=int(time.time()), **values)
    audit("org.profile", tenant_id, ("tenant", tenant_id), {k: v for k, v in values.items() if k != "about"}, actor=principal)


def set_logo(principal, tenant_id, data_url):
    _require(principal, tenant_id)
    mime, data = clean(data_url, LOGO_MAX_BYTES)
    TenantRepo(tenant_id).upsert("org_profiles", [], logo_mime=mime, logo_data=data, updated_at=int(time.time()))
    audit("org.logo", tenant_id, ("tenant", tenant_id), {"mime": mime}, actor=principal)


def remove_logo(principal, tenant_id):
    _require(principal, tenant_id)
    TenantRepo(tenant_id).update("org_profiles", "1 = 1", logo_mime=None, logo_data=None, updated_at=int(time.time()))
    audit("org.logo_removed", tenant_id, ("tenant", tenant_id), None, actor=principal)


def public(tenant_id):
    """What members (and people choosing an organisation) see: the details that are set, and the logo URL."""
    p = get(tenant_id)
    return {k: p[k] for k in (*FIELDS, "logo_url")}
