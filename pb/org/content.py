"""Organisation content: contacts (including emergency contacts) and notices that admins/operators publish,
and what each member is allowed to see of them.

Who may manage: contacts.manage (contacts), emergency.manage (emergency contacts), announcements.publish (notices),
held for the whole organisation. Who may see: active members whose roles match the item's audience
("" = everyone in the organisation). The same functions serve USSD, the app API and the portal.
"""
import datetime
import json
import time

from pb.audit import audit
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from pb.text import local_number, login_phone, normalize_contact_phone

NAME_MAX, TITLE_MAX, GROUP_MAX = 60, 60, 40
NOTICE_TITLE_MAX, NOTICE_BODY_MAX = 80, 2000


class NotAllowed(Exception):
    pass


# ---------------------------------------------------------------- audience

def parse_audience(values, tenant_id):
    """Form values -> stored audience. Each value is a role key, "d:<department id>" or "g:<group id>".
    Empty (or "everyone") -> "" (everyone)."""
    values = [v for v in (values or []) if v and v != "everyone"]
    if not values:
        return ""
    roles = {r["key"] for r in rbac.roles_for_tenant(tenant_id)}
    repo = TenantRepo(tenant_id)
    for v in values:
        kind, _, raw = v.lstrip("!").partition(":")
        if kind == "m" and raw.isdigit():
            if not repo.one("memberships", "id = ?", (int(raw),)):
                raise ValueError("One of the people chosen is no longer a member.")
        elif kind in ("d", "g") and raw.isdigit():
            table = "departments" if kind == "d" else "member_groups"
            if not repo.one(table, "id = ?", (int(raw),)):
                raise ValueError("One of the departments or groups no longer exists.")
        elif v not in roles:
            raise ValueError(f"Unknown role: {v}")
    return json.dumps(sorted(set(values)))


def audience_roles(stored):
    """The stored audience as a list (roles, "d:<id>", "g:<id>"); [] = everyone."""
    return json.loads(stored) if stored else []


def _tree_up(rows):
    """{id: {id and all its ancestors}} for departments or groups."""
    parent = {r["id"]: r["parent_id"] for r in rows}
    out = {}
    for node in parent:
        seen, cur = set(), node
        while cur and cur not in seen:
            seen.add(cur)
            cur = parent.get(cur)
        out[node] = seen
    return out


def member_tags(tenant_id):
    """{phone: tags} for active members. Tags: role keys ("member" if none), "d:<id>" for their departments and the
    departments above them, "g:<id>" for their groups and the groups above them (and a group's department)."""
    repo = TenantRepo(tenant_id)
    members = repo.all("memberships", "status = 'active'")
    roles_of = {}
    for a in rbac.tenant_assignments(tenant_id):
        roles_of.setdefault(a["principal"], set()).add(a["key"])
    dept_up = _tree_up(repo.all("departments"))
    group_rows = repo.all("member_groups")
    group_up = _tree_up(group_rows)
    group_dept = {g["id"]: g["department_id"] for g in group_rows}
    places = {}
    for r in repo.all("department_members"):
        places.setdefault(r["membership_id"], set()).update(f"d:{d}" for d in dept_up.get(r["department_id"], ()))
    for r in repo.all("group_members"):
        tags = places.setdefault(r["membership_id"], set())
        for gid in group_up.get(r["group_id"], ()):
            tags.add(f"g:{gid}")
            if group_dept.get(gid):
                tags.update(f"d:{d}" for d in dept_up.get(group_dept[gid], ()))
    return {m["user_phone"]: (roles_of.get(rbac.user_principal(m["user_phone"])) or {"member"}) | places.get(m["id"], set())
            | {f"m:{m['id']}"} for m in members}


def viewer_roles(tenant_id, phone):
    """A person's tags in an organisation (roles, departments, groups: see member_tags), or None if not an active member."""
    return member_tags(tenant_id).get(phone)


def matches(stored_audience, tags):
    """Does someone with these tags fall in the audience? Entries starting with "!" leave people out
    (e.g. "!m:12": everyone else but member 12)."""
    entries = audience_roles(stored_audience)
    tags = set(tags)
    if {e[1:] for e in entries if e.startswith("!")} & tags:
        return False
    wanted = [e for e in entries if not e.startswith("!")]
    return not wanted or bool(set(wanted) & tags)


def _visible(item, tags):
    return matches(item["audience"], tags)


MANAGE_PERMISSIONS = ("announcements.publish", "contacts.manage", "emergency.manage", "members.manage", "tenant.settings.manage")


def manages_any_organisation(phone):
    """Can this person use the organisation workspace in /platform (any manage permission in any of their organisations)?"""
    principal = rbac.user_principal(phone)
    return any(rbac.can(principal, p, o["tenant_id"]) for o in my_organisations(phone) for p in MANAGE_PERMISSIONS)


def my_organisations(phone):
    """Organisations where this person is an active member and the organisation is usable."""
    return [o for o in tenancy.memberships_of(phone) if o["status"] not in ("suspended", "closed")]


