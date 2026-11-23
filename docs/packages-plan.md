# Remote Phonebook: Packages, Enterprise Editions, RBAC and Billing (implementation plan)

Status: **approved (2026-11-23), with the decisions below.** Section 9 keeps the questions that are still open.

## Decisions (2026-11-23)
- **Prices:** the Super Admin dashboard lists every plan, add-on and USSD code fee, with editing. The placeholder prices are seeded
  as editable rows. Nothing is hard-coded.
- **USSD codes:** the Super Admin adds purchased codes (shared, extension or dedicated) to an inventory, then assigns them to tenants
  from that list. For testing, the current `*384*88041#` is seeded as the main shared code and can be changed in the dashboard
  (no longer a constant in code).
- **Student results:** visible to the student and to linked guardians. Both are assumed to have their **own phones and their own
  accounts**. Results and assignments are only shown to a logged-in account holder (PIN).
- **USSD from another phone:** a per-tenant operator setting, `allow_other_phone`, **default on**. When on, the member logs in with
  phone number + PIN (today's flow). When off, only the caller's own registered number can log in.
- **"SMS me my contacts":** the user's network operator charges for this, so it isn't billed by us. The billable restore is
  restoring backed-up contacts onto a phone through the app (to be built in Phase 3).
- **Uncommitted work:** committed as `e209f6f` before Phase 0.

---

## 1. What exists today

| Area | Current state |
|---|---|
| Stack | One Flask app, `app.py` (~3,200 lines), run by gunicorn (gthread, 2 workers × 4 threads) on Render's **free** plan. |
| Database | Raw SQL through `fetch_one / fetch_all / execute`, written with `?` placeholders and rewritten to `%s` for Postgres. Postgres (Neon, pooled) in production, SQLite locally. No ORM. |
| Migrations | `init_db()` runs `CREATE TABLE IF NOT EXISTS` and `add_column()` at startup under a Postgres advisory lock. No migration history table. |
| Identity | `users.phone` (TEXT primary key) is the account. It has a 4-digit USSD PIN (`pin_hash`) and an app password (`password_hash`), both hashed with werkzeug (scrypt), with lockouts. Up to 3 emails per account (`user_emails`). |
| App auth | itsdangerous-signed bearer tokens carrying a `jti`, `app_sessions`, `revoked_tokens`, and "log out everywhere" via `token_epoch`. |
| USSD | Africa's Talking. Every request replays the full `text` path. `handle()` does login (phone number, then PIN), resume within 40 s, and `menu()` dispatches on the path. Helpers: `con/end/screen`, `NAV` (0/00/000), `PAGE_SIZE=4`, `LIST_SCREEN_LEN=160`, `format_contacts`, `plain_text` (GSM-safe). Actions run only when the path ends on them. |
| Data | Personal contacts (`contacts.owner = phone`), optional AES-GCM encryption at rest (`CONTACTS_KEY`), priority contacts. |
| Admin | `/admin` uses a single `ADMIN_PASSWORD`, a signed cookie, CSRF and a lockout. It has feature switches (`settings` table, `feature_on()`), message templates, a notification log, complaints and replies, and email over SMTP. |
| Messaging | AT SMS (`send_sms_async`), AT Voice (call bridging), email, in-app notifications. |
| Clients | `mobile/App.tsx`: one 2,350-line Expo file with state-based screens (no router), also exported as the website into `web/`. The untracked `phone-book/` folder is a blank Expo Router template (see Q11). |
| Billing | **None.** No payments, plans or invoices. |
| Restore | **Not built.** The app backs device contacts *up* (`/api/contacts/sync`), but nothing writes contacts back into a new phone. "SMS me my contacts" over USSD is the closest thing. |
| Tests | **None.** |

### Constraints this plan works within
- **USSD timeout is about 10 s, and Render free instances sleep after 15 min idle.** A cold start can exceed the
  USSD timeout, so the first dial after a quiet period fails. A paid subscriber service needs an always-on
  instance (Render Starter or above). This is a precondition for going live, not something to fix in code.
- The current `/ussd` design (replay the whole path, stateless apart from `ussd_resume`) works well. I'll keep it.
- The existing personal phone book must keep working unchanged throughout.

---

## 2. Guiding decisions

1. **Split `app.py` into a package and keep Flask and raw SQL.** Adding about 35 tables and 7 modules to a
   3,200-line file isn't workable. I'll create a `pb/` package and move the existing code into it unchanged (Phase 0).
   Moved code keeps registering routes on the shared `app` object, so endpoint names don't change; new modules use
   blueprints. `app.py` stays as the entry point (`app:app`), so Render and gunicorn don't change. Keeping raw SQL
   (rather than adopting SQLAlchemy) matches the codebase and keeps the SQLite and Postgres support.
2. **Numbered migrations.** I'll add a `schema_migrations` table and `pb/migrations/NNNN_name.py` files that run in order at
   startup under the existing advisory lock. The current `init_db()` becomes migration `0001`, and since it is idempotent,
   existing databases pass straight through it.
3. **Tenant isolation in the data layer.** Tenant-owned tables are only reachable through
   `TenantRepo(tenant_id)`, which adds `tenant_id = ?` to every query. A lint test fails if a module touches a tenant table
   through the raw helpers. In production, **Postgres Row-Level Security** is a second layer (`SET LOCAL app.tenant_id` on every
   request). SQLite has no RLS, so it relies on the repository layer and the tests.
4. **Personal accounts stay as they are.** Individual users keep `contacts.owner = phone`. There's no data migration for
   existing users. Enterprise directories use new tables with a `tenant_id` column.
5. **Identity is global, membership is per tenant.** One `users` row per phone number. A person can be a SACCO
   member, a guardian at a school and an individual subscriber, through separate `memberships`.
6. **The web portal for operators and Super Admin is server-rendered Flask and Jinja**, extending the existing `/admin`
   (it already has CSRF, cookies and templates, and needs no app build or release). The **Expo app and website remain the
   member-facing secondary channel** (results, statements, assignments, directory). See Q10 if you'd rather the portal were in Expo.
7. **USSD first, every time.** In each module the USSD flow is built and tested before the API, and app and web
   views come last.
8. **Hashing:** keep werkzeug scrypt. It's a memory-hard KDF in the same class as argon2 and bcrypt, and changing it
   would force every PIN and password to be reset. Unknown hash formats are rehashed on the next successful login, so we can
   switch to argon2 later.

---

### Phase 1 notes (2026-11-23)
- The portal is server-rendered Flask at `/platform` (Q10 default). `/admin` stays for the personal phone book's
  switches, templates and complaints; its password is a break-glass Super Admin in `/platform`.
- **Postgres Row-Level Security is deferred to Phase 4**, when tenant content tables arrive. `execute()` commits after
  every statement, so a per-transaction `SET LOCAL app.tenant_id` wouldn't survive. RLS needs a transaction-scoped helper first.
  Until then isolation is enforced by `TenantRepo` plus a test that fails on raw queries to tenant tables.
- Permissions are a catalogue in code; system roles are synced into the database at every start.
- Supervisors also hold the Member permissions, so they can give people the Member role in their departments.

## 3. Target code layout

```
app.py                      # entry point: from pb import create_app; app = create_app()
pb/
  core/        db.py (helpers, TenantRepo, RLS), migrations/, settings.py (feature flags), audit.py, i18n.py, jobs.py
  auth/        pins.py, passwords.py, otp.py, tokens.py, staff.py (platform staff logins)
  rbac/        permissions.py (catalogue), engine.py (can(), scopes, visibility), seed.py (system roles)
  tenants/     tenants.py, editions.py (presets → modules), entitlements.py (plan features + flags)
  billing/     plans.py, pricing.py (proration, tiers, quotas), invoices.py, subscriptions.py,
               providers/base.py (PaymentProvider), providers/mpesa.py (Daraja), restore_billing.py
  ussd/        router.py (code → tenant), session.py (state machine, resume), screens.py (paginate/format),
               personal.py (today's menu, moved), tenant_menu.py (built from modules + custom services)
  modules/     members/, directory/ (contacts, groups, visibility, onboarding sync), announcements/,
               emergency/, schools/ (results, assignments, guardians), sacco/ (finance), legal/ (matters),
               insurance/, company/, builder/ (custom USSD services), retention/, support/, consent/
  messaging/   sms.py, email.py, push.py, notify.py (moved from app.py)
  api/         member-facing JSON API (app/web), integrations API (API keys), webhooks
  portal/      Jinja views: super admin, finance, support, reseller, tenant operator
tests/         pytest: unit (rbac, pricing, screens), integration (ussd per edition, billing, isolation)
```

Each module exposes the same small interface, so the USSD menu, API and portal can find it:
`MODULE = Module(key, permissions, ussd_entry, api_blueprint, portal_blueprint, retention_types)`.

---

## 4. Schema (new tables)

All tables are prefixed logically. Tenant-owned tables have `tenant_id NOT NULL` and an index that starts with `tenant_id`.
Money is stored as **integer cents (KES × 100)**. Timestamps are integer epoch seconds, as in the existing tables.

**Platform and tenancy**
- `tenants` (id, name, slug, kind `individual|enterprise`, edition, status `trial|active|grace|suspended|closed`,
  org_code, language, reseller_id, created_at)
- `editions` (key, name, active, default_modules JSON): schools, insurance, legal, sacco, company, plus the suggested
  churches, chama, health, estate and county editions (inactive)
- `modules` (key, name, description) and `tenant_modules` (tenant_id, module_key, source `plan|addon|manual`, enabled)
- `ussd_code_allocations` (tenant_id, type `shared|extension|dedicated`, code, status, setup_fee, monthly_fee,
  starts_at, ends_at)
- `feature_flags` (key, scope `global|tenant`, tenant_id NULL, value). This generalises the existing `settings` switches,
  which stay as global flags.

**Accounts and RBAC**
- `users`: the existing table, plus `language`, `pin_set_at`, `otp_secret` (unused while OTP is off) and `status`
- `staff_users` (platform staff: email, password_hash, name, active). Platform roles are assigned through `role_assignments` with
  `tenant_id NULL`.
- `memberships` (tenant_id, user_phone, display_name, member_no, status `active|inactive|departed`, joined_at, left_at)
- `permissions` (key, description, module_key): a catalogue seeded in code
- `roles` (id, tenant_id NULL for system roles, key, name, is_system) and `role_permissions` (role_id, permission_key)
- `role_assignments` (id, tenant_id, principal `user:<phone>|staff:<id>`, role_id, scope_type
  `tenant|department|group|matter|client`, scope_id, granted_by, created_at)
- `departments` (tenant_id, name, parent_id), `groups` (tenant_id, kind `class|stream|fellowship|segment|custom`, name, parent_id),
  `group_members` (tenant_id, group_id, membership_id, role_in_group)
- `visibility_rules` (tenant_id, role_id, resource_type `contact_group|department|announcement_audience|results|finance`,
  resource_id, scope_type, scope_id, granted_by)
- `audit_log` (id, tenant_id NULL, actor, action, target_type, target_id, details JSON, ip, channel, created_at).
  Append-only, with no update or delete path other than retention.

**Directory and content**
- `org_contacts` (tenant_id, name, phone, title, department_id, kind `staff|client|partner|emergency`, enc), with
  `contact_groups` and `contact_group_items`
- `announcements` (tenant_id, author, title, body, priority `normal|emergency`, audience JSON, publish_at, expires_at,
  pinned, sms_sent_at, status) and `announcement_reads` (tenant_id, announcement_id, membership_id, channel, read_at)
- `emergency_contacts` (tenant_id, subject_type `tenant|membership`, subject_id, name, phone, relation, sort)
- `guardian_links` (tenant_id, guardian_membership_id, student_membership_id, relation)
- `result_slips` (tenant_id, student_membership_id, term, exam, mean_grade, position, out_of, remarks, published_at, pdf_key)
  and `result_lines` (slip_id, subject, score, grade, teacher_remark)
- `assignments` (tenant_id, group_id, subject, title, body, due_at, delivery `full|link`, short_code, author) and
  `assignment_files` (tenant_id, assignment_id, filename, storage_key, size)
- `matters` (tenant_id, ref, title, client_membership_id, status) and `matter_advocates` (tenant_id, matter_id, contact_id)
- `financial_records` (tenant_id, scope `org|member`, membership_id NULL, kind `dividend_rate|loan_product|agm|shares|savings|loan_balance|contribution`,
  label, amount, as_of, details JSON)
- `ussd_services`, `ussd_menu_versions` (tenant_id, version, tree JSON, status `draft|published|archived`, published_by)
  and `service_submissions` (tenant_id, service_key, membership_id, answers JSON, status, created_at)
- `retention_policies` (tenant_id, data_type, keep `6m|1y|3y|forever`) and `retention_minimums` (data_type, minimum),
  plus `retention_runs` (log of what was removed)
- `consents` (user_phone, tenant_id NULL, purpose, version, channel, granted_at, withdrawn_at)
- `support_tickets` (tenant_id, author, subject, body, status) and `ticket_messages`
- `api_keys` (tenant_id, name, prefix, key_hash, scopes, last_used, revoked_at), `webhooks` and `webhook_deliveries`
- `files` (tenant_id, storage_key, mime, size). Storage: Neon Object Storage or S3-compatible, decided in Phase 5.

**Billing**
- `plans` (key, name, kind `per_transaction|recurring|seat|quoted`, interval, base_price, annual_discount_pct,
  included_members, per_member_price, active, visible)
- `plan_features` (plan_key, feature_key, value): module access, admins limit, quotas, custom roles and so on
- `plan_tiers` (plan_key, from_members, per_member_price): volume tiers
- `addons` (key, name, price, interval, module_key NULL)
- `promo_codes` (code, percent_off|amount_off, max_uses, valid_until, plan_keys) and `reseller_commissions`
- `subscriptions` (tenant_id or user_phone, plan_key, status `trialing|active|past_due|grace|suspended|cancelled`,
  interval, current_period_start/end, trial_ends_at, cancel_at, promo_code)
- `subscription_items` (subscription_id, kind `plan|addon|ussd_code|seats|overage`, ref, quantity, unit_price)
- `invoices` (number, tenant_id or user_phone, status `draft|open|paid|void`, subtotal, discount, total, due_at, paid_at)
  and `invoice_lines` (invoice_id, description, kind, quantity, unit_price, amount, period_start, period_end)
- `payments` (id, provider, idempotency_key UNIQUE, invoice_id NULL, purpose, amount, phone, status
  `pending|succeeded|failed|refunded`, provider_ref, raw JSON, created_at, settled_at)
- `usage_counters` (tenant_id, period, metric `sms|ussd_session`, count): quotas and overage
- `restore_orders` (id, user_phone, device, contact_count, payment_id, status
  `quoted|awaiting_payment|paid|delivering|delivered|failed|refunded`, created_at)

---

## 5. Key designs

### 5.1 RBAC engine
- Permission keys look like `<area>.<verb>[_scope]`: `contacts.view`, `contacts.manage`, `results.publish`, `results.view_own`,
  `results.view_linked`, `finance.view_own`, `finance.publish`, `ussd_service.create`, `roles.manage`, `audit.view` and so on.
- A single entry point, `can(principal, permission, resource=None) -> bool`. It loads the principal's assignments (cached
  per request), keeps those whose role holds the permission, and checks that the assignment scope covers the resource
  (tenant ⊇ department ⊇ group, matter, client). Every channel calls the same function.
