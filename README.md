# Phone Book USSD (*384*88041#)

A USSD phone book built on Africa's Talking. Each caller gets their own contact list.

```
1. Add contact      -> name -> phone number
2. Search contact   -> part of a name -> pick a contact
3. My contacts      -> 4 per screen, 9 = more -> pick a contact
4. Delete contact   -> pick number -> 1 Yes / 2 No
5. SMS me my contacts (sent with the Africa's Talking SMS API)
6. Set app PIN      -> 4 digits -> confirm
```

On the search and list screens, reply with the contact's position (1-4) or type any phone number:

```
1. Call             -> AT rings the caller, then connects them to the contact (needs AT_VOICE_NUMBER)
2. Send SMS         -> type a message; sent to the contact as "<message> - from <caller>"
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
Users log in with their phone number and the PIN set via USSD option 6.

```bash
cd mobile && npm install
npx expo start                                   # run in Expo Go / emulator
npx eas-cli build -p android --profile preview   # installable APK (needs a free expo.dev account)
```

API (JSON):
- `POST /api/login` `{phone, pin}` -> `{token}`; 5 wrong PINs lock the number for 15 minutes
- `GET /api/me` -> `{phone, contacts}`
- `POST /api/contacts/sync` `{contacts: [{name, phone}]}` -> `{added, skipped, total}`; numbers already saved are skipped

Send the token as `Authorization: Bearer <token>`. Resetting the PIN logs out existing app sessions.
