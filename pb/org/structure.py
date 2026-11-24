"""An organisation's own departments and groups (classes, streams, fellowships...), created by its operators.

They're the choices for a contact's group, and people can be placed in them (a department supervisor's role covers
the members of their department and its sub-departments). Needs departments.manage.
"""
import time

from pb.audit import audit
from pb.org.content import NotAllowed, can_manage
from pb.tenancy.repo import TenantRepo

NAME_MAX = 40
GROUP_KINDS = {"class": "Class", "stream": "Stream", "fellowship": "Fellowship", "team": "Team", "segment": "Client group",
               "custom": "Other"}


def _require(principal, tenant_id):
    if not can_manage(principal, tenant_id, "departments.manage"):
        raise NotAllowed("You don't have permission to manage groups and departments.")


def _clean(name):
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise ValueError("Enter a name.")
    return name


def departments(tenant_id):
    repo = TenantRepo(tenant_id)
    counts = {}
    for r in repo.all("department_members"):
        counts[r["department_id"]] = counts.get(r["department_id"], 0) + 1
    rows = repo.all("departments", order="name")
    names = {r["id"]: r["name"] for r in rows}
    return [dict(r, members=counts.get(r["id"], 0), parent_name=names.get(r["parent_id"])) for r in rows]


def groups(tenant_id):
    repo = TenantRepo(tenant_id)
    counts = {}
    for r in repo.all("group_members"):
        counts[r["group_id"]] = counts.get(r["group_id"], 0) + 1
    dept_names = {r["id"]: r["name"] for r in repo.all("departments")}
    rows = repo.all("member_groups", order="name")
    return [dict(r, members=counts.get(r["id"], 0), department_name=dept_names.get(r["department_id"]),
                 kind_name=GROUP_KINDS.get(r["kind"], "Other")) for r in rows]


def names(tenant_id):
    """Every department and group name, for the contact Group box."""
    repo = TenantRepo(tenant_id)
    return [r["name"] for r in repo.all("departments", order="name")] + [r["name"] for r in repo.all("member_groups", order="name")]


def _exists(repo, table, name, skip_id=None):
    for r in repo.all(table, "LOWER(name) = ?", (name.lower(),)):
        if r["id"] != skip_id:
            return True
    return False


def _valid_parent(repo, table, parent_id, own_id=None):
    if not parent_id:
        return None
    parent_id = int(parent_id)
    if parent_id == own_id or not repo.one(table, "id = ?", (parent_id,)):
        raise ValueError("Choose a valid parent.")
    return parent_id


def save_department(principal, tenant_id, name, parent_id=None, department_id=None):
    _require(principal, tenant_id)
    repo, name = TenantRepo(tenant_id), _clean(name)
    if _exists(repo, "departments", name, department_id):
        raise ValueError(f"There's already a department called {name}.")
    parent_id = _valid_parent(repo, "departments", parent_id, department_id)
    if department_id:
        if parent_id and department_id in _ancestors(repo, parent_id):
            raise ValueError("A department can't be inside one of its own sub-departments.")
        repo.update("departments", "id = ?", (department_id,), name=name, parent_id=parent_id)
        audit("department.update", tenant_id, ("department", department_id), {"name": name}, actor=principal)
        return department_id
    new_id = repo.insert("departments", name=name, parent_id=parent_id, created_at=int(time.time()))
    audit("department.add", tenant_id, ("department", new_id), {"name": name}, actor=principal)
    return new_id


def _ancestors(repo, department_id):
    seen, current = set(), department_id
    while current and current not in seen:
        seen.add(current)
        row = repo.one("departments", "id = ?", (current,))
        current = row["parent_id"] if row else None
    return seen