- Contact visibility: `visible_contacts(membership)` joins `visibility_rules` on the member's roles and scopes. USSD
  lists, API and portal all call it, so no channel filters on its own.
- Granting: `grant()` refuses any permission or scope that the granter doesn't hold, and writes to the audit log.
- Custom roles are allowed only when the plan feature `custom_roles` is on.
- System roles (seeded): every role in your table, as named permission bundles.

### 5.2 USSD routing and the screen helper
- `router.resolve(serviceCode, text)` returns `(tenant, remaining_parts)`:
  - **Dedicated code:** `serviceCode` matches a `dedicated` allocation.
  - **Dedicated extension:** `*384*88041*5#` arrives as `text="5*..."`, and the first part matches an `extension` allocation.
  - **Shared org code:** the first part matches `tenants.org_code`, or the user picks "Organisations" from a menu.
  - Otherwise, today's personal phone book, unchanged.
- `session.py` holds the login, PIN and resume state machine, generalised from today's `handle()`, with no business logic.
  It then calls `tenant_menu.render(ctx, path)`, where `ctx = (tenant, user, membership, caller, lang)`.
- `screens.py` is the one helper every module uses:
  - `page_list(items, render_item, page)`: numbered items, `98. More`, `0. Back`, kept under `LIST_SCREEN_LEN` (160)
  - `page_text(text, page)`: splits long text on word boundaries into `Page 1/4` screens
  - `fit(lines)`: trims to 182 characters with the NAV line, GSM-safe through `plain_text`
  - Tests prove that every screen in every module is at most 182 characters.
