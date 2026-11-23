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
