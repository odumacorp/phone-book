# Phone Book USSD (*384*88041#)

A USSD phone book built on Africa's Talking. Each phone number has its own contact list,
reachable from any phone after logging in.

Every session starts with a login: phone number (`+254XXXXXXXXX` or `0XXXXXXXXX`, e.g. 07… or 01…) -> 4-digit PIN -> main menu.
Numbers are registered in the mobile app (SMS code + PIN); an unregistered number dialling in is told to register there.
A wrong PIN gets one more try in the same session ("Wrong PIN. You have 1 more try"); a second wrong PIN
ends the session with how to reset it: in the Remote Phonebook app or at phone-book.odumacorp.com, log in and
tap Change USSD PIN. Wrong PINs count toward the lockout (5, then 15 minutes); a PIN already rejected in a
session isn't counted again when later screens resend it.
PINs are changed or reset only in the mobile app (Change PIN / Forgot PIN), never over USSD.

Typing a name on the main menu searches straight away.

The network ends each USSD session after a few minutes (sooner if a screen sits idle); that limit
can't be changed. A *login*, though, stays active for **40 seconds of inactivity** between screens:
redialling from your own phone within 40 seconds shows "Welcome back: 1. Continue where you left off,
2. Main menu, 0. Log out"; choosing 1 or 2 asks for the PIN first. A borrowed phone asks for the PIN straight away. After 40 seconds
without activity the user is logged out (the saved login is deleted and the next dial says so); a PIN
change in the app or a lockout also ends it.
A screen that just saved/sent/deleted continues at the main menu instead, so nothing runs twice.

After login, every screen offers `0. Back`, `00. Home` and `000. Log out` (000 ends the login at once), and results (saved, sent, deleted)
keep the session open instead of ending it. Actions only run once: input typed after a result
is rejected rather than repeating the save/send/delete.

```
1. My contacts      -> 4 per screen, 98 = next page (numbers continue: 5-8, 9-12...) -> pick a contact
2. Emergency numbers -> 999, 112, 911, 1199 (Red Cross), 1195 (GBV), 116 (Childline): 1. Call now ends the
                       session with "Dial 999 now" (calls are always made from the user's own phone), 2. Add to contacts
3. Add contact      -> name -> phone number
4. Search contact   -> part of a name -> pick a contact
5. Delete contact   -> pick number -> 1 Yes / 2 No
6. SMS me my contacts (sent to the phone in use, with the Africa's Talking SMS API)
```

On the search and list screens, reply with the contact's position (1-4) or type any phone number:

```
1. Call             -> AT rings the caller, then connects them to the contact (needs AT_VOICE_NUMBER; until
                       then it says calling isn't available and offers 2 and 3)
2. Send SMS         -> 1. Please call me, 2. Type a message (or just type it); no `*`. Every SMS ends with
                       "- <name> (07...)" using the name set in the app (just the number if none)
3. Copy number      -> shows the number on screen and texts it to the caller (USSD cannot reach the clipboard)
```

For calls, buy a voice number in Africa's Talking (Voice -> Phone Numbers), set it as `AT_VOICE_NUMBER`,
and set that number's callback URL to `https://<your-public-host>/voice`.

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your AT username + API key
python app.py               # http://localhost:8000
ngrok http 8000             # gives you a public https URL
```

## Code layout and tests

`app.py` is only the entry point (`gunicorn app:app`); the code is in `pb/`:
`config` (env + constants), `web` (the Flask app), `db` / `schema` (database), `ussd/` (session, screens,
personal menu, routes), `api/` (app endpoints), `notify` (email, templates, notifications), `admin`, `website`.
Plans for packages and enterprise editions: `docs/packages-plan.md`.

```bash
pip install -r requirements-dev.txt
pytest                                                    # SQLite
TEST_DATABASE_URL=postgresql://user@host/throwaway pytest # Postgres (empties every table!)
```

## Connect to Africa's Talking

1. Dashboard -> USSD -> Service Codes -> Create Channel, channel `88041` on `*384#`
   (in the sandbox: Sandbox app -> USSD -> Create Channel).
