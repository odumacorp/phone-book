"""Realistic test organisations and people for scripts/seed_test_accounts.py (all fictional).

Account numbers are made up from the org and person number: 0700 2OO PPP (people) and 0700 3OO NNN (organisation
contacts), so they never clash. Audiences name roles ("client"), departments or groups by name.
"""

# (name, role, job title, [departments / groups they're in])
SCHOOL_KILIMANI = {
    "name": "Kilimani Hills Academy", "edition": "schools", "plan": "enterprise_standard", "code": "2101",
    "departments": [("Administration", None), ("Sciences", None), ("Humanities", None), ("Boarding", None),
                    ("Form 1", None), ("Form 2", None), ("Form 3", None), ("Form 4", None)],
    "groups": [("Form 2 East", "class", "Form 2"), ("Form 4 West", "class", "Form 4"), ("Parents' Association", "custom", None),
               ("Drama Club", "team", None)],
    "people": [
        ("Dr. Grace Wanjiku Mwangi", "org_owner", "Principal", ["Administration"]),
        ("Samuel Otieno Ochieng", "org_admin", "Deputy Principal", ["Administration"]),
        ("Fatuma Hassan Ali", "content_publisher", "Bursar", ["Administration"]),
        ("Peter Kamau Njoroge", "department_supervisor", "Head of Sciences", ["Sciences"]),
        ("Aisha Mohammed", "teacher", "Mathematics Teacher", ["Sciences", "Form 2", "Form 2 East"]),
        ("David Thompson", "teacher", "English Teacher", ["Humanities", "Form 4", "Form 4 West", "Drama Club"]),
        ("Priya Sharma", "teacher", "Chemistry Teacher", ["Sciences", "Form 3"]),
        ("Josephine Chebet", "teacher", "Boarding Mistress", ["Boarding"]),
        ("Mary Achieng Odhiambo", "guardian", "Parent (Form 2 East)", ["Form 2 East", "Parents' Association"]),
        ("Michael O'Brien", "guardian", "Parent (Form 4 West)", ["Form 4 West", "Parents' Association"]),
        ("Halima Abdullahi", "guardian", "PA Chairperson", ["Parents' Association"]),
        ("Brian Kiprotich", "member", "Head Boy", ["Form 4", "Form 4 West"]),
        ("Joseph Mutua", "member", "Driver", ["Administration"]),
        ("Eng. Daniel Muriuki", "auditor", "Board of Management", []),
    ],
    "contacts": [
        ("School Office", "Reception", "Administration", True, []),
        ("Bursar's Office", "Fees and payments", "Administration", True, []),
        ("School Nurse - Sister Rose Wambui", "Sick bay", "Boarding", True, []),
        ("Boarding Office", "Boarders' welfare", "Boarding", True, ["guardian"]),
        ("Form 2 East Class Teacher", "Aisha Mohammed", "Form 2", False, []),
        ("Transport Desk", "School buses", "Administration", False, []),
        ("Kitchen Supervisor", "Catering", "Boarding", False, []),
        ("Exams Officer", "KCSE registration", "Administration", False, ["teacher"]),
    ],
    "emergency": [("Police", "999", "Police"), ("School Ambulance", "0700301911", "Ambulance"),
                  ("Childline Kenya", "116", "Child protection")],
    "notices": [
        ("Half-term break", "Half-term starts Thursday 12 March at 1pm. Students return Monday 16 March by 5pm. "
         "Day scholars' buses leave at 1:30pm.", [], True),
        ("Form 2 East: Mathematics contest", "Form 2 East students taking part in the county Mathematics contest meet "
         "Ms. Aisha Mohammed in Lab 2 on Friday at 4pm.", ["Form 2 East"], False),
        ("Fees reminder", "Term 1 fee balances are due by 31 January. Pay via the school M-Pesa paybill and keep the "
         "confirmation SMS.", ["guardian"], False),
        ("Staff meeting", "All teaching staff: meeting in the staffroom on Monday at 7:15am.", ["teacher", "org_admin"], False),
    ],
    "contact_groups": [
        ("Boarding team", ["Boarding Office", "School Nurse - Sister Rose Wambui", "Kitchen Supervisor"], ["Boarding", "guardian"]),
        ("Form 2 contacts", ["Form 2 East Class Teacher", "Exams Officer"], ["Form 2"]),
    ],
}