# ---------------------------------------------------------------- managing (portal)

def can_manage(principal, tenant_id, permission):
    return rbac.can(principal, permission, tenant_id) or rbac.can(principal, "platform.tenants.manage")


def _require(principal, tenant_id, permission):
    if not can_manage(principal, tenant_id, permission):
        raise NotAllowed("You don't have permission to do that here.")


LIVE = "deleted_at IS NULL"  # deleted items stay in the table as history


def contacts(tenant_id, emergency=None):
    where, params = LIVE, ()
    if emergency is not None:
        where, params = f"{LIVE} AND is_emergency = ?", (1 if emergency else 0,)
    return TenantRepo(tenant_id).all("org_contacts", where, params, order="is_emergency DESC, sort, group_name, name")


def contact(tenant_id, contact_id):
    return TenantRepo(tenant_id).one("org_contacts", f"id = ? AND {LIVE}", (contact_id,))


def _clean_contact(form, tenant_id):
    name = " ".join(str(form.get("name") or "").split())[:NAME_MAX]
    phone = normalize_contact_phone(str(form.get("phone") or ""))
    if not name:
        raise ValueError("Enter the contact's name.")
    if not phone:
        raise ValueError("Enter a valid phone number (e.g. 0712 345 678, or a short code like 999).")
    return {
        "name": name,
        "phone": phone,
        "title": " ".join(str(form.get("title") or "").split())[:TITLE_MAX] or None,
        "group_name": " ".join(str(form.get("group_name") or "").split())[:GROUP_MAX] or None,
        "email": str(form.get("email") or "").strip()[:120] or None,
        "audience": parse_audience(form.get("audience"), tenant_id),
        "in_phone_books": 1 if form.get("in_phone_books") else 0,
        "phone_book_audience": parse_audience(form.get("pb_audience"), tenant_id) if form.get("in_phone_books") else "",
    }


def save_contact(principal, tenant_id, form, emergency, contact_id=None):
    _require(principal, tenant_id, "emergency.manage" if emergency else "contacts.manage")
    values = _clean_contact(form, tenant_id)
    if contact_id and values["in_phone_books"]:
        # choices made for single people on their member page ("m:" / "!m:") stay when the contact is edited
        old = contact(tenant_id, contact_id)
        personal = [e for e in audience_roles(old["phone_book_audience"] if old else "") if e.lstrip("!").startswith("m:")]
        if personal:
            values["phone_book_audience"] = json.dumps(sorted(set(audience_roles(values["phone_book_audience"]) + personal)))
    repo, now = TenantRepo(tenant_id), int(time.time())
    if contact_id:
        if not repo.update("org_contacts", "id = ?", (contact_id,), updated_at=now, **values):
            raise ValueError("That contact no longer exists.")
        audit("org_contact.update", tenant_id, ("org_contact", contact_id), {"name": values["name"]}, actor=principal)
        return contact_id
    new_id = repo.insert("org_contacts", is_emergency=1 if emergency else 0, created_by=principal, created_at=now, updated_at=now, **values)
    audit("org_contact.add", tenant_id, ("org_contact", new_id), {"name": values["name"], "emergency": bool(emergency)}, actor=principal)
    return new_id


def members_for_contacts(tenant_id, emergency=False):
    """Active members who aren't contacts yet (on this list), with what a contact needs: name, phone, title, group."""
    from pb.org import structure

    taken = {c["phone"] for c in contacts(tenant_id, emergency=emergency)}
    placed = structure.placements_by_member(tenant_id)
    out = []
    for m in TenantRepo(tenant_id).all("memberships", "status = 'active'", order="display_name, user_phone"):
        if m["user_phone"] in taken:
            continue
        groups = placed.get(m["id"], [])
        out.append({"id": m["id"], "name": m["display_name"] or local_number(m["user_phone"]), "phone": m["user_phone"],
                    "local": local_number(m["user_phone"]), "title": m["title"] or "", "group": groups[0] if groups else ""})
    return out


def add_contacts_from_members(principal, tenant_id, membership_ids, emergency=False, group=None, audience="", in_phone_books=False):
    """Turn members into contacts in one go. group: one group for all, or None to use each member's own department or
    group. Members already on the list are skipped. Returns (added ids, skipped count)."""
    _require(principal, tenant_id, "emergency.manage" if emergency else "contacts.manage")
    wanted = {int(i) for i in membership_ids if str(i).isdigit()}
    if not wanted:
        raise ValueError("Tick at least one member.")
    available = {m["id"]: m for m in members_for_contacts(tenant_id, emergency)}
    repo, now, added = TenantRepo(tenant_id), int(time.time()), []
    for membership_id in sorted(wanted):
        m = available.get(membership_id)
        if not m:
            continue
        new_id = repo.insert("org_contacts", name=m["name"][:NAME_MAX], phone=m["phone"], title=(m["title"] or None),
                             group_name=None if emergency else ((group if group is not None else m["group"]) or None),
                             email=None, audience=audience, is_emergency=1 if emergency else 0, created_by=principal,
                             in_phone_books=1 if in_phone_books else 0,
                             created_at=now, updated_at=now)
        added.append(new_id)
    audit("org_contact.add_from_members", tenant_id, ("org_contact", ",".join(map(str, added))),
          {"count": len(added), "emergency": bool(emergency)}, actor=principal)
    return added, len(wanted) - len(added)