- Sensitive screens (results, finance, personal records, assignments if the tenant opts in) call `require_pin(ctx)`,
  which reuses `check_pin` and the lockout, then OTP if the flag is on for that tenant.
- An emergency broadcast is pinned as the first line of the tenant menu until it expires.
- Kiswahili: every USSD string goes through `t(key, lang)`, with string tables in `pb/core/i18n/{en,sw}.json`.

### 5.3 Billing
- `PaymentProvider` interface: `charge(phone, amount, reference, idempotency_key) -> PaymentIntent`,
  `parse_callback(request) -> PaymentResult`, `query(intent)`, `refund(payment)`.
  `MpesaDaraja` implements it with STK Push, the callback and the transaction-status query. Business code only sees the interface.
- **Verifying callbacks:** Daraja callbacks aren't signed. They're accepted only on a secret callback path, matched to a
  `pending` payment by `CheckoutRequestID`, and **confirmed with an STK Query** before anything is fulfilled. Optionally,
  Safaricom's published source IPs can also be allowlisted.
- **Idempotency:** a unique `payments.idempotency_key`. A repeated callback is a no-op. A reconciliation job queries
  anything left `pending` for more than 2 minutes and settles it.
- **Pay per restore:** quote (count, price) → confirm → STK Push → the app polls → on `succeeded` the order moves to
  `delivering` → the app writes the contacts to the device and confirms → `delivered`.
  If delivery fails or is never confirmed within N hours, the order is refunded automatically. Daraja refunds use the B2C or reversal API,
  which needs extra credentials. Until those exist, a failed restore becomes a **free restore credit** plus a Finance task (see Q5).
