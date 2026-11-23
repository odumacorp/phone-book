"""The app's view of a member's organisations: notices, contacts and emergency contacts (filtered by their roles)."""
from flask import g, jsonify

from pb.api.auth import api_error, require_auth
from pb.org import content
from pb.tenancy import tenants as tenancy
from pb.web import app


def _contact_json(c):
    return {"id": c["id"], "name": c["name"], "phone": c["phone"], "title": c["title"], "group": c["group_name"]}


@app.route("/api/orgs", methods=["GET"])
@require_auth
def api_orgs():
    orgs = []
    for o in content.my_organisations(g.phone):
        notices = content.visible_notices(o["tenant_id"], g.phone)
        orgs.append({
            "id": o["tenant_id"],
            "name": o["name"],
            "notices": len(notices),
            "urgent": sum(1 for n in notices if n["is_emergency"]),
        })
    return jsonify({"orgs": orgs})


@app.route("/api/orgs/<int:tenant_id>", methods=["GET"])
@require_auth
def api_org(tenant_id):
    if not any(o["tenant_id"] == tenant_id for o in content.my_organisations(g.phone)):
        return api_error("You're not a member of this organisation.", 404)
    t = tenancy.tenant(tenant_id)
    return jsonify({
        "org": {"id": tenant_id, "name": t["name"]},
        "notices": [
            {"id": n["id"], "title": n["title"], "body": n["body"], "urgent": bool(n["is_emergency"]), "pinned": bool(n["pinned"]),
             "at": n["publish_at"]}
            for n in content.visible_notices(tenant_id, g.phone)
        ],
        "contacts": [_contact_json(c) for c in content.visible_contacts(tenant_id, g.phone, emergency=False)],
        "emergency": [_contact_json(c) for c in content.visible_contacts(tenant_id, g.phone, emergency=True)],
    })