def selected_only(member):
    """The contact ids picked for a member whose list shows only those, or None if their list follows the defaults."""
    if not member or member["list_mode"] != "selected":
        return None
    try:
        return {int(i) for i in json.loads(member["list_selection"] or "[]")}
    except ValueError:
        return set()


def member_list_contacts(tenant_id, membership_id):
    """For a member's edit page: the organisation contacts they can see, each with whether it's in their own list."""
    member = TenantRepo(tenant_id).one("memberships", "id = ? AND status = 'active'", (membership_id,))
    if not member:
        return []
    tags = member_tags(tenant_id).get(member["user_phone"], set())
    picked = selected_only(member)
    out = []
    for c in contacts(tenant_id):
        if not matches(c["audience"], tags):
            continue
        if picked is not None:
            shown = c["id"] in picked
        else:
            shown = bool(c["in_phone_books"]) and matches(c["phone_book_audience"], tags)
        out.append(dict(c, shown=shown))
    return out


def set_member_list_mode(principal, tenant_id, membership_id, mode):
    """"defaults" (role/department/group lists plus changes for them) or "selected" (only contacts picked for them).
    Switching to "selected" starts from what they have now."""
    if not can_manage(principal, tenant_id, "contacts.manage"):
        raise NotAllowed("You don't have permission to change contact lists.")
    if mode not in ("defaults", "selected"):
        raise ValueError("Choose how their list works.")
    repo = TenantRepo(tenant_id)
    current = [c["id"] for c in member_list_contacts(tenant_id, membership_id) if c["shown"]]
    repo.update("memberships", "id = ?", (membership_id,), list_mode=mode,
                list_selection=json.dumps(sorted(current)) if mode == "selected" else None)
    audit("member.list_mode", tenant_id, ("member", membership_id), {"mode": mode}, actor=principal)


def reset_member_list(principal, tenant_id, membership_id):
    """Undo every change made for this one person: their list follows the defaults again."""
    if not can_manage(principal, tenant_id, "contacts.manage"):
        raise NotAllowed("You don't have permission to change contact lists.")
    tag, repo = f"m:{membership_id}", TenantRepo(tenant_id)
    for c in repo.all("org_contacts"):
        entries = audience_roles(c["phone_book_audience"])
        if tag not in entries and "!" + tag not in entries:
            continue
        had_people = [e for e in entries if not e.startswith("!")]
        entries = [e for e in entries if e not in (tag, "!" + tag)]
        left = [e for e in entries if not e.startswith("!")]
        in_books = 0 if (had_people and not left) else c["in_phone_books"]
        repo.update("org_contacts", "id = ?", (c["id"],), in_phone_books=in_books,
                    phone_book_audience=json.dumps(sorted(set(entries))) if entries and in_books else "")
    repo.update("memberships", "id = ?", (membership_id,), list_mode=None, list_selection=None)
    audit("member.list_reset", tenant_id, ("member", membership_id), None, actor=principal)


def set_member_list_contacts(principal, tenant_id, membership_id, wanted_ids):
    """Make exactly these organisation contacts show in one member's own list (only contacts they can see).
    Adds "m:<id>" or "!m:<id>" to each contact's list audience as needed. Returns how many changed."""
    tag = f"m:{membership_id}"
    wanted = {int(i) for i in wanted_ids if str(i).isdigit()}
    repo, changed = TenantRepo(tenant_id), 0
    member = repo.one("memberships", "id = ?", (membership_id,))
    if selected_only(member) is not None:  # their list shows only what's picked for them: keep it on their row
        if not can_manage(principal, tenant_id, "contacts.manage"):
            raise NotAllowed("You don't have permission to change contact lists.")
        allowed = {c["id"] for c in member_list_contacts(tenant_id, membership_id)}
        picked = sorted(wanted & allowed)
        repo.update("memberships", "id = ?", (membership_id,), list_selection=json.dumps(picked))
        audit("member.list_contacts", tenant_id, ("member", membership_id), {"contacts": picked, "mode": "selected"}, actor=principal)
        return 1
    for c in member_list_contacts(tenant_id, membership_id):
        should = c["id"] in wanted
        if should == c["shown"]:
            continue
        _require(principal, tenant_id, "emergency.manage" if c["is_emergency"] else "contacts.manage")
        original = audience_roles(c["phone_book_audience"])
        entries = [e for e in original if e not in (tag, "!" + tag)]
        tags = member_tags(tenant_id).get(_phone_of(repo, membership_id), set())
        in_books = 1
        if should:
            if not c["in_phone_books"]:
                entries = [tag]  # it was in nobody's list: now just this person's
            elif not matches(json.dumps(entries) if entries else "", tags):
                entries.append(tag)
        else:
            had_people = [e for e in original if not e.startswith("!")]
            left = [e for e in entries if not e.startswith("!")]
            if had_people and not left:
                in_books, entries = 0, []  # it was in this person's list only
            elif matches(json.dumps(entries) if entries else "", tags):
                entries.append("!" + tag)  # others keep it; leave this person out
        repo.update("org_contacts", "id = ?", (c["id"],), in_phone_books=in_books,
                    phone_book_audience=json.dumps(sorted(set(entries))) if entries else "")
        changed += 1
    if changed:
        audit("member.list_contacts", tenant_id, ("member", membership_id), {"contacts": sorted(wanted)}, actor=principal)
    return changed