- Proration, tiers, quotas and the annual discount live in `pricing.py` as pure functions with unit tests. Invoices are generated by a
  daily job. Grace period: N days `past_due`, then `grace`, then `suspended` (data kept, logins and USSD blocked with a
  "service paused, contact your admin" screen).

### 5.4 Background jobs
There's no job runner today. Scheduled posts, retention, renewals, reconciliation and dunning need one. **Plan:** a
`/internal/tick` endpoint protected by a secret, called every minute by a Render Cron Job (or any external cron). Each job
is claimed with one atomic `UPDATE` on `job_runs` so it never runs twice. That works through Neon's pooler, where
session-level advisory locks aren't reliable. Messages are sent through the existing async helpers.

### 5.5 Custom USSD service builder (Phase 6)
The menu tree is stored as JSON in `ussd_menu_versions`. The editor is a portal page (tree, drag to reorder, item form). Validation
covers depth ≤ 5, label length, and every rendered screen ≤ 182 characters. The simulator calls the same `tenant_menu.render` on the
draft version. Publishing archives the previous version, so rolling back means republishing an old version.

### 5.6 Data protection (Kenya DPA 2019)
- Consent on first use over USSD (one screen: "1. Agree 2. Read terms (SMS)"), versioned in `consents`.
- Export (JSON or CSV by email or app download) and deletion on request, with deletion kept in the audit log.
- Minors: students' records are marked `minor`. Their results are only shown to the people Q3 allows. Nothing is sent to them by SMS
  unless a guardian has consented. They're excluded from directory sharing by default.

