"""Modules: the shared building blocks that editions bundle. Each is built once and switched on per tenant.

`core` modules come with every enterprise plan. Edition modules come with plans whose edition_modules feature is
"included", and are otherwise sold as add-ons (the addons table links an add-on to a module_key).
"""

MODULES = {
    # key: (name, core?, description)
    "members": ("Members and departments", True, "Members, departments, classes, streams and groups"),
    "directory": ("Contacts directory", True, "Organisation contacts with role-based visibility and onboarding sync"),
    "announcements": ("Announcements", True, "Announcements to everyone or to groups; emergency broadcasts"),
    "emergency": ("Emergency contacts", True, "Emergency contacts for the organisation and per member"),
    "school_results": ("Result slips", False, "Student result slips on USSD (PIN) with full slips in the app"),
    "assignments": ("Assignments", False, "Assignments shown in full on USSD or as a link"),
    "guardians": ("Guardians", False, "Guardians linked to students"),
    "sacco_finance": ("SACCO finance", False, "Organisation financial details and members' own balances"),
    "matters": ("Matters and advocates", False, "Clients see the advocates on their matters"),
    "client_contacts": ("Client contacts", False, "Client contacts visible to assigned roles"),
    "custom_ussd": ("Custom USSD services", False, "Operator-built USSD menu items (Pro)"),
}


def is_core(module_key):
    return MODULES[module_key][1]