def delete_department(principal, tenant_id, department_id):
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    row = repo.one("departments", "id = ?", (department_id,))
    if not row:
        return
    repo.delete("department_members", "department_id = ?", (department_id,))
    for child in repo.all("departments", "parent_id = ?", (department_id,)):
        repo.update("departments", "id = ?", (child["id"],), parent_id=row["parent_id"])
    for g in repo.all("member_groups", "department_id = ?", (department_id,)):
        repo.update("member_groups", "id = ?", (g["id"],), department_id=None)
    repo.delete("departments", "id = ?", (department_id,))
    audit("department.delete", tenant_id, ("department", department_id), {"name": row["name"]}, actor=principal)


def save_group(principal, tenant_id, name, kind="custom", department_id=None, group_id=None):
    _require(principal, tenant_id)
    repo, name = TenantRepo(tenant_id), _clean(name)
    if kind not in GROUP_KINDS:
        kind = "custom"
    if _exists(repo, "member_groups", name, group_id):
        raise ValueError(f"There's already a group called {name}.")
    department_id = _valid_parent(repo, "departments", department_id)
    if group_id:
        repo.update("member_groups", "id = ?", (group_id,), name=name, kind=kind, department_id=department_id)
        audit("group.update", tenant_id, ("group", group_id), {"name": name}, actor=principal)
        return group_id
    new_id = repo.insert("member_groups", name=name, kind=kind, department_id=department_id, created_at=int(time.time()))
    audit("group.add", tenant_id, ("group", new_id), {"name": name, "kind": kind}, actor=principal)
    return new_id


def delete_group(principal, tenant_id, group_id):
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    row = repo.one("member_groups", "id = ?", (group_id,))
    if not row:
        return
    repo.delete("group_members", "group_id = ?", (group_id,))
    for child in repo.all("member_groups", "parent_id = ?", (group_id,)):
        repo.update("member_groups", "id = ?", (child["id"],), parent_id=None)
    repo.delete("member_groups", "id = ?", (group_id,))
    audit("group.delete", tenant_id, ("group", group_id), {"name": row["name"]}, actor=principal)


def place_member(principal, tenant_id, membership_id, placement):
    """Put a member in a department ("d:<id>") or group ("g:<id>"). Used when adding a member."""
    if not placement:
        return
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    kind, _, raw = str(placement).partition(":")
    if not raw.isdigit():
        raise ValueError("Choose a department or group.")
    if kind == "d" and repo.one("departments", "id = ?", (int(raw),)):
        repo.upsert("department_members", ["department_id", "membership_id"], department_id=int(raw), membership_id=membership_id)
    elif kind == "g" and repo.one("member_groups", "id = ?", (int(raw),)):
        repo.upsert("group_members", ["group_id", "membership_id"], group_id=int(raw), membership_id=membership_id)
    else:
        raise ValueError("Choose a department or group.")


def placements_by_member(tenant_id):
    """membership id -> ["Sales", "Form 2"] for the members list."""
    repo = TenantRepo(tenant_id)
    dept = {r["id"]: r["name"] for r in repo.all("departments")}
    grp = {r["id"]: r["name"] for r in repo.all("member_groups")}
    out = {}
    for r in repo.all("department_members"):
        if r["department_id"] in dept:
            out.setdefault(r["membership_id"], []).append(dept[r["department_id"]])
    for r in repo.all("group_members"):
        if r["group_id"] in grp:
            out.setdefault(r["membership_id"], []).append(grp[r["group_id"]])
    return out


def placements_of(tenant_id, membership_id):
    repo = TenantRepo(tenant_id)
    return ([f"d:{r['department_id']}" for r in repo.all("department_members", "membership_id = ?", (membership_id,))]
            + [f"g:{r['group_id']}" for r in repo.all("group_members", "membership_id = ?", (membership_id,))])


def set_placements(principal, tenant_id, membership_id, placements):
    """Make a member's departments and groups exactly these ("d:<id>" / "g:<id>")."""
    _require(principal, tenant_id)
    repo = TenantRepo(tenant_id)
    repo.delete("department_members", "membership_id = ?", (membership_id,))
    repo.delete("group_members", "membership_id = ?", (membership_id,))
    for placement in placements:
        place_member(principal, tenant_id, membership_id, placement)
