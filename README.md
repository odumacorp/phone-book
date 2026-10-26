# Phone Book USSD (*384*88041#)

A USSD phone book built on Africa's Talking. Each phone number has its own contact list,
reachable from any phone after logging in.

Every session starts with a login: phone number (`+254XXXXXXXXX` or `07XXXXXXXX`) -> 4-digit PIN -> main menu.
The first time, a number has no PIN; dialling from that same phone creates one (once only).
Wrong PINs count toward the same lockout as the app (5 tries, then 15 minutes).
Forgot PIN: choose `99` on the PIN screen (only works when dialling from that number), or reset it in the app.

After login, every screen offers `0. Back` and `00. Home`, and results (saved, sent, deleted)
keep the session open instead of ending it. Actions only run once: input typed after a result
is rejected rather than repeating the save/send/delete.

```
1. Add contact      -> name -> phone number
2. Search contact   -> part of a name -> pick a contact
3. My contacts      -> 4 per screen, 9 = more -> pick a contact
4. Delete contact   -> pick number -> 1 Yes / 2 No
5. SMS me my contacts (sent to the phone in use, with the Africa's Talking SMS API)
6. Change PIN       -> new PIN -> confirm (ends the session)
```

On the search and list screens, reply with the contact's position (1-4) or type any phone number:

```
1. Call             -> AT rings the caller, then connects them to the contact (needs AT_VOICE_NUMBER)
2. Send SMS         -> type a message (no `*`); sent to the contact as "<message> - from <caller>"
3. Copy number      -> the number is texted to the caller, who can copy, call or save it
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

## Mobile app (`mobile/`)

An Expo (React Native) Android app that uploads the phone's contacts to the user's phone book.
Users log in with the same phone number and PIN they use on USSD.

```bash
cd mobile && npm install
npx expo start                                   # run in Expo Go / emulator
npx eas-cli build -p android --profile preview   # installable APK (needs a free expo.dev account)
```

API (JSON):
- `POST /api/login` `{phone, pin}` -> `{token}`; 5 wrong PINs lock the number for 15 minutes
- `GET /api/me` -> `{phone, contacts}`
- `POST /api/pin/change` `{current_pin, new_pin, confirm_pin}` -> `{token}`; other sessions are logged out
- `POST /api/pin/reset/request` `{phone}` -> texts a 6-digit code (valid 10 min, one per minute)
- `POST /api/pin/reset/confirm` `{phone, code, new_pin, confirm_pin}` -> `{token}`; 5 wrong codes burn it; clears any lockout
- `POST /api/contacts/sync` `{contacts: [{name, phone}]}` -> `{added, skipped, total}`; numbers already saved are skipped

Send the token as `Authorization: Bearer <token>`.
