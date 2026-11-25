"""Total price after all the calculations: base, extra members, volume tiers, annual discount."""
from pb.billing.catalogue import quote

SEAT = {"model": "seat", "base_price": 250000, "included_members": 50, "per_member_price": 3000,
        "annual_discount_pct": 10, "billing_interval": "month"}


def test_monthly_with_extra_members():
    q = quote(SEAT, members=120)
    assert q["total"] == 250000 + 70 * 3000 and q["per"] == "month"  # KES 2,500 + 70 x 30 = KES 4,600
    assert [label for label, _ in q["lines"]][1].startswith("70 extra members")


def test_within_included_members_is_just_the_base():
    assert quote(SEAT, members=30)["total"] == 250000


def test_volume_tier_and_annual_discount():
    tiers = [{"from_members": 100, "per_member_price": 2000}, {"from_members": 1000, "per_member_price": 1000}]
    q = quote(SEAT, members=150, tiers=tiers, interval="year")
    monthly = 250000 + 100 * 2000
    assert q["total"] == monthly * 12 - round(monthly * 12 * 0.10) and q["per"] == "year"
    assert q["per_month"] == round(q["total"] / 12)
    assert any("Annual discount 10%" in label for label, _ in q["lines"])


def test_other_models():
    assert quote({**SEAT, "model": "recurring", "billing_interval": "year"})["total"] == 225000
    assert quote({**SEAT, "model": "per_transaction"}, transactions=3)["total"] == 750000
    assert quote({**SEAT, "model": "quoted"})["total"] is None


def test_plan_page_and_organisation_page_show_the_total(staff_client, client):
    from conftest import app_module
    from pb.rbac import engine as rbac
    from pb.tenancy import tenants as tenancy
    sc, _ = staff_client()
    page = sc.get("/platform/pricing/plan/enterprise_standard").get_data(as_text=True)
    assert 'id="calc"' in page and "Total price" in page
    with app_module.app.test_request_context():
        t = tenancy.create_tenant(rbac.ROOT, "Priced Org", "company", "enterprise_standard")
        tenancy.add_member(rbac.ROOT, t, "0711000001", "One")
    page = sc.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert "Total for 1 member" in page or "quoted" in page
