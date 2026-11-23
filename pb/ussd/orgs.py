"""USSD "My organisations": the notices, contacts and emergency contacts a member's organisations publish.

Main menu 7 -> pick the organisation (skipped when there's only one) -> 1. Notices / 2. Contacts / 3. Emergency contacts.
Lists are 4 per screen with 98 for more; notices open page by page with 98 and can be texted to the phone in use;
contacts open the usual contact menu (call, SMS, copy number). Every read is filtered by the member's roles
(pb.org.content), and an organisation that allows only members' own phones is closed to borrowed phones.
"""
from pb.config import MORE, PAGE_SIZE, SMS_CHUNK
from pb.messaging import send_sms_async
from pb import messaging
from pb.org import content
from pb.tenancy import tenants as tenancy
from pb.text import format_contacts, plain_text, position_of
from pb.ussd.screens import INVALID, screen

NOTICE_PAGE = 112  # characters of notice text per screen, leaving room for the page and option lines
TITLE_MAX = 24
NOT_A_MEMBER = "You're not in any organisation yet. Ask your organisation to add your phone number."


def has_organisations(owner):
    return bool(content.my_organisations(owner))


def page_and_rest(count, rest, page_size=PAGE_SIZE):
    """Consume leading 98s (next page) from the inputs. Returns (page number, index of the first other input)."""
    page, i = 0, 0
    while i < len(rest) and rest[i] == MORE and (page + 1) * page_size < count:
        page, i = page + 1, i + 1
    return page, i


def text_pages(text, room=NOTICE_PAGE):
    """Split text into screens of at most `room` characters, on word boundaries where possible."""
    words, pages, current = plain_text(text).replace("\r", "").split(" "), [], ""
    for word in words:
        while len(word) > room:  # a very long "word" (a URL): cut it
            if current:
                pages.append(current)
                current = ""
            pages.append(word[:room])
            word = word[room:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) > room:
            pages.append(current)
            current = word
        else:
            current = candidate
    if current or not pages:
        pages.append(current)
    return pages


def organisations_menu(owner, caller, rest, contact_menu):
    orgs = content.my_organisations(owner)
    if not orgs:
        return screen(NOT_A_MEMBER)
    if len(orgs) == 1:
        org = orgs[0]
    else:
        if not rest:
            lines = "\n".join(f"{i}. {plain_text(o['name'])[:TITLE_MAX]}" for i, o in enumerate(orgs[:8], 1))
            return screen(f"My organisations:\n{lines}")
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= min(len(orgs), 8):
            return screen(INVALID)
        org, rest = orgs[int(rest[0]) - 1], rest[1:]
    tenant_id, name = org["tenant_id"], plain_text(org["name"])[:TITLE_MAX]
    if caller != owner and not tenancy.allow_other_phone(tenant_id):
        return screen(f"{name} can only be used from your own phone number. Dial from your phone.")

    notices = content.visible_notices(tenant_id, owner)
    options = []
    if tenancy.module_enabled(tenant_id, "announcements"):
        options.append((f"Notices ({len(notices)})" if notices else "Notices", "notices"))
    if tenancy.module_enabled(tenant_id, "directory"):
        options.append(("Contacts", "contacts"))
    if tenancy.module_enabled(tenant_id, "emergency"):
        options.append(("Emergency contacts", "emergency"))
    if not options:
        return screen(f"{name} has nothing to show on USSD yet.")
    if not rest:
        urgent = next((n for n in notices if n["is_emergency"]), None)
        alert = f"URGENT: {plain_text(urgent['title'])[:40]}\n" if urgent else ""
        lines = "\n".join(f"{i}. {label}" for i, (label, _kind) in enumerate(options, 1))
        return screen(f"{name}\n{alert}{lines}")
    if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(options):
        return screen(INVALID)
    kind, sub = options[int(rest[0]) - 1][1], rest[1:]
    if kind == "notices":
        return notices_screen(org, notices, caller, sub)
    if kind == "emergency":
        rows = content.visible_contacts(tenant_id, owner, emergency=True)
        return contact_list(rows, sub, owner, caller, contact_menu, "No emergency contacts yet.", "Emergency:")
    return contacts_screen(tenant_id, owner, caller, sub, contact_menu)


