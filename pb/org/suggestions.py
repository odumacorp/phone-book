"""Ready-made choices for the organisation workspace forms, by kind of organisation (edition).

Operators can always type their own; these just save typing and keep names consistent (members browse
contacts by group on USSD, so "Bursar's office" and "Bursars office" would show as two groups).
"""

GROUPS = {
    "schools": ["Administration", "Principal's office", "Bursar's office", "Teachers", "Form 1", "Form 2", "Form 3", "Form 4",
                "Grade 7", "Grade 8", "Grade 9", "Boarding", "Sick bay", "Guidance and counselling", "Transport",
                "Games and clubs", "Library", "Kitchen", "Security", "Parents' association", "Board of management"],
    "insurance": ["Customer care", "Claims", "Motor", "Medical", "Life and pensions", "Underwriting", "Renewals", "Assessors",
                  "Agents", "Branch offices", "Finance", "Complaints"],
    "legal": ["Partners", "Advocates", "Litigation", "Conveyancing", "Corporate and commercial", "Family law",
              "Employment law", "Court clerks", "Accounts", "Reception"],
    "sacco": ["Front office", "Member services", "Loans", "Savings and deposits", "Credit committee", "Board of directors",
              "Supervisory committee", "Finance", "Recovery and collections", "Field officers", "Branches"],
    "company": ["Management", "Human resources", "Finance and accounts", "Sales", "Marketing", "Customer service", "Operations",
                "IT support", "Procurement", "Logistics", "Security", "Reception", "Clients", "Suppliers"],
    "church": ["Clergy", "Church office", "Elders", "Youth ministry", "Women's fellowship", "Men's fellowship", "Choir and worship",
               "Sunday school", "Welfare", "Home fellowships"],
    "chama": ["Officials", "Members", "Treasury", "Welfare", "Investments"],
    "health": ["Reception and appointments", "Outpatient", "Wards", "Maternity", "Pharmacy", "Laboratory", "Radiology",
               "Specialists", "Billing and insurance", "Ambulance"],
    "estate": ["Committee", "Security", "Caretakers", "Water", "Electricity", "Garbage collection", "Plumbers", "Electricians",
               "Service providers"],
    "county": ["Governor's office", "Health", "Education", "Finance and revenue", "Lands and housing", "Water", "Roads and transport",
               "Agriculture", "Trade", "Public service", "Ward offices"],
}

# Groups of people inside an organisation (classes, streams, fellowships, member categories)
GROUP_SUGGESTIONS = {
    "schools": ["Form 1", "Form 2", "Form 3", "Form 4", "Grade 7", "Grade 8", "Grade 9", "Boarders", "Day scholars",
                "Prefects", "Parents", "Teaching staff", "Support staff"],
    "insurance": ["Motor clients", "Medical clients", "Life clients", "Corporate clients", "Agents"],
    "legal": ["Individual clients", "Corporate clients", "Litigation clients", "Conveyancing clients"],
    "sacco": ["Members", "Delegates", "Staff", "Borrowers", "New members"],
    "company": ["Managers", "Staff", "Field staff", "Interns", "Clients", "Suppliers"],
    "church": ["Youth", "Women", "Men", "Children", "Choir", "New members", "Home fellowship A", "Home fellowship B"],
    "chama": ["Members", "Officials"],
    "health": ["Doctors", "Nurses", "Patients", "Support staff"],
    "estate": ["Residents", "Landlords", "Tenants", "Committee"],
    "county": ["Staff", "Residents", "Ward A", "Ward B"],
}

TITLES = {
    "schools": ["Principal", "Deputy principal", "Bursar", "Secretary", "Class teacher", "Head of department", "Teacher",
                "Matron", "School nurse", "Counsellor", "Games teacher", "Librarian", "Driver", "Security guard"],
    "insurance": ["Customer care officer", "Claims officer", "Relationship manager", "Agent", "Assessor", "Underwriter",
                  "Branch manager"],
    "legal": ["Managing partner", "Partner", "Senior advocate", "Associate advocate", "Paralegal", "Court clerk",
              "Legal secretary", "Accountant", "Receptionist"],
    "sacco": ["CEO", "Chairperson", "Secretary", "Treasurer", "Loans officer", "Credit officer", "Member services officer",
              "Accountant", "Teller", "Field officer"],
    "company": ["CEO", "Managing director", "Manager", "Supervisor", "HR officer", "Accountant", "Sales executive",
                "Customer care officer", "IT support", "Receptionist", "Driver"],
    "church": ["Pastor", "Reverend", "Bishop", "Elder", "Deacon", "Youth leader", "Choir director", "Secretary", "Treasurer"],
    "chama": ["Chairperson", "Vice chairperson", "Secretary", "Treasurer", "Member"],
    "health": ["Doctor", "Clinical officer", "Nurse", "Pharmacist", "Lab technologist", "Radiographer", "Receptionist",
               "Billing officer"],
    "estate": ["Chairperson", "Secretary", "Treasurer", "Caretaker", "Security guard", "Plumber", "Electrician"],
    "county": ["Chief officer", "Director", "Officer", "Ward administrator", "Clerk", "Receptionist"],
}

# Emergency contact names: everyone's, then each kind's own
EMERGENCY_COMMON = ["Police", "Ambulance", "Fire brigade", "Kenya Red Cross", "GBV hotline", "Childline"]
EMERGENCY = {
    "schools": ["School nurse", "Matron on duty", "Security gate", "Principal (after hours)", "School driver", "Nearest hospital"],
    "insurance": ["Claims hotline", "Tow and rescue partner", "Ambulance partner", "Medical emergency line", "Assessor on call"],
    "legal": ["Advocate on call", "Bail and arrest hotline", "Office security"],
    "sacco": ["Fraud hotline", "Lost card or M-Pesa issue", "Branch security"],
    "company": ["Security desk", "First aider", "Fire marshal", "IT emergency", "Facilities"],
    "church": ["Pastor on call", "Welfare committee", "Church security"],
    "chama": ["Chairperson", "Welfare contact"],
    "health": ["Emergency department", "Ambulance dispatch", "Maternity ward", "Hospital security"],
    "estate": ["Estate security", "Gate", "Water emergency", "Electricity (Kenya Power)", "Caretaker"],
    "county": ["County emergency centre", "Disaster response", "County ambulance", "Fire station"],
}


def _merge(*lists):
    seen, out = set(), []
    for items in lists:
        for item in items:
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
    return out


def department_suggestions(edition, existing=()):
    have = {e.lower() for e in existing}
    return [d for d in GROUPS.get(edition, []) if d.lower() not in have and d not in GROUP_SUGGESTIONS.get(edition, [])]


def group_suggestions(edition, existing=()):
    have = {e.lower() for e in existing}
    return [g for g in GROUP_SUGGESTIONS.get(edition, []) if g.lower() not in have]


def group_options(edition, existing, defined=()):
    """For a contact's Group box: the organisation's own departments and groups first, then names already used on
    contacts, then the suggestions for this kind of organisation."""
    return _merge(defined, sorted({g for g in existing if g}, key=str.lower), GROUPS.get(edition, []),
                  GROUP_SUGGESTIONS.get(edition, []))


def title_options(edition, existing=()):
    used = sorted({t for t in existing if t}, key=str.lower)
    return used + [t for t in TITLES.get(edition, []) if t.lower() not in {u.lower() for u in used}]


def emergency_options(edition, existing=()):
    used = {n.lower() for n in existing if n}
    return [n for n in EMERGENCY.get(edition, []) + EMERGENCY_COMMON if n.lower() not in used]