def _list_entries(c):
    entries = audience_roles(c["phone_book_audience"])
    return [e for e in entries if not e.startswith("!")], [e[1:] for e in entries if e.startswith("!")]


def list_reason(c, tags, membership_id, names):
    """Why a contact is (or isn't) in one member's list, in words."""
    tag = f"m:{membership_id}"
    wanted, left_out = _list_entries(c)
    if tag in left_out:
        return "left out for them"
    if not c["in_phone_books"]:
        return "not in anyone's list"
    if tag in wanted:
        return "just for them"
    if not wanted:
        return "everyone who can see it" if not (set(left_out) & set(tags)) else "left out for their group"
    hits = [names.get(e, e) for e in wanted if e in tags]
    return ("for " + ", ".join(hits)) if hits else "for other people"


def list_defaults(tenant_id, tag):
    """For the Default contact lists page: each contact, and whether people with this tag (a role, "d:<id>" or "g:<id>")
    get it in their lists by default."""
    out = []
    for c in contacts(tenant_id):
        wanted, left_out = _list_entries(c)
        on = bool(c["in_phone_books"]) and tag not in left_out and (not wanted or tag in wanted)
        out.append(dict(c, on=on, everyone=bool(c["in_phone_books"]) and not wanted))
    return out


def set_list_defaults(principal, tenant_id, tag, wanted_ids):
    """Make exactly these contacts part of the default lists of everyone with this tag. Returns how many changed."""
    parse_audience([tag], tenant_id)  # a real role, department or group
    wanted_ids = {int(i) for i in wanted_ids if str(i).isdigit()}
    repo, changed = TenantRepo(tenant_id), 0
    for c in list_defaults(tenant_id, tag):
        should = c["id"] in wanted_ids
        if should == c["on"]:
            continue
        _require(principal, tenant_id, "emergency.manage" if c["is_emergency"] else "contacts.manage")
        entries = [e for e in audience_roles(c["phone_book_audience"]) if e not in (tag, "!" + tag)]
        wanted = [e for e in entries if not e.startswith("!")]
        in_books = 1
        if should:
            if not c["in_phone_books"]:
                entries = [e for e in entries if e.startswith("!")] + [tag]
            elif wanted:  # an explicit list: add this group to it (if it was "everyone", removing the "!" was enough)
                entries.append(tag)
        else:
            if not wanted and c["in_phone_books"]:
                entries.append("!" + tag)  # everyone who can see it, except this group
            elif not wanted:
                in_books = 0
            # (an explicit list: removing the tag above was enough)
            if c["in_phone_books"] and wanted and not [e for e in entries if not e.startswith("!")]:
                in_books, entries = 0, []
        repo.update("org_contacts", "id = ?", (c["id"],), in_phone_books=in_books,
                    phone_book_audience=json.dumps(sorted(set(entries))) if entries else "")
        changed += 1
    if changed:
        audit("org_contact.list_defaults", tenant_id, ("audience", tag), {"contacts": sorted(wanted_ids)}, actor=principal)
    return changed


def _phone_of(repo, membership_id):
    row = repo.one("memberships", "id = ?", (membership_id,))
    return row["user_phone"] if row else None


def delete_contact(principal, tenant_id, contact_id):
    row = contact(tenant_id, contact_id)
    if not row:
        return
    _require(principal, tenant_id, "emergency.manage" if row["is_emergency"] else "contacts.manage")
    TenantRepo(tenant_id).update("org_contacts", "id = ?", (contact_id,), deleted_at=int(time.time()), deleted_by=principal)
    audit("org_contact.delete", tenant_id, ("org_contact", contact_id), {"name": row["name"]}, actor=principal)


def notices(tenant_id, include_expired=False):
    """Live notices for the operator (expired ones are in History unless include_expired)."""
    where, params = LIVE, ()
    if not include_expired:
        where, params = f"{LIVE} AND (expires_at IS NULL OR expires_at > ?)", (int(time.time()),)
    return TenantRepo(tenant_id).all("notices", where, params, order="pinned DESC, is_emergency DESC, publish_at DESC")