---

## 6. Migration strategy
- Phase 0 moves code without changing behaviour. Before the move I'll add a USSD regression suite covering today's flows, so the move is
  checked by tests rather than by eye.
- Migrations only add things. No existing table is renamed or dropped. Existing users get `memberships` only when an enterprise adds them.
- Every migration runs on SQLite (local) and on a **Neon branch** before production. Rollback means restoring the Neon branch to a point in time.
- Prices are seeded from your placeholder tables with `active=false` for production until you confirm them (Q1).

---

## 7. Phases

| # | Phase | Contents | Rough size |
|---|---|---|---|
| 0 ✅ | **Groundwork** (done 2026-11-23) | pytest and a regression suite for the current USSD and API; split `app.py` into `pb/` (no behaviour change); migrations framework; job tick endpoint | 1 week |
| 1 ✅ | **Foundation** (done 2026-11-23) | tenants, editions, modules, flags; users and memberships; staff accounts; RBAC engine and seeded roles; audit log; OTP (built, off); plans, pricing and code-allocation tables; Super Admin portal (tenants, plans, flags, codes, staff); tenant-isolation tests | 2–3 weeks |
| 2 | **USSD core** | router (shared, extension, dedicated), generalised session and PIN flows, `screens.py`, consent screen, portal USSD simulator, i18n plumbing | 1–2 weeks |
| 3 | **Billing** | provider interface and Daraja (sandbox first), **restore-to-device in the app** plus pay-per-restore, Individual subscriptions, enterprise subscriptions, seats, code fees, invoices, proration, grace and suspension, Finance portal | 2–3 weeks |
| 4 | **Shared enterprise modules** | members and departments and groups (with CSV import), directory and visibility rules, onboarding contact sync, announcements (scheduled, expiring, reads, SMS), emergency contacts and broadcast, retention settings and job, operator portal | 3 weeks |
| 5 | **Editions** | Schools (results, assignments full or link, guardians, PDFs), SACCO finance, Law matters, Insurance, Companies; app and web views | 3–4 weeks |
| 6 | **USSD service builder** | tree editor, versions and publishing, simulator, form submissions and export, payment-link service | 2 weeks |
| 7 | **Extras** | analytics, API keys and webhooks, full Kiswahili, push notifications, suggested editions, support tickets, reseller portal | 2–3 weeks |

