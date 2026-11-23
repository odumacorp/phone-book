from pb.config import AT_VOICE_NUMBER, MORE, PAGE_SIZE, SMS_CHUNK, SMS_MAX, SMS_MAX_MESSAGES
from pb.contacts import add_contact, delete_contact, find_contact_by_phone, list_contacts, priority_only, save_pending_call, search_contacts, sms_all
from pb.messaging import send_sms_async, sms, start_call_async, voice
from pb.text import EMERGENCY_NUMBERS, con, end, format_contacts, is_service_number, local_number, normalize_contact_phone, position_of
from pb.users import get_user
from pb.ussd.screens import INVALID, MAIN_MENU, MENU_ACTIONS, NO_PRIORITY, screen


# ---------------------------------------------------------------- menu logic
# Every request replays the whole path, so actions (save, delete, send, call) only run when
# the path ends exactly at them; anything typed after a result is rejected, not re-run.

def menu(owner, caller, parts):
    if not parts:
        return con(MAIN_MENU)

    choice, rest = MENU_ACTIONS.get(parts[0]), parts[1:]
    if parts[0].strip() and not parts[0].strip().isdigit():
        return menu(owner, caller, ["4"] + parts)  # typed a name on the main menu: search straight away
    only = priority_only(owner)
    none_yet = NO_PRIORITY if only else "You have no contacts yet."

    # 3. Add contact: 3 -> name -> phone
    if choice == "add":
        if len(rest) == 0:
            return screen("Enter contact name:")
        name = rest[0].strip()
        if not name:
            return screen("Name cannot be empty.")
        if len(rest) == 1:
            return screen(f"Enter phone number for {name}:")
        if len(rest) > 2:
            return screen(INVALID)
        phone = normalize_contact_phone(rest[1])
        if not phone:
            return screen("Invalid phone number.")
        add_contact(owner, name, phone)
        return screen(f"Saved {name} - {phone}")

    # 4. Search: 4 -> query -> pick contact -> action
    if choice == "search":
        if len(rest) == 0:
            return screen("Enter name to search:")
        query = rest[0].strip()
        rows = search_contacts(owner, query, only)
        if not rows:
            return screen(f"No contacts match '{query}'.")
        if len(rest) == 1:
            return screen(format_contacts(rows, numbered=True, short=True, extra="\n" + PICK_HINT) + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, rows, rest[1], rest[2:])

    # 1. List, paginated: 1 -> 98 (more) -> 98 ... -> pick contact -> action
    if choice == "list":
        rows = list_contacts(owner, only)
        if not rows:
            return screen(none_yet)
        page, i = 0, 0
        while i < len(rest) and rest[i] == MORE and (page + 1) * PAGE_SIZE < len(rows):
            page, i = page + 1, i + 1
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        first = page * PAGE_SIZE + 1
        if i == len(rest):
            more = f"\n{MORE}. More" if (page + 1) * PAGE_SIZE < len(rows) else ""
            body = format_contacts(chunk, numbered=True, short=True, first=first, extra=more + "\n" + PICK_HINT)
            return screen(body + more + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, chunk, rest[i], rest[i + 1:], first)

    # 5. Delete: 5 -> pick number -> 1 confirm / 2 cancel
    if choice == "delete":
        rows = list_contacts(owner, only)[:PAGE_SIZE]
        if not rows:
            return screen(none_yet)
        if len(rest) == 0:
            title = "Select contact to delete:\n"
            return screen(title + format_contacts(rows, numbered=True, short=True, extra=title))
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(rows):
            return screen("Invalid selection.")
        contact = rows[int(rest[0]) - 1]
        if len(rest) == 1:
            return screen(f"Delete {contact['name']} {contact['phone']}?\n1. Yes\n2. No")
        if len(rest) > 2:
            return screen(INVALID)
        if rest[1] == "1":
            delete_contact(owner, contact["id"])
            return screen(f"{contact['name']} deleted.")
        return screen("Cancelled.")

    # 6. SMS full phone book to the phone in use
    if choice == "sms":
        if rest:
            return screen(INVALID)
        if sms is None:
            return screen("SMS service is not available right now.")
        send_all = sms_all(get_user(owner))
        rows = list_contacts(owner, priority_only=not send_all)
        if not rows:
            return screen("You have no contacts yet." if send_all else NO_PRIORITY)
        messages = contact_sms(rows, "Your contacts" if send_all else "Your priority contacts")
        for text in messages:
            send_sms_async(caller, text)
        what = f"your {len(rows)} contacts" if send_all else f"your {len(rows)} priority contacts"
        count = f" in {len(messages)} messages" if len(messages) > 1 else ""
        return screen(f"You will receive {what} by SMS{count} shortly.")

    # 2. Emergency numbers: 2 -> pick -> 1 call now (dial it yourself) / 2 add to contacts
    if choice == "emergency":
        if len(rest) == 0:
            lines = "\n".join(f"{i}. {num} {short}" for i, (num, _label, short) in enumerate(EMERGENCY_NUMBERS, 1))
            return screen(f"Emergency (free):\n{lines}")
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(EMERGENCY_NUMBERS):
            return screen(INVALID)
        number, label, _short = EMERGENCY_NUMBERS[int(rest[0]) - 1]
        if len(rest) == 1:
            return screen(f"{number} {label}\n1. Call now\n2. Add to my contacts")
        if len(rest) > 2:
            return screen(INVALID)
        if rest[1] == "1":
            return end(dial_yourself(number))
        if rest[1] == "2":
            existing = find_contact_by_phone(owner, number)
            if existing:
                return screen(f"{number} is already in your contacts as {existing['name']}.")
            add_contact(owner, label, number)
            return screen(f"Saved {label} - {number}")
        return screen(INVALID)

    return screen(INVALID)