def notice(tenant_id, notice_id):
    return TenantRepo(tenant_id).one("notices", f"id = ? AND {LIVE}", (notice_id,))


def save_notice(principal, tenant_id, form, notice_id=None):
    _require(principal, tenant_id, "announcements.publish")
    title = " ".join(str(form.get("title") or "").split())[:NOTICE_TITLE_MAX]
    body = str(form.get("body") or "").strip()[:NOTICE_BODY_MAX]
    if not title:
        raise ValueError("Give the notice a title.")
    if not body:
        raise ValueError("Write the notice.")
    expires_at = notice_expiry(form)
    values = {
        "title": title, "body": body, "audience": parse_audience(form.get("audience"), tenant_id),
        "pinned": 1 if form.get("pinned") else 0, "is_emergency": 1 if form.get("is_emergency") else 0,
        "expires_at": expires_at,
    }
    repo, now = TenantRepo(tenant_id), int(time.time())
    if notice_id:
        if not repo.update("notices", "id = ?", (notice_id,), updated_at=now, **values):
            raise ValueError("That notice no longer exists.")
        audit("notice.update", tenant_id, ("notice", notice_id), {"title": title}, actor=principal)
        return notice_id
    new_id = repo.insert("notices", publish_at=now, created_by=principal, created_at=now, updated_at=now, **values)
    audit("notice.add", tenant_id, ("notice", new_id), {"title": title, "emergency": bool(values["is_emergency"])}, actor=principal)
    return new_id


EAT = datetime.timezone(datetime.timedelta(hours=3))
EXPIRY_CHOICES = [  # (value, label) for the "Show it until" list; value = days
    ("", "Until I delete it"), ("1", "1 day"), ("3", "3 days"), ("7", "1 week"), ("14", "2 weeks"), ("30", "1 month"),
    ("90", "3 months"), ("180", "6 months"), ("365", "1 year"), ("date", "Pick a date…"),
]
MAX_EXPIRY_DAYS = 730


def notice_expiry(form, now=None):
    """When a notice stops showing: None (until deleted), N days from now, or the end of a chosen date (Kenya time)."""
    now = now or int(time.time())
    mode = str(form.get("expires_mode", form.get("expires_days")) or "").strip()
    if not mode:
        return None
    if mode == "date":
        raw = str(form.get("expires_date") or "").strip()
        try:
            day = datetime.date.fromisoformat(raw)
        except ValueError:
            raise ValueError("Pick the last day to show the notice.") from None
        end = int(datetime.datetime.combine(day, datetime.time(23, 59, 59), EAT).timestamp())
        if end <= now:
            raise ValueError("The last day can't be in the past.")
        if end - now > MAX_EXPIRY_DAYS * 86400:
            raise ValueError("Choose a date within the next two years.")
        return end
    if not mode.isdigit() or not 1 <= int(mode) <= MAX_EXPIRY_DAYS:
        raise ValueError("Choose how long to show the notice.")
    return now + int(mode) * 86400


def delete_notice(principal, tenant_id, notice_id):
    row = notice(tenant_id, notice_id)
    if not row:
        return
    _require(principal, tenant_id, "announcements.publish")
    TenantRepo(tenant_id).update("notices", "id = ?", (notice_id,), deleted_at=int(time.time()), deleted_by=principal)
    audit("notice.delete", tenant_id, ("notice", notice_id), {"title": row["title"]}, actor=principal)


# ---------------------------------------------------------------- history: deleted and expired items

def history(tenant_id):
    """Deleted notices and contacts (newest first), and notices that have expired, for the History page."""
    repo, now = TenantRepo(tenant_id), int(time.time())
    return {
        "notices": repo.all("notices", "deleted_at IS NOT NULL", order="deleted_at DESC", limit=200),
        "expired": repo.all("notices", f"{LIVE} AND expires_at IS NOT NULL AND expires_at <= ?", (now,),
                            order="expires_at DESC", limit=200),
        "contacts": repo.all("org_contacts", "deleted_at IS NOT NULL", order="deleted_at DESC", limit=200),
    }


def repost_notice(principal, tenant_id, notice_id, form):
    """Bring a deleted or expired notice back, as new: posted now, shown for the chosen time (default: until deleted)."""
    _require(principal, tenant_id, "announcements.publish")
    repo = TenantRepo(tenant_id)
    row = repo.one("notices", "id = ?", (notice_id,))
    if not row:
        raise ValueError("That notice no longer exists.")
    now = int(time.time())
    repo.update("notices", "id = ?", (notice_id,), deleted_at=None, deleted_by=None, publish_at=now,
                expires_at=notice_expiry(form, now), updated_at=now)
    audit("notice.repost", tenant_id, ("notice", notice_id), {"title": row["title"]}, actor=principal)


