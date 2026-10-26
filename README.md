# Phone Book USSD (*384*88041#)

A USSD phone book built on Africa's Talking. Each phone number has its own contact list,
reachable from any phone after logging in.

Every session starts with a login: phone number (`+254XXXXXXXXX` or `0XXXXXXXXX`, e.g. 07… or 01…) -> 4-digit PIN -> main menu.
Numbers are registered in the mobile app (SMS code + PIN); an unregistered number dialling in is told to register there.
Wrong PINs count toward the same lockout as the app (5 tries, then 15 minutes).
PINs are changed or reset only in the mobile app (Change PIN / Forgot PIN), never over USSD.

Typing a name on the main menu searches straight away.

The network ends each USSD session after a few minutes (sooner if a screen sits idle); that limit
can't be changed. A *login*, though, stays active for **1 minute of inactivity**:
redialling from your own phone within 1 minute shows "Welcome back: 1. Continue where you left off,
2. Main menu, 0. Log out" with no PIN. A borrowed phone always asks for the PIN again. After 1 minute
without activity the user is logged out (the saved login is deleted and the next dial says so); a PIN
change in the app or a lockout also ends it.
A screen that just saved/sent/deleted continues at the main menu instead, so nothing runs twice.
Continues without a PIN appear in the app's login history as "Continued".

After login, every screen offers `0. Back`, `00. Home` and `000. Log out` (000 ends the login at once), and results (saved, sent, deleted)
keep the session open instead of ending it. Actions only run once: input typed after a result
is rejected rather than repeating the save/send/delete.

```
1. Add contact      -> name -> phone number
2. Search contact   -> part of a name -> pick a contact
3. My contacts      -> 4 per screen, 98 = next page (numbers continue: 5-8, 9-12...) -> pick a contact
4. Delete contact   -> pick number -> 1 Yes / 2 No
5. SMS me my contacts (sent to the phone in use, with the Africa's Talking SMS API)
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

## Mobile app (`mobile/`): Remote Phonebook

An Expo (React Native) app for managing the phone book that USSD uses.

- **Register:** name, phone number (confirmed with a 6-digit SMS code), optional email, a password of the
  user's choosing (8+ characters) and a 4-digit USSD PIN.
- **Log in:** email *or* phone number + password. USSD keeps phone number + PIN (letters are slow on a keypad).
- **Forgot password:** email or phone -> code by SMS to the account's phone -> new password.
- **Change password** (other devices are logged out) and **Change USSD PIN** (needs the password, so a
  forgotten PIN can simply be replaced).
- Wrong passwords lock the app login for 15 minutes after 5 tries, separately from the USSD PIN lockout.
- Emails aren't verified (the server can't send email yet); they are only a login name and must be unique.
- **Log out** (top bar): this device, or everywhere (all app logins and any USSD session).
- **Sessions:** each app device and USSD login with login time, logout time (and why), duration, and its own Log out.
- **USSD logins:** shows the active USSD session with a "Log out USSD session" button, and lists every USSD login attempt on the account: the phone that dialled
  (the user's own or another), its network, the time, and whether it succeeded.
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
- `POST /api/contacts` `{name, phone}` -> the new contact (409 if the number is already saved)
- `DELETE /api/contacts/<id>`
- `POST /api/contacts/<id>/priority` `{priority: true|false}`; at most 5 priority contacts
- `POST /api/settings` `{show_others?, name?, email?}`; name signs the user's SMS; email ("" removes it) is a login;
  when show_others is false, USSD lists/search/delete/SMS-me show only priority contacts
- `POST /api/contacts/sync` `{contacts: [{name, phone}]}` -> `{added, skipped, total}`; numbers already saved are skipped

Send the token as `Authorization: Bearer <token>`. Codes stop working after 5 wrong tries.
