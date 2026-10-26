# Phone Book USSD (*384*88041#)

A USSD phone book built on Africa's Talking. Each caller gets their own contact list.

```
1. Add contact      -> name -> phone number
2. Search contact   -> part of a name
3. My contacts      -> 5 per screen, 9 = more
4. Delete contact   -> pick number -> 1 Yes / 2 No
5. SMS me my contacts (sent with the Africa's Talking SMS API)
```

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

Contacts are stored in SQLite (`phonebook.db`); set `DB_PATH` to change where.