def restore_contact(principal, tenant_id, contact_id):
    repo = TenantRepo(tenant_id)
    row = repo.one("org_contacts", "id = ? AND deleted_at IS NOT NULL", (contact_id,))
    if not row:
        raise ValueError("That contact isn't in the history.")
    _require(principal, tenant_id, "emergency.manage" if row["is_emergency"] else "contacts.manage")
    repo.update("org_contacts", "id = ?", (contact_id,), deleted_at=None, deleted_by=None, updated_at=int(time.time()))
    audit("org_contact.restore", tenant_id, ("org_contact", contact_id), {"name": row["name"]}, actor=principal)


def purge(principal, tenant_id, table, item_id):
    """Delete for good, from the History page (only deleted or expired items)."""
    repo = TenantRepo(tenant_id)
    if table == "notices":
        row = repo.one("notices", "id = ? AND (deleted_at IS NOT NULL OR (expires_at IS NOT NULL AND expires_at <= ?))",
                       (item_id, int(time.time())))
        permission = "announcements.publish"
    elif table == "org_contacts":
        row = repo.one("org_contacts", "id = ? AND deleted_at IS NOT NULL", (item_id,))
        permission = "emergency.manage" if row and row["is_emergency"] else "contacts.manage"
    else:
        raise ValueError("Unknown item.")
    if not row:
        raise ValueError("Only items in the history can be deleted for good.")
    _require(principal, tenant_id, permission)
    repo.delete(table, "id = ?", (item_id,))
    audit(f"{'notice' if table == 'notices' else 'org_contact'}.purge", tenant_id, (table, item_id),
          {"name": row["title"] if table == "notices" else row["name"]}, actor=principal)


# ---------------------------------------------------------------- what a member sees (USSD, app)

def visible_notices(tenant_id, phone, roles=None):
    """What a member sees. roles: view as these roles instead (the portal preview)."""
    roles = set(roles) if roles else viewer_roles(tenant_id, phone)
    if roles is None or not tenancy.module_enabled(tenant_id, "announcements"):
        return []
    now = int(time.time())
    rows = TenantRepo(tenant_id).all(
        "notices", f"{LIVE} AND publish_at <= ? AND (expires_at IS NULL OR expires_at > ?)", (now, now),
        order="pinned DESC, is_emergency DESC, publish_at DESC",
    )
    return [r for r in rows if _visible(r, roles)]


def visible_contacts(tenant_id, phone, emergency, roles=None):
    roles = set(roles) if roles else viewer_roles(tenant_id, phone)
    module = "emergency" if emergency else "directory"
    if roles is None or not tenancy.module_enabled(tenant_id, module):
        return []
    rows = TenantRepo(tenant_id).all("org_contacts", f"{LIVE} AND is_emergency = ?", (1 if emergency else 0,),
                                     order="sort, group_name, name")
    return [r for r in rows if _visible(r, roles)]


# ---------------------------------------------------------------- members (portal)

def add_member_with_role(principal, tenant_id, phone, name, role_key, title=None):
    """Add someone to the organisation and give them a role (only roles the operator may give)."""
    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to add members.")
    as_platform = rbac.can(principal, "platform.tenants.manage")
    role_key = role_key or "member"
    if not tenancy.role_offered(tenant_id, role_key):
        raise ValueError("That role isn't used in this organisation.")
    if role_key == tenancy.SUPER_ADMIN:
        raise ValueError("There's only one Super Admin. Add them as a member, then use Edit to make them the Super Admin.")
    new_phone = login_phone(str(phone or ""))
    if role_key == tenancy.ADMIN and new_phone:
        tenancy.check_admin_room(tenant_id, rbac.user_principal(new_phone))
    membership_id = tenancy.add_member(principal, tenant_id, phone, name)
    member_phone = TenantRepo(tenant_id).one("memberships", "id = ?", (membership_id,))["user_phone"]
    title = " ".join(str(title or "").split())[:60] or None
    if title:
        TenantRepo(tenant_id).update("memberships", "id = ?", (membership_id,), title=title)
    rbac.grant(principal, tenant_id, rbac.user_principal(member_phone), role_key or "member", as_platform=as_platform)
    return membership_id


def remove_member(principal, tenant_id, membership_id):
    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to remove members.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ?", (membership_id,))
    if not member:
        return
    member_principal = rbac.user_principal(member["user_phone"])
    if member_principal == principal:
        raise ValueError("You can't remove yourself.")
    if tenancy.super_admin(tenant_id) == member_principal:
        raise ValueError("This is the Super Admin. Make someone else the Super Admin first.")
    as_platform = rbac.can(principal, "platform.tenants.manage")
    for a in rbac.tenant_assignments(tenant_id):
        if a["principal"] == member_principal:
            rbac.revoke(principal, tenant_id, a["id"], as_platform=as_platform)
    repo.update("memberships", "id = ?", (membership_id,), status="inactive", left_at=int(time.time()))
    audit("member.remove", tenant_id, ("member", membership_id), {"phone": member["user_phone"]}, actor=principal)


