"""A member's own contact list can ignore the defaults and show only contacts picked for them.

list_mode: NULL / "defaults" (default lists by role, department and group, plus changes for them) or "selected";
list_selection: JSON list of organisation contact ids, used when list_mode is "selected".
"""


def up(db, m):
    m.add_column("memberships", "list_mode", "TEXT")
    m.add_column("memberships", "list_selection", "TEXT")
