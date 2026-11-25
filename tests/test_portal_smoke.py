"""Every platform portal page renders for a Super Admin (catches template errors)."""

def test_every_page_renders(staff_client):
    client, csrf = staff_client()
    resp = client.post("/platform/tenants", data={"csrf": csrf, "name": "Smoke", "edition": "sacco", "plan": "enterprise_pro",
                                                  "owner_phone": "0711000001"})
    tenant_page = resp.headers["Location"]
    pages = ["/platform/", "/platform/tenants", tenant_page, "/platform/pricing", "/platform/pricing/plan/enterprise_pro",
             "/platform/pricing/plan/individual_restore", "/platform/pricing/plan/enterprise_custom", "/platform/pricing/addon/auto_backup",
             "/platform/pricing/code_fee/dedicated", "/platform/ussd-codes", "/platform/staff", "/platform/settings", "/platform/audit",
             "/admin"]
    for p in pages:
        r = client.get(p)
        assert r.status_code in (200,), (p, r.status_code)


def test_page_titles_are_plain_text():
    """Scripts must never end up inside {% block title %} (they'd show as the browser tab's title and never run)."""
    import glob
    import os
    import re
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    for path in glob.glob(os.path.join(root, "**", "*.html"), recursive=True):
        for title in re.findall(r"{% block title %}(.*?){% endblock %}", open(path).read(), re.S):
            assert "<" not in title and "combo_script" not in title, path