ADMIN_ROLES = ("org_owner", "org_admin")


def member_role(tenant_id, phone):
    """(role key, scope) of a member's main role: their first assignment, or ("member", tenant) if they have none."""
    for a in rbac.tenant_assignments(tenant_id):
        if a["principal"] == rbac.user_principal(phone):
            return a["key"], (a["scope_type"], a["scope_id"])
    return "member", ("tenant", None)


def update_member(principal, tenant_id, membership_id, form):
    """Change a member's name, member number, role (and the department a supervisor runs) and departments/groups."""
    from pb.org import structure

    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to change members.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ? AND status = 'active'", (membership_id,))
    if not member:
        raise ValueError("That member isn't in the organisation any more.")
    member_principal = rbac.user_principal(member["user_phone"])
    as_platform = rbac.can(principal, "platform.tenants.manage")
    changes = {}

    name = " ".join(str(form.get("name") or "").split())[:80] or None
    member_no = " ".join(str(form.get("member_no") or "").split())[:40] or None
    title = " ".join(str(form.get("title") or "").split())[:60] or None
    if (name, member_no, title) != (member["display_name"], member["member_no"], member["title"]):
        repo.update("memberships", "id = ?", (membership_id,), display_name=name, member_no=member_no, title=title)
        changes["details"] = {"name": [member["display_name"], name], "member_no": [member["member_no"], member_no],
                              "title": [member["title"], title]}

    role_key = form.get("role") or "member"
    if not tenancy.role_offered(tenant_id, role_key):
        raise ValueError("That role isn't used in this organisation.")
    scope = ("tenant", None)
    if role_key == "department_supervisor":
        dept = str(form.get("supervises") or "")
        if not dept.isdigit():
            raise ValueError("Choose the department this supervisor runs.")
        scope = ("department", int(dept))
    old_key, old_scope = member_role(tenant_id, member["user_phone"])
    if (role_key, scope) != (old_key, old_scope):
        if member_principal == principal:
            raise ValueError("You can't change your own role. Ask the Super Admin (or Oduma Corp).")
        if old_key == tenancy.SUPER_ADMIN:
            raise ValueError("This is the Super Admin. To change that, make someone else the Super Admin: they take over the role.")
        if role_key == tenancy.SUPER_ADMIN:
            if not (as_platform or tenancy.super_admin(tenant_id) == principal):
                raise NotAllowed("Only the Super Admin (or Oduma Corp) can hand over the Super Admin role.")
            tenancy.appoint_owner(principal, tenant_id, member["user_phone"], as_platform=as_platform)
            audit("member.update", tenant_id, ("member", membership_id), {**changes, "role": [old_key, role_key]}, actor=principal)
            if "placements_sent" in form:
                structure.set_placements(principal, tenant_id, membership_id, form.get("placements") or [])
            return
        if role_key == tenancy.ADMIN:
            tenancy.check_admin_room(tenant_id, member_principal)
        current = [a for a in rbac.tenant_assignments(tenant_id) if a["principal"] == member_principal]
        rbac.grant(principal, tenant_id, member_principal, role_key, scope, as_platform=as_platform)  # checks the operator may
        for a in current:
            if (a["key"], (a["scope_type"], a["scope_id"])) != (role_key, scope):
                rbac.revoke(principal, tenant_id, a["id"], as_platform=as_platform)
        changes["role"] = [old_key, role_key]

    if "placements_sent" in form:
        structure.set_placements(principal, tenant_id, membership_id, form.get("placements") or [])
    if changes:
        audit("member.update", tenant_id, ("member", membership_id), changes, actor=principal)


def add_role(principal, tenant_id, name, base_key, permission_keys=None):
    """An organisation's own role, e.g. "Nurse" that works like Staff. With the same permissions as an existing role
    it's just a new name and always allowed; different permissions need a plan with custom roles. People can only
    make roles with permissions they hold themselves."""
    import re as _re
    from pb.db import execute, insert_id

    staff = rbac.can(principal, "platform.tenants.manage")
    if not (staff or rbac.can(principal, "tenant.admins.manage", tenant_id) or rbac.can(principal, "roles.custom", tenant_id)):
        raise NotAllowed("Only the Super Admin and admins can add roles.")
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("Give the role a name, e.g. Nurse.")
    offered = dict(tenancy.available_roles(tenant_id))
    if base_key not in offered or base_key == tenancy.ADMIN:
        raise ValueError("Choose which role it works like.")
    if name.lower() in {n.lower() for n in offered.values()} | {"organisation super admin"}:
        raise ValueError(f"There's already a role called {name}.")
    base_perms = rbac.role_permissions(rbac.role_by_key(base_key, tenant_id)["id"])
    perms = set(permission_keys) if permission_keys else set(base_perms)
    if perms != base_perms and not tenancy.plan_allows(tenant_id, "custom_roles"):
        raise NotAllowed("Changing what a role can do is part of the Pro plan. You can still add it with the same permissions.")
    if not staff and any(not rbac.can(principal, p, tenant_id) for p in perms):
        raise NotAllowed("You can only make roles with permissions you have yourself.")
    key = "custom_" + (_re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "role")[:30]
    existing = rbac.role_by_key(key, tenant_id)
    if existing and existing["tenant_id"] == tenant_id:
        raise ValueError(f"There's already a role called {name}.")
    role_id = insert_id(
        "INSERT INTO roles (tenant_id, key, name, scope, is_system, created_by, created_at) VALUES (?, ?, ?, 'tenant', 0, ?, ?)",
        (tenant_id, key, name, principal, int(time.time())),
    )
    for permission in sorted(perms):
        execute("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)", (role_id, permission))
    rbac.clear_cache()
    audit("role.create", tenant_id, ("role", role_id), {"key": key, "name": name, "like": base_key,
                                                         "same_permissions": perms == base_perms}, actor=principal)
    return role_id


