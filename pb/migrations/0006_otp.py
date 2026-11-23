"""One-time codes by SMS (second factor). Built, but off until the Super Admin turns OTP on."""


def up(db, m):
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS otp_codes (
               id {m.id_col},
               phone TEXT NOT NULL,
               purpose TEXT NOT NULL,
               code_hash TEXT NOT NULL,
               ticket TEXT NOT NULL UNIQUE,
               expires_at INTEGER NOT NULL,
               sent_at INTEGER NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0,
               used_at INTEGER
           )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_otp_codes_phone ON otp_codes(phone, purpose, sent_at)")