SCHOOL_LAKEVIEW = {
    "name": "Lakeview Girls' High School", "edition": "schools", "plan": "enterprise_standard", "code": "2102",
    "departments": [("Administration", None), ("Languages", None), ("Mathematics & Sciences", None), ("Guidance & Counselling", None)],
    "groups": [("Form 3 Blue", "class", None), ("Christian Union", "fellowship", None), ("Debate Club", "team", None)],
    "people": [
        ("Mrs. Rosemary Atieno Owino", "org_owner", "Principal", ["Administration"]),
        ("Catherine Nduta Gichuru", "org_admin", "Deputy Principal (Academics)", ["Administration"]),
        ("Sarah Jones", "teacher", "French Teacher", ["Languages", "Debate Club"]),
        ("Lucy Wairimu Kariuki", "department_supervisor", "Head of Guidance & Counselling", ["Guidance & Counselling"]),
        ("Agnes Moraa Nyaboke", "teacher", "Biology Teacher", ["Mathematics & Sciences", "Form 3 Blue"]),
        ("Pastor Isaac Omondi", "member", "School Chaplain", ["Christian Union"]),
        ("Margaret Wangari", "guardian", "Parent (Form 3 Blue)", ["Form 3 Blue"]),
        ("Ahmed Yusuf", "guardian", "Parent (Form 3 Blue)", ["Form 3 Blue"]),
    ],
    "contacts": [
        ("Principal's Office", "Principal", "Administration", True, []),
        ("Counselling Office", "Confidential support", "Guidance & Counselling", True, []),
        ("Matron - Mrs. Akinyi", "Dormitories", "Administration", True, ["guardian"]),
        ("Games Master", "Sports and trips", "Administration", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("GBV Hotline", "1195", "Gender-based violence")],
    "notices": [
        ("Visiting day", "Visiting day is Saturday 7 February, 10am to 3pm. Parents please carry your ID.", ["guardian"], True),
        ("Debate finals", "The Debate Club hosts the inter-school finals in the main hall on Friday at 2pm.", [], False),
    ],
    "contact_groups": [("Student welfare", ["Counselling Office", "Matron - Mrs. Akinyi"], ["guardian", "Guidance & Counselling"])],
}

SACCO_UFANISI = {
    "name": "Ufanisi Teachers SACCO", "edition": "sacco", "plan": "enterprise_standard", "code": "2103",
    "departments": [("Board", None), ("Loans", None), ("Savings & Deposits", None), ("Customer Service", None),
                    ("Nakuru Branch", None), ("Eldoret Branch", None)],
    "groups": [("Development Loan Borrowers", "segment", "Loans"), ("Youth Savers", "segment", None),
               ("Retirees", "segment", None)],
    "people": [
        ("James Kipchumba Rotich", "org_owner", "Chief Executive Officer", ["Board"]),
        ("Esther Wambui Kimani", "org_admin", "Operations Manager", ["Customer Service"]),
        ("Kennedy Omondi Were", "department_supervisor", "Credit Manager", ["Loans"]),
        ("Nancy Jeptoo", "content_publisher", "Communications Officer", ["Customer Service"]),
        ("Mary Achieng Odhiambo", "member", "Member (Teacher, Kisumu)", ["Development Loan Borrowers"]),
        ("Paul Mwangi Gitau", "member", "Member (Teacher, Nakuru)", ["Nakuru Branch"]),
        ("Zawadi Mwende Musyoka", "member", "Member (Teacher, Machakos)", ["Youth Savers"]),
        ("Thomas Keter", "member", "Member (Retired Head Teacher)", ["Retirees", "Eldoret Branch"]),
        ("Rehema Salim", "member", "Member (Teacher, Mombasa)", ["Youth Savers"]),
        ("CPA Jane Muthoni Ngugi", "auditor", "Supervisory Committee", ["Board"]),
    ],
    "contacts": [
        ("Customer Care", "Account enquiries", "Customer Service", True, []),
        ("Loans Desk", "Loan applications", "Loans", True, []),
        ("Nakuru Branch Office", "Branch", "Nakuru Branch", True, ["Nakuru Branch"]),
        ("Eldoret Branch Office", "Branch", "Eldoret Branch", True, ["Eldoret Branch"]),
        ("M-Pesa Deposits Help", "Paybill support", "Savings & Deposits", False, []),
        ("Guarantors Desk", "Loan guarantors", "Loans", False, []),
        ("Dividends Enquiries", "Annual dividends", "Savings & Deposits", False, []),
    ],
    "emergency": [("Fraud Hotline", "0700300999", "Report fraud"), ("Police", "999", "Police")],
    "notices": [
        ("Annual General Meeting", "The AGM is on Saturday 28 March at Nakuru ASK Showground, 9am. Bring your member number "
         "and ID. Dividends of 11% on share capital will be proposed.", [], True),
        ("New development loan rates", "Development loans now charge 1% a month on reducing balance, up to 4x your "
         "deposits. Talk to the Loans Desk.", ["Development Loan Borrowers"], False),
        ("Beware of fraudsters", "Ufanisi staff will never ask for your PIN. Report suspicious calls to the Fraud Hotline.", [], False),
    ],
    "contact_groups": [("Loans team", ["Loans Desk", "Guarantors Desk"], ["Development Loan Borrowers", "Loans"])],
}

SACCO_JUMUIKA = {
    "name": "Jumuika Boda Boda SACCO", "edition": "sacco", "plan": "enterprise_standard", "code": "2104",
    "departments": [("Office", None), ("Kondele Stage", None), ("Mamboleo Stage", None)],
    "groups": [("Motorbike Loan", "segment", None), ("Insurance Cover", "segment", None)],
    "people": [
        ("Fredrick Onyango Ouma", "org_owner", "Chairman", ["Office"]),
        ("Beatrice Adhiambo", "org_admin", "Secretary", ["Office"]),
        ("Collins Otieno", "department_supervisor", "Stage Chairman, Kondele", ["Kondele Stage"]),
        ("Kevin Odhiambo Oloo", "member", "Rider", ["Kondele Stage", "Motorbike Loan"]),
        ("Moses Wekesa", "member", "Rider", ["Mamboleo Stage", "Insurance Cover"]),
        ("Dennis Kiprono", "member", "Rider", ["Mamboleo Stage", "Motorbike Loan"]),
    ],
    "contacts": [
        ("SACCO Office", "Daily contributions", "Office", True, []),
        ("Kondele Stage Chairman", "Collins Otieno", "Kondele Stage", True, ["Kondele Stage"]),
        ("Mamboleo Stage Chairman", "Stage leader", "Mamboleo Stage", True, ["Mamboleo Stage"]),
        ("Insurance Agent", "Third-party cover", "Office", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Kenya Red Cross", "1199", "Ambulance and first aid")],
    "notices": [("Crackdown on riders without helmets", "County officers are checking helmets and reflectors this week. "
                 "Ride safe.", [], True)],
    "contact_groups": [("Stage leaders", ["Kondele Stage Chairman", "Mamboleo Stage Chairman"], [])],
}

COMPANY_SAVANNAH = {
    "name": "Savannah Logistics Ltd", "edition": "company", "plan": "enterprise_pro", "code": "2105",
    "departments": [("Management", None), ("Operations", None), ("Mombasa Port Office", "Operations"),
                    ("Nairobi ICD", "Operations"), ("Finance", None), ("Sales", None), ("Human Resources", None), ("IT", None)],
    "groups": [("Drivers", "team", "Operations"), ("Key Accounts", "segment", "Sales"), ("Night Shift", "team", "Operations")],
    "people": [
        ("Abdul Karim Omar", "org_owner", "Managing Director", ["Management"]),
        ("Linda Njeri Kamau", "org_admin", "HR Manager", ["Human Resources"]),
        ("Rajesh Patel", "department_supervisor", "Operations Manager", ["Operations"]),
        ("Hannah Schmidt", "content_publisher", "Communications Lead", ["Management"]),
        ("Mwanajuma Bakari", "member", "Port Clearing Agent", ["Mombasa Port Office"]),
        ("George Ndirangu", "member", "Truck Driver", ["Drivers", "Night Shift"]),
        ("Hussein Abdi", "member", "Truck Driver", ["Drivers"]),
        ("Winnie Chepkoech", "member", "Accountant", ["Finance"]),
        ("Tom Baker", "member", "Head of Sales", ["Sales", "Key Accounts"]),
        ("Diana Mueni", "member", "IT Support", ["IT"]),
        ("Mercy Wanjala", "client", "Procurement, Mlima Wholesalers", ["Key Accounts"]),
        ("Oliver Grant", "client", "Logistics Manager, Rift Valley Bottlers", ["Key Accounts"]),
        ("CPA Samuel Kirui", "auditor", "External Auditor", []),
    ],
    "contacts": [
        ("Dispatch Desk", "24h dispatch", "Operations", True, []),
        ("Mombasa Port Office", "Clearing and forwarding", "Operations", True, ["Mombasa Port Office", "client"]),
        ("Nairobi ICD Office", "Inland container depot", "Operations", True, ["Nairobi ICD"]),
        ("Fleet Workshop", "Truck repairs", "Operations", True, ["Drivers"]),
        ("Payroll", "Salaries and payslips", "Finance", True, ["member"]),
        ("HR Office", "Leave and staff welfare", "Human Resources", True, ["member", "org_admin"]),
        ("IT Helpdesk", "Laptops, email, VPN", "IT", True, ["member"]),
        ("Customer Service", "Track your cargo", "Sales", True, ["client"]),
        ("Fuel Card Support", "Fuel cards", "Finance", False, []),
        ("Security Office", "Yard security", "Operations", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Breakdown Rescue", "0700305911", "Road rescue"),
                  ("Kenya Red Cross", "1199", "Ambulance")],
    "notices": [
        ("Port congestion update", "Expect 2-3 day delays at Mombasa Port this week. Customer Service will update each "
         "consignee.", ["client", "Operations"], True),
        ("Night shift roster", "The new night shift roster starts Monday. Check with the Dispatch Desk.", ["Night Shift"], False),
        ("Payslips", "December payslips are on the HR portal. Contact Payroll for questions.", ["member"], False),
    ],
    "contact_groups": [
        ("Drivers' essentials", ["Dispatch Desk", "Fleet Workshop", "Fuel Card Support", "Security Office"], ["Drivers"]),
        ("Client care", ["Customer Service", "Mombasa Port Office"], ["client"]),
    ],
}

COMPANY_NYOTA = {
    "name": "Nyota Digital Solutions", "edition": "company", "plan": "enterprise_standard", "code": "2106",
    "departments": [("Leadership", None), ("Engineering", None), ("Product & Design", None), ("Customer Success", None),
                    ("People & Culture", None)],
    "groups": [("Mobile Squad", "team", "Engineering"), ("Payments Squad", "team", "Engineering"), ("Enterprise Clients", "segment", None)],
    "people": [
        ("Amara Okafor", "org_owner", "CEO & Co-founder", ["Leadership"]),
        ("Lars Andersen", "org_admin", "CTO & Co-founder", ["Leadership", "Engineering"]),
        ("Chen Wei", "department_supervisor", "Engineering Manager", ["Engineering"]),
        ("Sophie Martin", "content_publisher", "Head of Product", ["Product & Design"]),
        ("Brian Kiplagat", "member", "Senior Android Engineer", ["Mobile Squad"]),
        ("Faith Wanjiru Maina", "member", "Backend Engineer", ["Payments Squad"]),
        ("Yusuf Ibrahim", "member", "UX Designer", ["Product & Design"]),
        ("Grace Akinyi Opiyo", "member", "Customer Success Lead", ["Customer Success"]),
        ("Carlos Mendes", "client", "IT Director, Kijani Microfinance", ["Enterprise Clients"]),
    ],
    "contacts": [
        ("Office Manager", "Westlands office", "Leadership", True, []),
        ("On-call Engineer", "Production incidents", "Engineering", True, ["Engineering", "client"]),
        ("Customer Success", "Support for clients", "Customer Success", True, ["client"]),
        ("People & Culture", "Benefits and leave", "People & Culture", True, ["member"]),
    ],
    "emergency": [("Police", "999", "Police"), ("Building Security", "0700306900", "Westlands office security")],
    "notices": [("Hackathon", "Our M-Pesa integration hackathon is on Friday from 9am. Lunch provided.", ["member"], True)],
    "contact_groups": [("Incident response", ["On-call Engineer", "Customer Success"], ["Enterprise Clients"])],
}

LAW_WANJIRU = {
    "name": "Wanjiru, Okafor & Patel Advocates", "edition": "legal", "plan": "enterprise_standard", "code": "2107",
    "departments": [("Partners", None), ("Litigation", None), ("Conveyancing", None), ("Corporate & Commercial", None),
                    ("Family Law", None), ("Accounts", None)],
    "groups": [("Pupils", "team", None), ("Land Clients", "segment", "Conveyancing"), ("Corporate Clients", "segment", None)],
    "people": [
        ("Wanjiru Kamau Ndegwa", "org_owner", "Managing Partner", ["Partners"]),
        ("Chinedu Okafor", "org_admin", "Senior Partner", ["Partners", "Corporate & Commercial"]),
        ("Anjali Patel", "department_supervisor", "Partner, Conveyancing", ["Conveyancing"]),
        ("Victor Mutiso", "content_publisher", "Associate, Litigation", ["Litigation"]),
        ("Elizabeth Moraa", "member", "Associate, Family Law", ["Family Law"]),
        ("Kelvin Barasa", "member", "Pupil", ["Pupils", "Litigation"]),
        ("Ruth Nyambura", "member", "Legal Secretary", ["Partners"]),
        ("Simon Kariuki", "member", "Court Clerk", ["Litigation"]),
        ("Pauline Wafula", "member", "Accountant", ["Accounts"]),
        ("John Mwangi Gachanja", "client", "Client (Land purchase, Kiambu)", ["Land Clients"]),
        ("Emily Clarke", "client", "General Counsel, Acacia Renewables", ["Corporate Clients"]),
        ("Mohamed Farah", "client", "Client (Succession matter)", []),
        ("CPA Lilian Achieng", "auditor", "Auditor", []),
    ],
    "contacts": [
        ("Reception", "Appointments", "Partners", True, []),
        ("Litigation Desk", "Court dates and filings", "Litigation", True, ["client", "Litigation"]),
        ("Conveyancing Desk", "Title searches and transfers", "Conveyancing", True, ["Land Clients", "Conveyancing"]),
        ("Accounts - Fee Notes", "Invoices and payments", "Accounts", True, ["client"]),
        ("Court Clerk - Milimani", "Court runner", "Litigation", False, []),
        ("Lands Registry Liaison", "Ardhi House", "Conveyancing", False, []),
        ("Family Law Desk", "Divorce, custody, succession", "Family Law", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("LSK Legal Aid", "0700307111", "Legal aid hotline")],
    "notices": [
        ("Court vacation", "Courts close for the vacation from 21 December to 13 January. Urgent applications are still "
         "heard.", [], True),
        ("Title deeds ready", "Clients whose transfers were lodged in November: your title deeds are ready for collection "
         "at Reception. Bring your ID.", ["Land Clients"], False),
        ("Pupils' rota", "Pupils: Milimani court runs this week are Monday, Wednesday and Friday.", ["Pupils"], False),
    ],
    "contact_groups": [("Litigation team", ["Litigation Desk", "Court Clerk - Milimani"], ["Litigation", "Pupils"]),
                       ("Land matters", ["Conveyancing Desk", "Lands Registry Liaison"], ["Land Clients"])],
}

LAW_KIBET = {
    "name": "Kibet & Fernandes LLP", "edition": "legal", "plan": "enterprise_standard", "code": "2108",
    "departments": [("Partners", None), ("Employment Law", None), ("Shipping & Maritime", None)],
    "groups": [("Employer Clients", "segment", None)],
    "people": [
        ("Ezekiel Kibet Langat", "org_owner", "Managing Partner", ["Partners"]),
        ("Isabel Fernandes", "org_admin", "Partner, Shipping & Maritime", ["Partners", "Shipping & Maritime"]),
        ("Nuru Hamisi", "member", "Associate, Employment Law", ["Employment Law"]),
        ("Robert Wilson", "client", "HR Director, Coast Cement Ltd", ["Employer Clients"]),
        ("Salma Abdalla", "client", "Client (Unfair dismissal claim)", []),
    ],
    "contacts": [
        ("Mombasa Office", "Nyali Centre, 3rd floor", "Partners", True, []),
        ("Employment Desk", "Workplace disputes", "Employment Law", True, ["client"]),
        ("Maritime Desk", "Cargo claims, ship arrests", "Shipping & Maritime", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Kenya Coast Guard", "0700308112", "Sea emergencies")],
    "notices": [("New office hours", "From 1 February we're open Saturday 9am to 1pm.", [], True)],
    "contact_groups": [("Employer support", ["Employment Desk", "Mombasa Office"], ["Employer Clients"])],
}


SCHOOL_MOMBASA = {
    "name": "Mombasa Coast Academy", "edition": "schools", "plan": "enterprise_standard", "code": "2109",
    "departments": [("Administration", None), ("Sciences", None), ("Languages", None), ("Sports", None), ("Form 4", None)],
    "groups": [("Form 4 North", "class", "Form 4"), ("Swimming Team", "team", "Sports"), ("Muslim Students Association", "fellowship", None)],
    "people": [
        ("Mr. Salim Athman Bakari", "org_owner", "Principal", ["Administration"]),
        ("Zainab Omar Mwinyi", "org_admin", "Deputy Principal", ["Administration"]),
        ("Rashid Juma", "department_supervisor", "Head of Sports", ["Sports"]),
        ("Emma Collins", "teacher", "English & Literature Teacher", ["Languages", "Form 4", "Form 4 North"]),
        ("Kahindi Charo", "teacher", "Physics Teacher", ["Sciences"]),
        ("Neema Mwakio", "content_publisher", "School Secretary", ["Administration"]),
        ("Ali Swaleh", "guardian", "Parent (Form 4 North)", ["Form 4 North"]),
        ("Rukia Abdalla", "guardian", "Parent (Swimming Team)", ["Swimming Team"]),
    ],
    "contacts": [
        ("School Office", "Reception", "Administration", True, []),
        ("Games Office", "Fixtures and trips", "Sports", True, ["Swimming Team", "Sports"]),
        ("Bursar", "Fees", "Administration", True, ["guardian"]),
        ("Lab Technician", "Science labs", "Sciences", False, []),
        ("School Clinic", "First aid", "Administration", True, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Kenya Red Cross", "1199", "Ambulance")],
    "notices": [
        ("Swimming gala", "The inter-schools swimming gala is on Saturday at Nyali pool from 8am.", ["Swimming Team", "guardian"], True),
        ("Form 4 mock exams", "Form 4 mock exams start on Monday 2 March. Timetables are on the notice board.", ["Form 4"], False),
    ],
    "contact_groups": [("Sports contacts", ["Games Office", "School Clinic"], ["Sports", "Swimming Team"])],
}

SCHOOL_SUNRISE = {
    "name": "Sunrise Junior School", "edition": "schools", "plan": "enterprise_standard", "code": "2110",
    "departments": [("Administration", None), ("Lower Primary", None), ("Upper Primary", None), ("Junior Secondary", None),
                    ("Transport", None)],
    "groups": [("Grade 4 Red", "class", "Upper Primary"), ("Grade 7 Blue", "class", "Junior Secondary"), ("Bus Route A", "custom", "Transport")],
    "people": [
        ("Mrs. Janet Jerono Kiptoo", "org_owner", "Head Teacher", ["Administration"]),
        ("Philip Kirwa", "org_admin", "Deputy Head Teacher", ["Administration"]),
        ("Gladys Cherono", "department_supervisor", "Head of Junior Secondary", ["Junior Secondary"]),
        ("Hellen Busienei", "teacher", "Grade 4 Class Teacher", ["Upper Primary", "Grade 4 Red"]),
        ("Mark Robinson", "teacher", "Grade 7 Science Teacher", ["Junior Secondary", "Grade 7 Blue"]),
        ("Elijah Sang", "member", "School Bus Driver", ["Transport", "Bus Route A"]),
        ("Caroline Chepkemoi", "guardian", "Parent (Grade 4 Red, Bus Route A)", ["Grade 4 Red", "Bus Route A"]),
        ("Vincent Kiprop", "guardian", "Parent (Grade 7 Blue)", ["Grade 7 Blue"]),
    ],
    "contacts": [
        ("School Office", "Reception", "Administration", True, []),
        ("Transport Coordinator", "Bus routes and times", "Transport", True, ["Bus Route A", "guardian"]),
        ("Grade 4 Red Class Teacher", "Hellen Busienei", "Upper Primary", True, ["Grade 4 Red"]),
        ("Grade 7 Blue Class Teacher", "Mark Robinson", "Junior Secondary", True, ["Grade 7 Blue"]),
        ("School Nurse", "Sick bay", "Administration", True, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Childline Kenya", "116", "Child protection")],
    "notices": [
        ("Bus Route A delay", "Bus Route A will pick up 20 minutes late tomorrow because of road works at Kapsoya.", ["Bus Route A"], True),
        ("CBC projects", "Grade 7 learners: bring your science project materials on Wednesday.", ["Grade 7 Blue"], False),
    ],
    "contact_groups": [("Parents' contacts", ["School Office", "Transport Coordinator", "School Nurse"], ["guardian"])],
}

SCHOOL_RIFT = {
    "name": "Rift Valley International School", "edition": "schools", "plan": "enterprise_pro", "code": "2111",
    "departments": [("Leadership", None), ("Primary Years", None), ("Middle Years", None), ("Diploma Programme", None),
                    ("Admissions", None), ("Boarding", None)],
    "groups": [("Year 9 Kestrels", "class", "Middle Years"), ("DP1 Cohort", "class", "Diploma Programme"),
               ("Model UN", "team", None)],
    "people": [
        ("Dr. Helen Carter", "org_owner", "Head of School", ["Leadership"]),
        ("Joseph Nderitu Mwaura", "org_admin", "Deputy Head", ["Leadership"]),
        ("Ingrid Johansson", "department_supervisor", "Diploma Programme Coordinator", ["Diploma Programme"]),
        ("Kwame Mensah", "teacher", "History Teacher", ["Middle Years", "Year 9 Kestrels", "Model UN"]),
        ("Anita Desai", "teacher", "Mathematics Teacher", ["Diploma Programme", "DP1 Cohort"]),
        ("Wanjiku Gathoni", "content_publisher", "Admissions Officer", ["Admissions"]),
        ("Pierre Dubois", "guardian", "Parent (Year 9 Kestrels)", ["Year 9 Kestrels"]),
        ("Njambi Karanja", "guardian", "Parent (DP1 Cohort)", ["DP1 Cohort"]),
        ("Robert Mwangi Kariuki", "auditor", "Board of Governors", []),
    ],
    "contacts": [
        ("Front Office", "Visitors and general enquiries", "Leadership", True, []),
        ("Admissions Office", "New applications", "Admissions", True, []),
        ("Boarding House Parent", "Boarders' welfare", "Boarding", True, ["guardian", "Boarding"]),
        ("DP Coordinator's Office", "IB Diploma", "Diploma Programme", True, ["DP1 Cohort"]),
        ("IT Helpdesk", "Student laptops", "Leadership", False, []),
        ("Health Centre", "School nurse and counsellor", "Boarding", True, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Kenya Red Cross", "1199", "Ambulance")],
    "notices": [
        ("Model UN conference", "Our Model UN delegates travel to Nairobi on Thursday 6am. Consent forms due Tuesday.", ["Model UN", "guardian"], True),
        ("DP1 extended essays", "DP1 students: extended essay topics are due to your supervisor by Friday.", ["DP1 Cohort"], False),
        ("Open day", "Prospective families are welcome on Saturday 14 March from 10am.", [], False),
    ],
    "contact_groups": [("Boarding essentials", ["Boarding House Parent", "Health Centre"], ["Boarding", "guardian"])],
}

SACCO_MKULIMA = {
    "name": "Mkulima Dairy Farmers SACCO", "edition": "sacco", "plan": "enterprise_standard", "code": "2112",
    "departments": [("Management", None), ("Milk Collection", None), ("Loans & Inputs", None), ("Othaya Centre", "Milk Collection"),
                    ("Mukurweini Centre", "Milk Collection")],
    "groups": [("Zero-grazing Farmers", "segment", None), ("Women Farmers", "segment", None)],
    "people": [
        ("Joseph Maina Wachira", "org_owner", "General Manager", ["Management"]),
        ("Lucy Wanjiru Mathenge", "org_admin", "Office Administrator", ["Management"]),
        ("Stephen Gichuki", "department_supervisor", "Milk Collection Supervisor", ["Milk Collection"]),
        ("Naomi Wangui", "content_publisher", "Extension Officer", ["Loans & Inputs"]),
        ("Daniel Kimani Waweru", "member", "Farmer (6 cows)", ["Othaya Centre", "Zero-grazing Farmers"]),
        ("Mary Nyokabi", "member", "Farmer", ["Mukurweini Centre", "Women Farmers"]),
        ("Peter Muriithi", "member", "Farmer", ["Othaya Centre"]),
    ],
    "contacts": [
        ("SACCO Office", "Payments and statements", "Management", True, []),
        ("Othaya Collection Centre", "Morning and evening collection", "Milk Collection", True, ["Othaya Centre"]),
        ("Mukurweini Collection Centre", "Morning and evening collection", "Milk Collection", True, ["Mukurweini Centre"]),
        ("AI & Vet Services", "Artificial insemination, vet", "Loans & Inputs", True, []),
        ("Feeds & Inputs Store", "Dairy meal on credit", "Loans & Inputs", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("County Vet Emergency", "0700312911", "Animal emergencies")],
    "notices": [
        ("Milk price review", "From 1 March we pay KES 52 per litre. Payments go out on the 10th of each month.", [], True),
        ("Free fodder training", "Women farmers: free silage training at Othaya Centre on Saturday 9am.", ["Women Farmers"], False),
    ],
    "contact_groups": [("Farm services", ["AI & Vet Services", "Feeds & Inputs Store"], [])],
}

SACCO_AFYA = {
    "name": "Afya Health Workers SACCO", "edition": "sacco", "plan": "enterprise_standard", "code": "2113",
    "departments": [("Board", None), ("Credit", None), ("Member Services", None), ("Investments", None)],
    "groups": [("Nurses", "segment", None), ("Clinical Officers", "segment", None), ("Housing Scheme", "segment", "Investments")],
    "people": [
        ("Dr. Abdirahman Hussein", "org_owner", "Chairperson", ["Board"]),
        ("Pamela Akoth Ouma", "org_admin", "Chief Executive Officer", ["Member Services"]),
        ("Francis Mutinda", "department_supervisor", "Credit Manager", ["Credit"]),
        ("Irene Wanjala", "content_publisher", "Marketing Officer", ["Member Services"]),
        ("Wairimu Njoroge", "member", "Nurse, Kenyatta National Hospital", ["Nurses", "Housing Scheme"]),
        ("Ben Otieno Okoth", "member", "Clinical Officer", ["Clinical Officers"]),
        ("Sister Agnes Mbithe", "member", "Midwife", ["Nurses"]),
        ("CPA Kenneth Kiprop", "auditor", "Internal Auditor", ["Board"]),
    ],
    "contacts": [
        ("Member Services", "Statements and enquiries", "Member Services", True, []),
        ("Credit Office", "Emergency and school-fees loans", "Credit", True, []),
        ("Housing Scheme Desk", "Plots in Kitengela", "Investments", True, ["Housing Scheme"]),
        ("Check-off Desk", "Payroll deductions", "Member Services", False, []),
        ("Insurance Partner", "Medical and life cover", "Member Services", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Fraud Hotline", "0700313999", "Report fraud")],
    "notices": [
        ("Housing scheme site visit", "Housing Scheme members: site visit to Kitengela on Saturday. Bus leaves KNH at 8am.", ["Housing Scheme"], True),
        ("Emergency loans", "Emergency loans are now processed within 24 hours through the app.", [], False),
    ],
    "contact_groups": [("Loans and check-off", ["Credit Office", "Check-off Desk"], ["Nurses", "Clinical Officers"])],
}

SACCO_PWANI = {
    "name": "Pwani Fishermen SACCO", "edition": "sacco", "plan": "enterprise_standard", "code": "2114",
    "departments": [("Office", None), ("Kilifi Landing Site", None), ("Malindi Landing Site", None), ("Boats & Gear", None)],
    "groups": [("Boat Owners", "segment", "Boats & Gear"), ("Fish Traders", "segment", None)],
    "people": [
        ("Bakari Hamisi Mwamure", "org_owner", "Chairman", ["Office"]),
        ("Mwanaisha Said", "org_admin", "Secretary", ["Office"]),
        ("Kazungu Kenga", "department_supervisor", "Beach Leader, Kilifi", ["Kilifi Landing Site"]),
        ("Omar Shee", "member", "Fisherman, boat owner", ["Kilifi Landing Site", "Boat Owners"]),
        ("Sidi Mohamed", "member", "Fish trader", ["Malindi Landing Site", "Fish Traders"]),
        ("Fatma Said", "member", "Fish trader", ["Fish Traders"]),
        ("Giovanni Russo", "member", "Boat mechanic (Malindi)", ["Boats & Gear"]),
    ],
    "contacts": [
        ("SACCO Office", "Savings and loans", "Office", True, []),
        ("Kilifi Beach Leader", "Kilifi landing site", "Kilifi Landing Site", True, ["Kilifi Landing Site"]),
        ("Malindi Beach Leader", "Malindi landing site", "Malindi Landing Site", True, ["Malindi Landing Site"]),
        ("Cold Room Manager", "Ice and storage", "Office", True, ["Fish Traders"]),
        ("Outboard Engine Mechanic", "Repairs", "Boats & Gear", False, []),
    ],
    "emergency": [("Kenya Coast Guard", "0700314112", "Sea rescue"), ("Police", "999", "Police")],
    "notices": [
        ("Weather warning", "Strong winds expected from Tuesday. Small boats should not go beyond the reef.", [], True),
        ("Gear loans", "Boat owners can now apply for new nets and engines on a 12-month loan.", ["Boat Owners"], False),
    ],
    "contact_groups": [("At sea", ["Kilifi Beach Leader", "Malindi Beach Leader", "Outboard Engine Mechanic"], ["Boat Owners"])],
}

COMPANY_BARAKA = {
    "name": "Baraka Supermarkets Ltd", "edition": "company", "plan": "enterprise_pro", "code": "2115",
    "departments": [("Head Office", None), ("Nakuru Town Branch", None), ("Naivasha Branch", None), ("Procurement", "Head Office"),
                    ("Finance", "Head Office"), ("Security", None)],
    "groups": [("Cashiers", "team", None), ("Branch Managers", "team", None), ("Suppliers", "segment", None)],
    "people": [
        ("Hon. Mohamed Abdi Noor", "org_owner", "Chairman", ["Head Office"]),
        ("Ruth Wambui Ngugi", "org_admin", "General Manager", ["Head Office", "Branch Managers"]),
        ("Kiprotich Langat", "department_supervisor", "Branch Manager, Nakuru Town", ["Nakuru Town Branch", "Branch Managers"]),
        ("Susan Mwikali", "department_supervisor", "Branch Manager, Naivasha", ["Naivasha Branch", "Branch Managers"]),
        ("Ravi Mehta", "content_publisher", "Procurement Manager", ["Procurement"]),
        ("Grace Nekesa", "member", "Cashier", ["Nakuru Town Branch", "Cashiers"]),
        ("Edwin Owino", "member", "Security Supervisor", ["Security"]),
        ("Patricia Nduku", "client", "Supplier, Kamili Bakers", ["Suppliers"]),
        ("James Whitfield", "client", "Supplier, Highland Dairies", ["Suppliers"]),
    ],
    "contacts": [
        ("Head Office", "Administration", "Head Office", True, []),
        ("Nakuru Town Branch", "Store", "Nakuru Town Branch", True, ["Nakuru Town Branch"]),
        ("Naivasha Branch", "Store", "Naivasha Branch", True, ["Naivasha Branch"]),
        ("Procurement Office", "Orders and deliveries", "Procurement", True, ["client", "Branch Managers"]),
        ("Accounts Payable", "Supplier payments", "Finance", True, ["client"]),
        ("Security Control Room", "CCTV and guards", "Security", True, ["member"]),
    ],
    "emergency": [("Police", "999", "Police"), ("Fire Brigade", "0700315222", "Fire")],
    "notices": [
        ("Stock-take", "Annual stock-take is on Sunday. All branch staff report at 6am.", ["member", "Branch Managers"], True),
        ("Supplier payments", "Supplier invoices received by the 20th are paid on the 30th.", ["client"], False),
    ],
    "contact_groups": [("Supplier desk", ["Procurement Office", "Accounts Payable"], ["Suppliers"])],
}

COMPANY_TAUSI = {
    "name": "Tausi Hotels & Resorts", "edition": "company", "plan": "enterprise_standard", "code": "2116",
    "departments": [("Management", None), ("Front Office", None), ("Housekeeping", None), ("Kitchen", None), ("Maintenance", None)],
    "groups": [("Night Team", "team", None), ("Tour Operators", "segment", None)],
    "people": [
        ("Fernando Alvarez", "org_owner", "Managing Director", ["Management"]),
        ("Amina Mohamed Kassim", "org_admin", "General Manager", ["Management"]),
        ("Chef Juma Mwinyi", "department_supervisor", "Executive Chef", ["Kitchen"]),
        ("Lilian Kadzo", "content_publisher", "Guest Relations Manager", ["Front Office"]),
        ("Saidi Mwakidudu", "member", "Night Auditor", ["Front Office", "Night Team"]),
        ("Esther Kalama", "member", "Housekeeper", ["Housekeeping"]),
        ("Hans Becker", "client", "Tour Operator, Safari Sonnenschein", ["Tour Operators"]),
    ],
    "contacts": [
        ("Reception", "24h front desk", "Front Office", True, []),
        ("Reservations", "Bookings", "Front Office", True, ["client"]),
        ("Housekeeping Desk", "Rooms", "Housekeeping", True, ["member"]),
        ("Maintenance Duty", "Repairs, power, water", "Maintenance", True, ["member"]),
        ("Kitchen", "Room service", "Kitchen", False, []),
    ],
    "emergency": [("Police", "999", "Police"), ("Diani Beach Rescue", "0700316112", "Beach and sea rescue")],
    "notices": [
        ("Peak season", "Peak season starts 15 December: all leave is frozen until 5 January.", ["member", "department_supervisor"], True),
        ("Group bookings", "Tour operators: group rates for Easter are now available from Reservations.", ["Tour Operators"], False),
    ],
    "contact_groups": [("Night duty", ["Reception", "Maintenance Duty"], ["Night Team"])],
}

COMPANY_GREENFIELD = {
    "name": "Greenfield Agro Exports", "edition": "company", "plan": "enterprise_standard", "code": "2117",
    "departments": [("Management", None), ("Farm Operations", None), ("Pack House", None), ("Quality Assurance", None),
                    ("Export Logistics", None)],
    "groups": [("Outgrowers", "segment", None), ("Overseas Buyers", "segment", None), ("Pickers", "team", "Farm Operations")],
    "people": [
        ("Sarah Mitchell", "org_owner", "Managing Director", ["Management"]),
        ("Patrick Kinyanjui Muigai", "org_admin", "Operations Director", ["Management"]),
        ("Dr. Beatrice Nyambura", "department_supervisor", "Quality Assurance Manager", ["Quality Assurance"]),
        ("Martin Odera", "department_supervisor", "Pack House Manager", ["Pack House"]),
        ("Wilson Kirui", "content_publisher", "Outgrower Coordinator", ["Farm Operations"]),
        ("Jane Wanjiku", "member", "Picker Team Leader", ["Farm Operations", "Pickers"]),
        ("Peter Kamau Mbugua", "client", "Outgrower (French beans, Thika)", ["Outgrowers"]),
        ("Pieter de Vries", "client", "Buyer, Fresh Holland BV", ["Overseas Buyers"]),
    ],
    "contacts": [
        ("Farm Office", "Thika farm", "Farm Operations", True, []),
        ("Pack House Floor", "Grading and packing", "Pack House", True, ["Pack House", "Pickers"]),
        ("QA Lab", "Residue testing", "Quality Assurance", True, ["Outgrowers"]),
        ("Export Desk - JKIA", "Air freight", "Export Logistics", True, ["Overseas Buyers"]),
        ("Outgrower Coordinator", "Seeds, spraying schedules", "Farm Operations", True, ["Outgrowers"]),
    ],
    "emergency": [("Police", "999", "Police"), ("Poison Information Centre", "0700317111", "Chemical exposure")],
    "notices": [
        ("Spraying schedule", "Outgrowers: no spraying within 7 days of harvest. QA will test every batch.", ["Outgrowers"], True),
        ("Shipment cut-off", "Friday's flight to Amsterdam closes at 4pm. Pack house works until 2pm.", ["Pack House", "Overseas Buyers"], False),
    ],
    "contact_groups": [("Outgrower support", ["Outgrower Coordinator", "QA Lab"], ["Outgrowers"])],
}

LAW_MUTUA = {
    "name": "Mutua, Hassan & Associates", "edition": "legal", "plan": "enterprise_standard", "code": "2118",
    "departments": [("Partners", None), ("Criminal Law", None), ("Land & Succession", None), ("Administration", None)],
    "groups": [("Succession Clients", "segment", "Land & Succession"), ("Pupils", "team", None)],
    "people": [
        ("Benjamin Mutua Kilonzo", "org_owner", "Senior Partner", ["Partners"]),
        ("Khadija Hassan", "org_admin", "Partner", ["Partners", "Criminal Law"]),
        ("Dorcas Mwende", "department_supervisor", "Head of Land & Succession", ["Land & Succession"]),
        ("Kevin Musyoka", "member", "Associate", ["Criminal Law"]),
        ("Ann Nzisa", "member", "Pupil", ["Pupils", "Land & Succession"]),
        ("Joyce Mueni Kioko", "client", "Client (Succession, Machakos)", ["Succession Clients"]),
        ("Thomas Ndambuki", "client", "Client (Land dispute)", []),
    ],
    "contacts": [
        ("Machakos Office", "Reception", "Administration", True, []),
        ("Criminal Desk", "Bail and plea", "Criminal Law", True, ["client"]),
        ("Succession Desk", "Grants and confirmations", "Land & Succession", True, ["Succession Clients"]),
        ("Court Clerk - Machakos", "Court runner", "Administration", False, []),
        ("Accounts", "Fee notes", "Administration", True, ["client"]),
    ],
    "emergency": [("Police", "999", "Police"), ("Duty Advocate (after hours)", "0700318911", "Arrests after hours")],
    "notices": [("Succession clinic", "Free succession clinic at our Machakos office on the last Friday of each month.", [], True)],
    "contact_groups": [("Court team", ["Criminal Desk", "Court Clerk - Machakos"], ["Pupils", "Criminal Law"])],
}

LAW_ACHIENG = {
    "name": "Achieng & Lindqvist Advocates", "edition": "legal", "plan": "enterprise_standard", "code": "2119",
    "departments": [("Partners", None), ("Human Rights & Public Interest", None), ("NGO & Charities", None), ("Litigation", None)],
    "groups": [("NGO Clients", "segment", "NGO & Charities"), ("Volunteers", "team", None)],
    "people": [
        ("Millicent Achieng Awuor", "org_owner", "Managing Partner", ["Partners"]),
        ("Erik Lindqvist", "org_admin", "Partner", ["Partners", "NGO & Charities"]),
        ("Brian Ochieng Okello", "department_supervisor", "Head of Litigation", ["Litigation"]),
        ("Nasra Abdi", "content_publisher", "Communications & Outreach", ["Human Rights & Public Interest"]),
        ("Lydia Nyanchama", "member", "Associate", ["Human Rights & Public Interest", "Volunteers"]),
        ("Elena Rossi", "client", "Programme Lead, Lake Basin Water Trust", ["NGO Clients"]),
        ("Samuel Oduor", "client", "Client (Public interest petition)", []),
    ],
    "contacts": [
        ("Kisumu Office", "Milimani, Kisumu", "Partners", True, []),
        ("NGO Compliance Desk", "Registration and returns", "NGO & Charities", True, ["NGO Clients"]),
        ("Litigation Desk", "Court dates", "Litigation", True, ["client"]),
        ("Legal Aid Clinic", "Free advice on Wednesdays", "Human Rights & Public Interest", True, []),
        ("Volunteer Coordinator", "Clinic volunteers", "Human Rights & Public Interest", False, ["Volunteers"]),
    ],
    "emergency": [("Police", "999", "Police"), ("GBV Hotline", "1195", "Gender-based violence")],
    "notices": [
        ("NGO annual returns", "NGO clients: annual returns are due by 31 March. Send your accounts to the compliance desk.", ["NGO Clients"], True),
        ("Legal aid clinic", "The free legal aid clinic runs every Wednesday 9am to 1pm.", [], False),
    ],
    "contact_groups": [("Clinic team", ["Legal Aid Clinic", "Volunteer Coordinator"], ["Volunteers"])],
}

LAW_NJOROGE = {
    "name": "Njoroge, Mensah & Co. Advocates", "edition": "legal", "plan": "enterprise_pro", "code": "2120",
    "departments": [("Partners", None), ("Banking & Finance", None), ("Intellectual Property", None), ("Tax", None),
                    ("Dispute Resolution", None)],
    "groups": [("Bank Clients", "segment", "Banking & Finance"), ("Start-up Clients", "segment", None), ("Associates", "team", None)],
    "people": [
        ("Margaret Njeri Njoroge", "org_owner", "Managing Partner", ["Partners"]),
        ("Kofi Mensah", "org_admin", "Partner, Banking & Finance", ["Partners", "Banking & Finance"]),
        ("Aditi Rao", "department_supervisor", "Partner, Intellectual Property", ["Intellectual Property"]),
        ("Collins Kiprono Bett", "content_publisher", "Knowledge Manager", ["Partners"]),
        ("Sharon Atieno", "member", "Senior Associate, Tax", ["Tax", "Associates"]),
        ("Dennis Wekesa Simiyu", "member", "Associate, Dispute Resolution", ["Dispute Resolution", "Associates"]),
        ("Amara Okafor", "client", "CEO, Nyota Digital Solutions", ["Start-up Clients"]),
        ("William Harper", "client", "Head of Legal, Meridian Bank Kenya", ["Bank Clients"]),
        ("CPA Grace Wairimu", "auditor", "Auditor", []),
    ],
    "contacts": [
        ("Reception - Upper Hill", "Appointments", "Partners", True, []),
        ("Banking Desk", "Securities and facilities", "Banking & Finance", True, ["Bank Clients"]),
        ("IP Desk", "Trademarks and patents", "Intellectual Property", True, ["Start-up Clients"]),
        ("Tax Desk", "KRA objections and advisory", "Tax", True, ["client"]),
        ("Arbitration Desk", "Disputes", "Dispute Resolution", False, []),
        ("Billing", "Fee notes", "Partners", True, ["client"]),
    ],
    "emergency": [("Police", "999", "Police"), ("Building Security - Upper Hill", "0700320900", "Office security")],
    "notices": [
        ("Finance Act briefing", "Join our breakfast briefing on the new Finance Act at the Upper Hill office, Thursday 7:30am.", ["client"], True),
        ("Trademark renewals", "Start-up clients: trademarks registered in 2016 must be renewed this year.", ["Start-up Clients"], False),
    ],
    "contact_groups": [("Start-up desk", ["IP Desk", "Tax Desk", "Billing"], ["Start-up Clients"])],
}

# Order matters: phone numbers come from the position here, so add new organisations at the END.
ORGANISATIONS = [
    SCHOOL_KILIMANI, SCHOOL_LAKEVIEW, SACCO_UFANISI, SACCO_JUMUIKA, COMPANY_SAVANNAH, COMPANY_NYOTA, LAW_WANJIRU, LAW_KIBET,
    SCHOOL_MOMBASA, SCHOOL_SUNRISE, SCHOOL_RIFT, SACCO_MKULIMA, SACCO_AFYA, SACCO_PWANI,
    COMPANY_BARAKA, COMPANY_TAUSI, COMPANY_GREENFIELD, LAW_MUTUA, LAW_ACHIENG, LAW_NJOROGE,
]

# People using Remote Phonebook on their own (no organisation): (name, what they do, [their own contacts])
INDIVIDUALS = [
    ("Wairimu Njoroge", "Nurse, Kenyatta National Hospital",
     ["Mum", "Ward 4B Nurses' Station", "Kevin (brother)", "Matatu SACCO Driver", "Landlord - Mr. Gitau"]),
    ("James O'Connor", "Irish expat, NGO programme manager in Nairobi",
     ["Siobhan (wife)", "Office - Gigiri", "Askari - Peter", "Dr. Shah (GP)", "Embassy Duty Officer"]),
    ("Hassan Abdi Mohamed", "Livestock trader, Garissa",
     ["Abdi (father)", "Market Agent - Ali", "Lorry Driver - Juma", "Vet - Dr. Noor"]),
    ("Elena Rossi", "Italian researcher, ILRI Nairobi",
     ["Marco", "Lab Manager", "Taxi - Samuel", "Yoga Class"]),
    ("Kevin Ouma", "University of Nairobi student",
     ["Mama", "Roommate - Dan", "Class Rep", "Hostel Warden", "Fundi - Phone Repair"]),
    ("Mei Lin", "Civil engineer, SGR project",
     ["Site Office", "Interpreter - Wanjiku", "Driver - Omari", "Wei (husband)"]),
    ("Fatma Said", "Small business owner, Mombasa Old Town",
     ["Supplier - Spices", "M-Pesa Agent", "Daughter - Zainab", "Accountant - Rashid"]),
    ("Daniel Kimani Waweru", "Farmer, Nyeri (coffee and dairy)",
     ["Coffee Factory", "AI Technician", "Milk Collector", "Agrovet - Mama Njeri"]),
]

# Each organisation's profile (Settings > Organisation profile): (about, address, logo colour). Phone, email and website
# are made up from the name (".example" addresses never reach anyone).
PROFILES = {
    "Kilimani Hills Academy": ("A mixed day and boarding secondary school in Kilimani, Nairobi, founded in 1998. 640 students, "
                               "strong in sciences, drama and debate.", "Argwings Kodhek Road, Kilimani, Nairobi", (27, 94, 160)),
    "Lakeview Girls' High School": ("A national girls' boarding school on the shores of Lake Victoria, known for languages "
                                    "and debate.", "Off Nairobi Road, Kisumu", (126, 58, 145)),
    "Ufanisi Teachers SACCO": ("Savings and loans for teachers in the Rift Valley since 1979: development, school-fees and "
                               "emergency loans, and yearly dividends.", "Ufanisi House, Kenyatta Avenue, Nakuru", (0, 121, 107)),
    "Jumuika Boda Boda SACCO": ("Kisumu's boda boda riders saving together: daily contributions, motorbike loans and "
                                "insurance cover.", "Kondele Roundabout, Kisumu", (230, 126, 34)),
    "Savannah Logistics Ltd": ("Clearing, forwarding and trucking between Mombasa Port, Nairobi and Kampala. 120 trucks, "
                               "24-hour dispatch.", "Moi Avenue, Mombasa", (183, 110, 28)),
    "Nyota Digital Solutions": ("We build mobile money and banking apps for East African lenders and SACCOs.",
                                "The Mirage, Westlands, Nairobi", (63, 81, 181)),
    "Wanjiru, Okafor & Patel Advocates": ("A full-service law firm: litigation, conveyancing, corporate and family law. "
                                          "Commissioners for oaths and notaries public.", "ACK Garden House, Upper Hill, Nairobi", (55, 71, 79)),
    "Kibet & Fernandes LLP": ("Employment, shipping and maritime law for businesses at the Coast.",
                              "Nyali Centre, 3rd Floor, Mombasa", (21, 101, 192)),
    "Mombasa Coast Academy": ("A day secondary school in Nyali with a strong sports and swimming tradition.",
                              "Links Road, Nyali, Mombasa", (0, 150, 136)),
    "Sunrise Junior School": ("A CBC primary and junior secondary school with school transport across Eldoret.",
                              "Kapsoya Estate, Eldoret", (245, 166, 35)),
    "Rift Valley International School": ("An IB World School for ages 3 to 18, with day and boarding places.",
                                         "Moi South Lake Road, Naivasha", (25, 118, 210)),
    "Mkulima Dairy Farmers SACCO": ("Dairy farmers in Nyeri marketing milk together: monthly payments, inputs on credit, "
                                    "AI and vet services.", "Othaya Town, Nyeri County", (76, 175, 80)),
    "Afya Health Workers SACCO": ("Savings, loans and housing for nurses, clinical officers and other health workers.",
                                  "Afya Plaza, Hospital Road, Nairobi", (211, 47, 47)),
    "Pwani Fishermen SACCO": ("Fishermen and fish traders of Kilifi and Malindi: savings, gear loans and a cold room.",
                              "Kilifi Landing Site, Kilifi", (2, 136, 209)),
    "Baraka Supermarkets Ltd": ("A family-owned supermarket chain in Nakuru and Naivasha, open 7am to 10pm every day.",
                                "Kenyatta Avenue, Nakuru", (198, 40, 40)),
    "Tausi Hotels & Resorts": ("A 120-room beach resort on Diani Beach, with conference and wedding facilities.",
                               "Diani Beach Road, Kwale", (0, 131, 143)),
    "Greenfield Agro Exports": ("Growers and exporters of French beans, snow peas and avocados to Europe, working with "
                                "400 outgrowers.", "Garissa Road, Thika", (56, 142, 60)),
    "Mutua, Hassan & Associates": ("Criminal law, land and succession matters in Machakos and Makueni counties.",
                                   "Ngei Road, Machakos", (93, 64, 55)),
    "Achieng & Lindqvist Advocates": ("Human rights, public interest and NGO law, with a free legal aid clinic every Wednesday.",
                                      "Milimani, Kisumu", (142, 36, 170)),
    "Njoroge, Mensah & Co. Advocates": ("Banking, tax, intellectual property and dispute resolution for banks, corporates "
                                        "and start-ups.", "Upper Hill Chambers, Nairobi", (38, 50, 56)),
}
