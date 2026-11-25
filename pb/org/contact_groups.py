"""Contact groups: named sets of organisation contacts (e.g. "Claims team"), given to departments, groups or roles.

Everyone a contact group is given to finds its contacts in their own contact list (as long as they may see each
contact). A contact left out for one person on their member page stays out for them. audience NULL = not given to
anyone yet; "" = everyone in the organisation; else roles / "d:<id>" / "g:<id>".
"""
import json
import time

from pb.audit import audit
from pb.org import content
from pb.tenancy.repo import TenantRepo

NAME_MAX = 40


def _require(principal, tenant_id):
    if not content.can_manage(principal, tenant_id, "contacts.manage"):
        raise content.NotAllowed("You don't have permission to manage contact groups.")


def groups(tenant_id):
    repo = TenantRepo(tenant_id)
    items = {}
    live = {c["id"] for c in content.contacts(tenant_id)}
    for r in repo.all("contact_group_items"):
        if r["contact_id"] in live:
            items.setdefault(r["group_id"], []).append(r["contact_id"])
    return [dict(g, contact_ids=items.get(g["id"], [])) for g in repo.all("contact_groups", order="name")]


def group(tenant_id, group_id):
    return next((g for g in groups(tenant_id) if g["id"] == group_id), None)


def save(principal, tenant_id, name, group_id=None):
    _require(principal, tenant_id)
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise ValueError("Give the contact group a name, e.g. Claims team.")
    repo = TenantRepo(tenant_id)
    for g in repo.all("contact_groups", "LOWER(name) = ?", (name.lower(),)):
        if g["id"] != group_id:
            raise ValueError(f"There's already a contact group called {name}.")
    if group_id:
        repo.update("contact_groups", "id = ?", (group_id,), name=name)
        audit("contact_group.rename", tenant_id, ("contact_group", group_id), {"name": name}, actor=principal)
        return group_id
    new_id = repo.insert("contact_groups", name=name, audience=None, created_at=int(time.time()))
    audit("contact_group.add", tenant_id, ("contact_group", new_id), {"name": name}, actor=principal)
    return new_id


def update(principal, tenant_id, group_id, contact_ids, audience_values, given):
    """Set a contact group's contacts and who gets it (given=False: nobody yet)."""
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    if not repo.one("contact_groups", "id = ?", (group_id,)):
        raise ValueError("That contact group no longer exists.")
    live = {c["id"] for c in content.contacts(tenant_id)}
    wanted = sorted({int(i) for i in contact_ids if str(i).isdigit()} & live)
    repo.delete("contact_group_items", "group_id = ?", (group_id,))
    for cid in wanted:
        repo.insert("contact_group_items", returning_id=False, group_id=group_id, contact_id=cid)
    audience = content.parse_audience(audience_values, tenant_id) if given else None
    repo.update("contact_groups", "id = ?", (group_id,), audience=audience)
    audit("contact_group.update", tenant_id, ("contact_group", group_id), {"contacts": wanted, "given_to": audience}, actor=principal)


def delete(principal, tenant_id, group_id):
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    row = repo.one("contact_groups", "id = ?", (group_id,))
    if not row:
        return
    repo.delete("contact_group_items", "group_id = ?", (group_id,))
    repo.delete("contact_groups", "id = ?", (group_id,))
    audit("contact_group.delete", tenant_id, ("contact_group", group_id), {"name": row["name"]}, actor=principal)


def for_tag(tenant_id, tag):
    """Every contact group, with whether people with this tag (a role, "d:<id>" or "g:<id>") get it."""
    out = []
    for g in groups(tenant_id):
        everyone = g["audience"] == ""
        out.append(dict(g, for_everyone=everyone,
                        on=everyone or (g["audience"] is not None and tag in content.audience_roles(g["audience"]))))
    return out


def set_for_tag(principal, tenant_id, tag, group_ids):
    """Give exactly these contact groups to people with this tag (adds or removes it in each group's "given to").
    Groups given to everyone stay as they are; a group left with nobody becomes "not given to anyone yet"."""
    _require(principal, tenant_id)
    content.parse_audience([tag], tenant_id)  # a real role, department or group
    repo = TenantRepo(tenant_id)
    wanted, changed = {int(i) for i in group_ids if str(i).isdigit()}, 0
    for g in for_tag(tenant_id, tag):
        if g["for_everyone"] or (g["id"] in wanted) == g["on"]:
            continue
        current = content.audience_roles(g["audience"]) if g["audience"] else []
        current = [*current, tag] if not g["on"] else [e for e in current if e != tag]
        audience = json.dumps(sorted(set(current))) if current else None
        repo.update("contact_groups", "id = ?", (g["id"],), audience=audience)
        audit("contact_group.update", tenant_id, ("contact_group", g["id"]), {"given_to": audience}, actor=principal)
        changed += 1
    return changed


def given_contacts(tenant_id, tags):
    """{contact id: ["contact group <name>", ...]}: the contact groups that give it to someone with these tags."""
    out = {}
    for g in groups(tenant_id):
        if g["audience"] is None or not content.matches(g["audience"], tags):
            continue
        for cid in g["contact_ids"]:
            out.setdefault(cid, []).append(f"contact group {g['name']}")
    return out