def delete_role(principal, tenant_id, role_id):
    from pb.db import execute, fetch_one
    if not (rbac.can(principal, "tenant.admins.manage", tenant_id) or rbac.can(principal, "roles.custom", tenant_id)
            or rbac.can(principal, "platform.tenants.manage")):
        raise NotAllowed("Only the Super Admin and admins can remove roles.")
    role = fetch_one("SELECT id, key, name FROM roles WHERE id = ? AND tenant_id = ? AND is_system = 0", (role_id, tenant_id))
    if not role:
        raise ValueError("Only roles your organisation added can be removed.")
    if any(a["key"] == role["key"] for a in rbac.tenant_assignments(tenant_id)):
        raise ValueError(f"Some people still have the {role['name']} role. Give them another role first.")
    execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    execute("DELETE FROM roles WHERE id = ? AND tenant_id = ?", (role_id, tenant_id))
    rbac.clear_cache()
    audit("role.delete", tenant_id, ("role", role_id), {"name": role["name"]}, actor=principal)


def former_members(tenant_id):
    return TenantRepo(tenant_id).all("memberships", "status = 'inactive'", order="left_at DESC", limit=200)


def readd_member(principal, tenant_id, membership_id):
    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to add members.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ? AND status <> 'active'", (membership_id,))
    if not member:
        raise ValueError("That person is already a member.")
    repo.update("memberships", "id = ?", (membership_id,), status="active", left_at=None)
    rbac.grant(principal, tenant_id, rbac.user_principal(member["user_phone"]), "member",
               as_platform=rbac.can(principal, "platform.tenants.manage"))
    audit("member.readd", tenant_id, ("member", membership_id), {"phone": member["user_phone"]}, actor=principal)


def profile_organisations(phone):
    """For the person's own profile: each organisation with their role(s), title, member number, departments and groups."""
    from pb.org import structure

    out = []
    for o in my_organisations(phone):
        tenant_id = o["tenant_id"]
        member = TenantRepo(tenant_id).one("memberships", "user_phone = ? AND status = 'active'", (phone,))
        roles = [a["name"] for a in rbac.tenant_assignments(tenant_id) if a["principal"] == rbac.user_principal(phone)]
        out.append({
            "id": tenant_id,
            "name": o["name"],
            "roles": roles or ["Member"],
            "title": member["title"] if member else None,
            "member_no": member["member_no"] if member else None,
            "groups": structure.placements_by_member(tenant_id).get(member["id"], []) if member else [],
            "joined_at": member["joined_at"] if member else None,
        })
    return out


def last_logins(phones):
    """phone -> (time, "app"/"USSD") of the latest successful login, for the members list."""
    from pb.db import fetch_all

    phones = list(phones)
    if not phones:
        return {}
    marks = ", ".join("?" for _ in phones)
    seen = {}
    for r in fetch_all(f"SELECT owner, MAX(created_at) AS t FROM app_sessions WHERE owner IN ({marks}) GROUP BY owner", tuple(phones)):
        seen[r["owner"]] = (r["t"], "app")
    for r in fetch_all(f"SELECT owner, MAX(created_at) AS t FROM login_logs WHERE success = 1 AND owner IN ({marks}) GROUP BY owner",
                       tuple(phones)):
        if r["t"] and (r["owner"] not in seen or r["t"] > seen[r["owner"]][0]):
            seen[r["owner"]] = (r["t"], "USSD")
    return seen


def member_rows(tenant_id):
    """Active members with their role names, for the portal."""
    roles_by_principal = {}
    for a in rbac.tenant_assignments(tenant_id):
        roles_by_principal.setdefault(a["principal"], []).append(a["name"])
    rows = TenantRepo(tenant_id).all("memberships", "status = 'active'", order="display_name, user_phone")
    return [dict(r, roles=roles_by_principal.get(rbac.user_principal(r["user_phone"]), ["Member"]),
                 role_key=member_role(tenant_id, r["user_phone"])[0]) for r in rows]
