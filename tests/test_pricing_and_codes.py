"""Prices in the database (seeded placeholders, editing, history) and the USSD code inventory."""
import pytest

from conftest import auth
from pb.audit import audit_entries
from pb.billing import catalogue, ussd_codes
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy

ROOT = rbac.ROOT


def test_money_helpers():
    assert catalogue.kes(2500000) == "KES 25,000"
    assert catalogue.kes(4950) == "KES 49.50"
    assert catalogue.kes(None) == "-"
    assert catalogue.parse_kes("25,000") == 2500000
    assert catalogue.parse_kes("KES 49.5") == 4950
    assert catalogue.parse_kes("", allow_empty=True) is None
    for bad in ("abc", "-5", "1.234", "1e5"):
        with pytest.raises(ValueError):
            catalogue.parse_kes(bad)


def test_placeholder_prices_seeded(ctx):
    assert catalogue.plan("individual_restore")["base_price"] == 5000
    starter = catalogue.plan("enterprise_starter")
    assert (starter["base_price"], starter["included_members"], starter["per_member_price"]) == (250000, 50, 3000)
    assert catalogue.plan_feature("enterprise_pro", "custom_roles") == "1"
    assert catalogue.code_fee("dedicated")["setup_fee"] == 2500000
    assert catalogue.code_fee("extension")["monthly_fee"] == 300000
    kinds = {i["kind"] for i in catalogue.price_list()}
    assert kinds == {"plan", "addon", "code_fee"}


def test_editing_a_price_is_audited_with_old_and_new(ctx):
    changes = catalogue.update_plan("enterprise_starter", ROOT, base_price=300000)
    assert changes == {"base_price": [250000, 300000]}
    assert catalogue.plan("enterprise_starter")["base_price"] == 300000
    entry = audit_entries(action="pricing.plan.update")[0]
    assert entry["target_id"] == "enterprise_starter" and "250000" in entry["details"]
    assert catalogue.update_plan("enterprise_starter", ROOT, base_price=300000) == {}  # no change, no entry
    assert len(audit_entries(action="pricing.plan.update")) == 1


def test_main_code_seeded_and_changeable(ctx, client, register):
    assert ussd_codes.main_code() == "*384*88041#"
    new_id = ussd_codes.add_code(ROOT, "*483*100#", "shared", "Africa's Talking")
    ussd_codes.set_main(ROOT, new_id)
    assert ussd_codes.main_code() == "*483*100#"
    token = register()
    assert client.get("/api/me", headers=auth(token)).get_json()["ussd_code"] == "*483*100#"
    with pytest.raises(ValueError):
        ussd_codes.set_main(ROOT, ussd_codes.add_code(ROOT, "*483*100*7#", "extension"))  # main must be shared


def test_code_validation(ctx):
    for bad in ("384", "*384", "*384*abc#", "**384#", "*384*88041*1*2*3*4#"):
        with pytest.raises(ValueError):
            ussd_codes.add_code(ROOT, bad, "shared")
    ussd_codes.add_code(ROOT, " *384*9# ", "extension")
    with pytest.raises(ValueError):
        ussd_codes.add_code(ROOT, "*384*9#", "extension")  # already listed


def test_dedicated_codes_are_exclusive_and_fees_copied(ctx):
    a = tenancy.create_tenant(ROOT, "A", "company", "enterprise_pro")
    b = tenancy.create_tenant(ROOT, "B", "company", "enterprise_pro")
    code_id = ussd_codes.add_code(ROOT, "*384*5#", "extension")
    ussd_codes.assign(ROOT, a, code_id)
    with pytest.raises(ValueError):
        ussd_codes.assign(ROOT, b, code_id)
    alloc = ussd_codes.allocations(a)[0]
    assert (alloc["setup_fee"], alloc["monthly_fee"]) == (500000, 300000)
    assert ussd_codes.dial_string(alloc) == "*384*5#"
    assert ussd_codes.code(code_id)["status"] == "in_use"
    # a later fee change doesn't touch existing allocations
    catalogue.update_code_fee("extension", ROOT, monthly_fee=400000)
    assert ussd_codes.allocations(a)[0]["monthly_fee"] == 300000
    ussd_codes.release(ROOT, alloc["id"])
    assert ussd_codes.code(code_id)["status"] == "available"
    ussd_codes.assign(ROOT, b, code_id)
    assert ussd_codes.allocations(b)[0]["monthly_fee"] == 400000


def test_shared_code_org_codes(ctx):
    a = tenancy.create_tenant(ROOT, "A", "church", "enterprise_starter") if tenancy.edition("church")["active"] else None
    assert a is None or a  # church is inactive by default
    with pytest.raises(ValueError):
        tenancy.create_tenant(ROOT, "Church", "church", "enterprise_starter")
    a = tenancy.create_tenant(ROOT, "A", "company", "enterprise_starter")
    b = tenancy.create_tenant(ROOT, "B", "company", "enterprise_starter")
    main = next(c for c in ussd_codes.codes() if c["is_main"])
    ussd_codes.assign(ROOT, a, main["id"], "2045")
    assert ussd_codes.dial_string(ussd_codes.allocations(a)[0]) == "*384*88041*2045#"
    with pytest.raises(ValueError):
        ussd_codes.assign(ROOT, b, main["id"], "2045")  # taken
    ussd_codes.assign(ROOT, b, main["id"])  # automatic org code
    assert tenancy.tenant(b)["org_code"] and tenancy.tenant(b)["org_code"] != "2045"
    assert ussd_codes.allocations(a)[0]["monthly_fee"] == 0  # shared code: included in plan


def test_changing_code_ends_previous_allocation(ctx):
    a = tenancy.create_tenant(ROOT, "A", "company", "enterprise_pro")
    main = next(c for c in ussd_codes.codes() if c["is_main"])
    ussd_codes.assign(ROOT, a, main["id"])
    ext = ussd_codes.add_code(ROOT, "*384*6#", "extension")
    ussd_codes.assign(ROOT, a, ext)
    active = ussd_codes.allocations(a)
    assert len(active) == 1 and active[0]["code_type"] == "extension"
    assert len(ussd_codes.allocations(a, active_only=False)) == 2


def test_starter_plan_gets_edition_modules_as_addons(ctx):
    starter = tenancy.create_tenant(ROOT, "Small school", "schools", "enterprise_starter")
    standard = tenancy.create_tenant(ROOT, "Big school", "schools", "enterprise_standard")
    assert tenancy.module_enabled(starter, "members")  # core
    assert not tenancy.module_enabled(starter, "school_results")  # edition module: add-on on Starter
    assert tenancy.module_enabled(standard, "school_results")


def test_public_config_has_main_code(client):
    assert client.get("/api/config").get_json() == {"ussd_code": "*384*88041#"}