def notices_screen(org, rows, caller, sub):
    if not rows:
        return screen("No notices right now.")
    page, i = page_and_rest(len(rows), sub)
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    first = page * PAGE_SIZE + 1
    if i == len(sub):
        lines = "\n".join(
            f"{n}. {'!' if r['is_emergency'] else ''}{plain_text(r['title'])[:TITLE_MAX + 4]}" for n, r in enumerate(chunk, first)
        )
        more = f"\n{MORE}. More" if (page + 1) * PAGE_SIZE < len(rows) else ""
        return screen(f"Notices:\n{lines}{more}")
    index = position_of(sub[i], first, len(chunk))
    if index is None:
        return screen(INVALID)
    notice, rest = chunk[index], sub[i + 1:]
    pages = text_pages(f"{notice['title']}\n{notice['body']}")
    shown, j = 0, 0
    while j < len(rest) and rest[j] == MORE and shown + 1 < len(pages):
        shown, j = shown + 1, j + 1
    if j == len(rest):
        nav = f"\n{MORE}. Next ({shown + 1}/{len(pages)})" if shown + 1 < len(pages) else (f"\n({len(pages)}/{len(pages)})" if len(pages) > 1 else "")
        sms_line = "\n1. SMS me this" if messaging.sms is not None else ""
        return screen(pages[shown] + nav + sms_line)
    if rest[j:] == ["1"] and messaging.sms is not None:
        text = f"{org['name']}: {notice['title']}\n{notice['body']}"
        send_sms_async(caller, text[:SMS_CHUNK])
        return screen("Sent to you by SMS.")
    return screen(INVALID)


def contacts_screen(tenant_id, owner, caller, sub, contact_menu):
    rows = content.visible_contacts(tenant_id, owner, emergency=False)
    if not rows:
        return screen("No contacts yet.")
    groups = sorted({r["group_name"] or "Other" for r in rows}, key=lambda g: (g == "Other", g.lower()))
    if len(groups) > 1:
        page, i = page_and_rest(len(groups), sub)
        chunk = groups[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        first = page * PAGE_SIZE + 1
        if i == len(sub):
            lines = "\n".join(f"{n}. {plain_text(g)[:TITLE_MAX]}" for n, g in enumerate(chunk, first))
            more = f"\n{MORE}. More" if (page + 1) * PAGE_SIZE < len(groups) else ""
            return screen(f"Contacts:\n{lines}{more}")
        index = position_of(sub[i], first, len(chunk))
        if index is None:
            return screen(INVALID)
        group = chunk[index]
        rows = [r for r in rows if (r["group_name"] or "Other") == group]
        return contact_list(rows, sub[i + 1:], owner, caller, contact_menu, "No contacts here.", f"{plain_text(group)[:TITLE_MAX]}:")
    return contact_list(rows, sub, owner, caller, contact_menu, "No contacts yet.", "Contacts:")


def contact_list(rows, sub, owner, caller, contact_menu, empty, title):
    if not rows:
        return screen(empty)
    page, i = page_and_rest(len(rows), sub)
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    first = page * PAGE_SIZE + 1
    if i == len(sub):
        more = f"\n{MORE}. More" if (page + 1) * PAGE_SIZE < len(rows) else ""
        body = format_contacts(chunk, numbered=True, short=True, first=first, extra=title + "\n" + more)
        return screen(f"{title}\n{body}{more}")
    index = position_of(sub[i], first, len(chunk))
    if index is None:
        return screen(INVALID)
    row = chunk[index]
    name = f"{row['name']} ({row['title']})" if row["title"] else row["name"]
    return contact_menu(owner, caller, {"name": name, "phone": row["phone"]}, sub[i + 1:])