2. Set the callback URL to `https://<your-public-host>/ussd`.
3. Test with the simulator at https://simulator.africastalking.com (sandbox), or dial `*384*88041#` on a real phone (live).

## Production

```bash
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

Set `DATABASE_URL` to a Postgres connection string (e.g. Neon) in production. Without it,
contacts are stored in SQLite (`phonebook.db`, or `DB_PATH`). `SECRET_KEY` must be set for app logins.

## Admin page (`/admin`)

Set `ADMIN_PASSWORD` (and `SECRET_KEY`) on the server, then open `/admin` (e.g.
https://phone-book.odumacorp.com/admin). Without `ADMIN_PASSWORD` the page doesn't exist (404).
It's for the developer/admin only: one password, a signed HttpOnly SameSite=Strict cookie (8 hours),
CSRF tokens on every form, and 5 wrong passwords lock that address out for 15 minutes.

- **Overview:** accounts, contacts, encrypted contacts, verified emails, open reports.
- **Feature switches** (all **off** by default, so the prototype demo behaves exactly as before):
  - **Encrypt contacts at rest:** see below.
  - **Email verification:** users confirm their email with a 6-digit code (app: *Verify your email*).
    While on, an email can only be used to log in once verified; phone-number login always works.
  - **Email notifications:** security alerts by email (new app login, password change/reset, PIN change,
    USSD login from another phone, a USSD PIN lockout). They always appear in the app's Notifications.
  - **Send a test email** to check the SMTP settings.
- **Message users:** a subject and message to every account (`all`) or one phone number. It goes to each
  user's in-app Notifications and the emails they chose (whenever email is configured).
- **Sent notifications and replies** (`/admin/notifications`): everything sent to users in the last 90 days:
  security alerts, messages from the team and replies to reports (with the report they answer). For each: the
  account, whether it reached the in-app list and whether the user has read it (or cleared it), which emails it
  went to and whether they were sent or failed (with the error), or why it wasn't delivered (e.g. the user turned
  that kind off). Filter by type, phone number or email.
- **Emails and notifications** (`/admin/templates`): edit every message users get: new login, password
  changed/reset, PIN changed, USSD login from another phone, USSD lockout, reply to a report, and the email
  verification code. For each: turn it on/off for everyone, edit the subject and text with placeholders
  (`{name}`, `{phone}`, `{device}`, `{caller}`, `{report}`, `{reply}`, `{code}`...), preview it with a sample
  user, send a test, or reset it to the default. Also the sender name, the footer on notification emails and
  the signature on every email. Stored in the settings table; unknown `{words}` are left as typed.
- **Reports and complaints:** users send them from the app (*Report a problem*, also on the login screen
  for people who can't log in, with a phone/email to reply to). The admin replies, resolves or reopens;
  the reply goes to the user's Notifications and their chosen emails (or, for a report sent without
  logging in, to the email they left). 5 reports per hour
  per user/address.

The email switches can't be turned on until email is configured: `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS
or 465 TLS), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` (e.g. Gmail with an app password, Brevo, Mailgun).
Emails are sent in the background, so USSD screens never wait on the mail server.

### Encrypting contacts at rest (optional)

When the admin turns it on, every contact's name and phone number is stored as **AES-256-GCM**
ciphertext (a random nonce per value, authenticated so it can't be altered or moved between fields),
and all existing contacts are encrypted. Turning it off decrypts them again. The key is
`CONTACTS_KEY`, kept only in the server's environment (Render env vars / local `.env`), never in the
database, so a copy of the database or a backup shows only ciphertext. Contacts are decrypted on the
server only when the owner's USSD screen or app request needs them; search and sorting happen there.

- Generate a key: `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`
- The switch is disabled until `CONTACTS_KEY` is set. Every server using the same database needs the same key.
- While any contact is encrypted, the server won't start without the matching key.
- **Keep a copy of the key somewhere safe**: without it, encrypted contacts can't be recovered.
- Only contacts are encrypted (not account names, emails or login history), and the database provider's
  restore history may still hold older plaintext copies for its retention period.

## Website: https://phone-book.odumacorp.com/

The same Remote Phonebook app, built for the web (Expo web) and served by this server at `/`
(health check moved to `/health`). It fits any screen from 320 px phones up, centred on tablets
and computers, and does everything the phone app does. Differences in a browser:
- **Choose contacts from phone:** Android Chrome opens the phonebook (Contact Picker); other browsers
  import a `.vcf` contacts file exported from the phone's Contacts app.
- The login is kept in the browser's local storage; Sessions show the device as e.g. "Chrome on Android (website)".

Rebuild after changing the app: `cd mobile && npm run build:web` (writes `../web/`), then commit `web/`.

## Mobile app (`mobile/`): Remote Phonebook

An Expo (React Native) app for managing the phone book that USSD uses.

- **Register:** name, phone number (confirmed with a 6-digit SMS code), optional email, a password of the
  user's choosing (8+ characters) and a 4-digit USSD PIN.
- **Log in:** email *or* phone number + password. USSD keeps phone number + PIN (letters are slow on a keypad).
- **Forgot password:** email or phone -> code by SMS to the account's phone -> new password.
- **Change password** (other devices are logged out) and **Change USSD PIN** (needs the password, so a
  forgotten PIN can simply be replaced).
- Wrong passwords lock the app login for 15 minutes after 5 tries, separately from the USSD PIN lockout.
- **Notifications:** an in-app list (kept 90 days; unread count on the home screen) of security alerts,
  replies to reports and messages from the team. Users choose what to get (security alerts, messages from
  the team), where (in the app, each of their emails), or *Stop all notifications*. While the admin has
  email verification on, only verified emails get them.
- **Emails:** up to 3 per account (*Add email*, *Remove*), each usable to log in; an email can be on only one
  account. When the admin turns on email verification, each one is confirmed with a code (*Verify*).
- **Log out** (top bar): this device, or everywhere (all app logins and any USSD session).
- **Sessions:** each app device and USSD login with login time, logout time (and why), duration, and its own Log out.
- **USSD logins:** shows the active USSD session with a "Log out USSD session" button, and lists every USSD login attempt on the account: the phone that dialled
  (the user's own or another), its network, the time, and whether it succeeded.
- **Emergency numbers:** a Call button opens the phone's dialler; Add puts the number in the contacts.
  Every contact also has a Call button. Contacts can be short service numbers (999, 1199...): on USSD
  they show Call (dial it yourself) and Copy number, never SMS.
- **Contacts:** choose which phone contacts to add (checkboxes, search, select all; already-added ones are
  marked), add one by hand, sync all at once, or tap a contact to remove it.
- The app lists the phone book; users star up to 5 priority contacts (shown first everywhere)
  and choose whether their other contacts appear on USSD.

```bash
cd mobile && npm install
npx expo start                                   # run in Expo Go / emulator
npx eas-cli build -p android --profile preview   # installable APK (needs a free expo.dev account)
```

API (JSON):
- `POST /api/register/request` `{phone, email?}` -> texts a 6-digit code (valid 10 min, one per minute)
- `POST /api/register/confirm` `{phone, code, name, email?, password, confirm_password?, pin, confirm_pin?}` -> `{token}`
- `POST /api/login` `{login, password}` (login = email or phone) -> `{token}`
- `POST /api/password/reset/request` `{login}` -> texts a code to the account's phone -> `{sent, phone (masked)}`
- `POST /api/password/reset/confirm` `{login, code, new_password, confirm_password?}` -> `{token}`; clears the password lockout
- `POST /api/password/change` `{current_password, new_password, confirm_password?}` -> `{token}`; other sessions logged out
- `POST /api/pin/change` `{password, new_pin, confirm_pin?}` -> `{token}`; the USSD PIN
- `GET /api/me` -> `{phone, contacts, name, email, show_others}`
- `GET /api/sessions` -> `{app: [...], ussd: [...], ussd_timeout_seconds, history_days}`: every app device and USSD
  login with `logged_in`, `logged_out` (null while active), `duration_seconds`, `active`, `end_reason`; active first,
  ended ones kept 30 days. The app sends its model as `X-Device-Name`
- `POST /api/sessions/app/<id>/logout`, `POST /api/sessions/ussd/<id>/logout` -> log out one session
- `GET /api/ussd/sessions` -> `{sessions: [{phone, own_phone, last_active, expires_at}], timeout_seconds}`
- `POST /api/ussd/logout` -> ends the account's USSD sessions: an open one stops on its next screen
  ("You were logged out from the Remote Phonebook app") and the next dial needs a full login
- `POST /api/logout` `{everywhere?}` -> revokes this device's token, or (everywhere) every app login + USSD
- `GET /api/logins?limit=` -> `{logins: [{at, phone, own_phone, network, success, reason}], keep_days}`: USSD login
  attempts on this account (one per session; reason ok / wrong_pin / locked), newest first, kept 90 days
- `GET /api/contacts` -> `{contacts: [{id, name, phone, priority}], max_priority, show_others}` (priority first)
- `GET /api/emergency` -> `{numbers: [{number, label, saved}]}`
- `POST /api/contacts` `{name, phone}` (phone may be a 3-6 digit service number) -> the new contact (409 if the number is already saved)
- `PATCH /api/contacts/<id>` `{name, phone}` -> the edited contact (409 if another contact has that number)
- `DELETE /api/contacts/<id>`
- `POST /api/contacts/delete` `{ids: [...]}` -> `{deleted: [...]}`; bulk delete (up to 500), ids that are not yours are ignored
- `POST /api/contacts/<id>/priority` `{priority: true|false}`; at most 5 priority contacts
- `POST /api/settings` `{show_others?, name?}`; name signs the user's SMS (emails: `/api/emails`);
  when show_others is false, USSD lists/search/delete/SMS-me show only priority contacts
- `POST /api/contacts/sync` `{contacts: [{name, phone}]}` -> `{added, skipped, total}`; numbers already saved are skipped
- `GET /api/emails` -> `{emails: [{id, email, verified}], max_emails}`; up to 3 emails per account, each one a login
- `POST /api/emails` `{email}` -> the new email (409 if it's on any account, or the account already has 3)
- `DELETE /api/emails/<id>` -> removes it (it stops working as a login and gets no more alerts)
- `POST /api/email/verify/request` `{id}` -> emails a 6-digit code to that address (only while the admin has
  email verification on)
- `POST /api/email/verify/confirm` `{id, code}` -> `{id, email, verified: true}`; `GET /api/me` also returns
  `emails`, `max_emails` and `features.email_verification`
- `GET /api/notifications` -> `{notifications: [{id, kind, title, body, created_at, read}], unread, prefs, keep_days}`;
  `prefs` = `{inapp, security, messages, emails: [{id, email, verified, notify}], email_available, verified_only}`
- `POST /api/notifications/read` (all read), `POST /api/notifications/clear` (empty the list)
- `POST /api/notifications/prefs` `{inapp?, security?, messages?}`, `{email_id, notify}` or `{all: false|true}` -> `prefs`
- `POST /api/complaints` `{message, contact?}` -> a report to the admin (token optional; without one, `contact` is required)
- `GET /api/complaints` -> `{complaints: [{id, message, status, reply, created_at, updated_at}]}`: the user's own reports

Send the token as `Authorization: Bearer <token>`. Codes stop working after 5 wrong tries.
