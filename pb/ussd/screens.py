from pb.config import BACK, HOME, LOGOUT, NAV, USSD_MAX_LEN
from pb.text import con


MAIN_MENU = (
    "Remote Phone-Book\n"
    "1. My contacts\n"
    "2. Emergency numbers\n"
    "3. Add contact\n"
    "4. Search contact\n"
    "5. Delete contact\n"
    "6. SMS me my contacts\n"
    "Or type a name to search\n"
    "000. Log out"
)
# For people in an organisation, My organisations is option 3 (the rest move down one; typing a name still searches)
MEMBER_MENU = (
    "Remote Phone-Book\n"
    "1. My contacts\n"
    "2. Emergency numbers\n"
    "3. My organisations\n"
    "4. Add contact\n"
    "5. Search contact\n"
    "6. Delete contact\n"
    "7. SMS me my contacts\n"
    "000. Log out"
)
# Main menu numbers, in the order shown above (7 still opens My organisations for everyone else: "not in any yet")
MENU_ACTIONS = {"1": "list", "2": "emergency", "3": "add", "4": "search", "5": "delete", "6": "sms", "7": "orgs"}
MEMBER_ACTIONS = {"1": "list", "2": "emergency", "3": "orgs", "4": "add", "5": "search", "6": "delete", "7": "sms"}


def screen(msg):
    """A screen inside the menu: always offers Back/Home, so a result or mistake doesn't end the session."""
    room = USSD_MAX_LEN - 4 - len(NAV) - 1
    return con(msg[:room] + "\n" + NAV)


def resolve_nav(inputs):
    """Apply 0 (back one step) and 00 (home) to the inputs typed after logging in.

    Returns None if 000 (log out) was typed.
    """
    path = []
    for value in inputs:
        if value == LOGOUT:
            return None
        if value == HOME:
            path = []
        elif value == BACK:
            path = path[:-1]
        else:
            path.append(value)
    return path


INVALID = "Invalid choice."
NO_PRIORITY = "No priority contacts yet. Choose up to 5 in the Remote Phonebook app."