def contact_sms(rows, title):
    """Contacts as SMS texts of at most SMS_CHUNK characters each (no more than SMS_MAX_MESSAGES)."""
    lines = [f"{r['name']} {r['phone']}" for r in rows]
    messages, current, sent = [], f"{title}:", 0
    for i, line in enumerate(lines):
        if len(current) + 1 + len(line) > SMS_CHUNK:
            if len(messages) + 1 == SMS_MAX_MESSAGES:  # last message: say what didn't fit
                break
            messages.append(current)
            current = f"{title} (cont.):"
        current += "\n" + line
        sent = i + 1
    if sent < len(lines):
        more = f"\n+{len(lines) - sent} more in the Remote Phonebook app"
        while len(current) + len(more) > SMS_CHUNK and "\n" in current:
            current = current.rsplit("\n", 1)[0]
            sent -= 1
            more = f"\n+{len(lines) - sent} more in the Remote Phonebook app"
        current += more
    messages.append(current)
    return messages


def dial_yourself(number):
    # Ending the USSD session frees the phone to dial straight away
    return f"Dial {number} now from your phone. It is free."


PICK_HINT = "Reply no. or phone no."
QUICK_REPLIES = {"1": "Please call me."}
TYPE_OWN = "2"


def signature(owner):
    """Added to every SMS a user sends: it arrives from our sender ID, so say who it's from and how to reply."""
    user = get_user(owner)
    name = user["name"] if user else None
    return f"- {name} ({local_number(owner)})" if name else f"- {local_number(owner)}"


def pick_and_act(owner, caller, rows, selection, rest, first=1):
    """Resolve a contact number shown on the page or a typed phone number, then run the contact menu."""
    selection = selection.strip()
    index = position_of(selection, first, len(rows))
    if index is not None:
        contact = rows[index]
    else:
        phone = normalize_contact_phone(selection)
        if not phone:
            return screen("Enter a number from the list or a phone number.")
        contact = find_contact_by_phone(owner, phone) or {"name": phone, "phone": phone}
    return contact_menu(owner, caller, contact, rest)


def calling_available():
    return voice is not None and bool(AT_VOICE_NUMBER)


def contact_menu(owner, caller, contact, rest):
    name, phone = contact["name"], contact["phone"]
    if is_service_number(phone):
        # 999, 1199...: never connected through Africa's Talking, and they can't receive SMS
        if len(rest) == 0:
            title = name if name == phone else f"{name}\n{phone}"
            return screen(f"{title}\n1. Call\n3. Copy number")
        if rest == ["1"]:
            return end(dial_yourself(phone))
        if rest[0] != "3" or len(rest) > 1:
            return screen(INVALID)
    if len(rest) == 0:
        title = name if name == phone else f"{name}\n{phone}"
        return screen(f"{title}\n1. Call\n2. Send SMS\n3. Copy number")

    action = rest[0]
    if action == "1" and not calling_available():
        # No voice number set up yet: say so, and offer the options that do work. What they
        # pick next is read as a choice on this contact's menu.
        if len(rest) == 1:
            return screen("Calling isn't available yet.\n2. Send SMS\n3. Copy number")
        return contact_menu(owner, caller, contact, rest[1:])
    if len(rest) > (3 if action == "2" else 1):
        return screen(INVALID)

    # Call: Africa's Talking rings the caller, then /voice bridges them to the contact
    if action == "1":
        save_pending_call(caller, phone)
        start_call_async(caller)
        return end(f"We are calling you now. Answer to be connected to {name}.")

    if action in ("2", "3") and sms is None:
        return screen("SMS service is not available right now.")

    # Send SMS: pick a quick reply (no typing, so the session doesn't time out), or type one
    if action == "2":
        if len(rest) == 1:
            replies = "\n".join(f"{n}. {text}" for n, text in QUICK_REPLIES.items())
            return screen(f"SMS {name}:\n{replies}\n{TYPE_OWN}. Type a message\nOr just type it now")
        choice = rest[1].strip()
        if choice == TYPE_OWN:
            if len(rest) == 2:
                return screen(f"Type your message to {name}:")
            message = rest[2].strip()
        elif len(rest) > 2:
            return screen(INVALID)
        else:
            message = QUICK_REPLIES.get(choice, choice)  # anything else is the message itself
        sign = signature(owner)
        message = message[: SMS_MAX - len(sign) - 1]  # message + signature fit in one SMS
        if not message:
            return screen("Message cannot be empty.")
        send_sms_async(phone, f"{message}\n{sign}")
        return screen(f"Message sent to {name}.")

    # Copy number: USSD can't reach the clipboard, so text it to the user to copy/call/save
    if action == "3":
        send_sms_async(caller, f"{name}: {phone}" if name != phone else phone)
        title = f"{name}\n{phone}" if name != phone else phone
        return screen(f"{title}\nAlso sent to you by SMS: hold it there to copy.")

    return screen(INVALID)