The sizes are for one developer, as a rough guide. After each phase I'll stop and report what changed, the migrations, and how to test it.

**Deferred unless you say otherwise:** carrier or airtime billing (only the interface is built), white-labelling (Custom plan),
push notifications until Phase 7 (they need an EAS build with FCM and APNs credentials).

---

## 8. Things to set up outside the code
- An always-on Render instance (see §1) and a Render Cron Job.
- Daraja: sandbox app now. For production: a paybill or till number, a passkey, a callback URL, and B2C credentials if we're to refund to M-Pesa.
- Africa's Talking: dedicated extensions and shortcodes are requested from AT and Safaricom (weeks of lead time, with carrier fees).
- Object storage for PDFs and attachments (Phase 5).
- A privacy policy and terms text (and their Kiswahili versions), plus your ODPC registration as a data controller or processor.

---

## 9. Questions for you

Answered 2026-11-23: questions 1–4, 5 (partly) and 12. See *Decisions* at the top. Still open: whether restoring to the same
phone counts as a paid restore, refunds versus a free retry credit (default until answered: **free retry credit**), and 6–11.

**Blocking Phase 1–2**
1. **Prices.** Are the placeholder plans and code fees fine to seed as *inactive* drafts until you confirm them?
2. **Main code and org-code format.** Do we stay on `*384*88041#` (AT shared channel), or are you getting your own code?
   For org codes on the shared code: numeric only (e.g. `*384*88041*2045#`, 3–5 digits), or a menu choice "Organisations → enter code"?
3. **Who sees student results:** the student, the guardian, or both? And do assignments need a PIN by default?
4. **Enterprise USSD identity.** Today a user types their phone number, so they can use any phone. For enterprise members
   I propose **caller ID + PIN** (faster, two fewer screens), with "use another phone" available. OK?

**Needed by Phase 3**
5. **What counts as a billable "restore"?** I propose: writing backed-up contacts into a phone through the app (a new feature).
   Does "SMS me my contacts" over USSD stay free? Does restoring to the *same* phone count? If an M-Pesa refund isn't possible yet,
   is a free retry credit acceptable?
6. **Free tier for backup.** Free, or a small fee? Any limit (e.g. 5,000 contacts)?
7. **VAT.** Are prices VAT-inclusive (16%)? Do invoices need KRA eTIMS integration?

**Product and scope**
8. **Grace period and trial length:** e.g. 7 days' grace, 14-day trial?
9. **Edition modules for Standard:** included, or sold as add-ons by default?
10. **Portal technology.** I recommend server-rendered Flask pages for the operator and Super Admin portals (fast to build, like
    today's `/admin`). Do you want them inside the Expo web app instead?
11. **The untracked `phone-book/` folder** is a blank Expo Router template. Is it meant to replace `mobile/App.tsx`, or can I ignore it?
    (I'd like to split `App.tsx` into screens when the member-facing views grow, in Phase 4–5.)
12. **Uncommitted work.** `app.py` has staged and unstaged changes (the quick-replies USSD change). Should I commit it before
    Phase 0 starts, since Phase 0 moves that code?
