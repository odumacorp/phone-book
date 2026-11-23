import time

from pb.config import DATABASE_URL, DATABASE_URL_UNPOOLED
from pb.crypto import check_contacts_key
from pb.db import add_column, connect, sql


def init_db():
    id_col = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = [
        f"""CREATE TABLE IF NOT EXISTS contacts (
               id {id_col},
               owner TEXT NOT NULL,
               name TEXT NOT NULL,
               phone TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )""",
        "CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner)",
        """CREATE TABLE IF NOT EXISTS users (
               phone TEXT PRIMARY KEY,
               pin_hash TEXT NOT NULL,
               failed_attempts INTEGER NOT NULL DEFAULT 0,
               locked_until INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS pin_resets (
               phone TEXT PRIMARY KEY,
               code_hash TEXT NOT NULL,
               expires_at INTEGER NOT NULL,
               sent_at INTEGER NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS ussd_resume (
               caller TEXT PRIMARY KEY,
               owner TEXT NOT NULL,
               path TEXT NOT NULL,
               saved_at INTEGER NOT NULL,
               session_id TEXT NOT NULL,
               offered_in TEXT,
               resume_owner TEXT,
               resume_from TEXT
           )""",
        f"""CREATE TABLE IF NOT EXISTS login_logs (
               id {id_col},
               owner TEXT NOT NULL,
               caller TEXT NOT NULL,
               network TEXT,
               session_id TEXT,
               success INTEGER NOT NULL,
               reason TEXT,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_login_logs_owner ON login_logs(owner, created_at)",
        """CREATE TABLE IF NOT EXISTS app_sessions (
               jti TEXT PRIMARY KEY,
               owner TEXT NOT NULL,
               device TEXT,
               version TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               last_seen INTEGER NOT NULL,
               ended_at INTEGER,
               end_reason TEXT
           )""",
        f"""CREATE TABLE IF NOT EXISTS ussd_sessions (
               id {id_col},
               owner TEXT NOT NULL,
               caller TEXT NOT NULL,
               network TEXT,
               started_at INTEGER NOT NULL,
               last_active INTEGER NOT NULL,
               ended_at INTEGER,
               end_reason TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_ussd_sessions_owner ON ussd_sessions(owner, started_at)",
        "CREATE INDEX IF NOT EXISTS idx_app_sessions_owner ON app_sessions(owner)",
        """CREATE TABLE IF NOT EXISTS revoked_tokens (
               jti TEXT PRIMARY KEY,
               expires_at INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS ussd_wrong_pins (
               session_id TEXT NOT NULL,
               mac TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               PRIMARY KEY (session_id, mac)
           )""",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        f"""CREATE TABLE IF NOT EXISTS complaints (
               id {id_col},
               owner TEXT,
               contact TEXT,
               message TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'open',
               reply TEXT,
               ip TEXT,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status, created_at)",
        f"""CREATE TABLE IF NOT EXISTS user_emails (
               id {id_col},
               owner TEXT NOT NULL,
               email TEXT NOT NULL UNIQUE,
               verified INTEGER NOT NULL DEFAULT 0,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_user_emails_owner ON user_emails(owner)",
        f"""CREATE TABLE IF NOT EXISTS notifications (
               id {id_col},
               owner TEXT NOT NULL,
               kind TEXT NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               read_at INTEGER
           )""",
        "CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications(owner, created_at)",
        # Everything sent to users, for the admin: what, to whom, and how it was delivered
        f"""CREATE TABLE IF NOT EXISTS notification_log (
               id {id_col},
               owner TEXT,
               recipient TEXT,
               kind TEXT NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               inapp_id INTEGER,
               emails TEXT,
               email_status TEXT NOT NULL DEFAULT 'none',
               note TEXT,
               complaint_id INTEGER,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_notification_log_time ON notification_log(created_at)",
        """CREATE TABLE IF NOT EXISTS email_codes (
               phone TEXT PRIMARY KEY,
               email TEXT NOT NULL,
               code_hash TEXT NOT NULL,
               expires_at INTEGER NOT NULL,
               sent_at INTEGER NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS pending_calls (
               user_phone TEXT PRIMARY KEY,
               contact_phone TEXT NOT NULL,
               created_at INTEGER NOT NULL
           )""",
    ]
    db = connect(DATABASE_URL_UNPOOLED)
    try:
        if DATABASE_URL:
            # gunicorn workers start together; concurrent CREATE TABLE IF NOT EXISTS can collide
            db.execute("SELECT pg_advisory_xact_lock(88041)")
        for statement in statements:
            db.execute(statement)
        # Columns added after launch; existing databases get them on the next start
        add_column(db, "contacts", "priority", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "show_others", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "name", "TEXT")
        # App accounts: email (optional) or phone + a password of the user's choosing
        add_column(db, "users", "email", "TEXT")
        add_column(db, "users", "password_hash", "TEXT")
        add_column(db, "users", "pw_failed_attempts", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "pw_locked_until", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "token_epoch", "INTEGER NOT NULL DEFAULT 0")  # bumped by "log out everywhere"
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        add_column(db, "ussd_resume", "pin_mac", "TEXT")
        add_column(db, "ussd_resume", "logged_out", "INTEGER NOT NULL DEFAULT 0")  # ended from the app
        add_column(db, "ussd_resume", "login_id", "INTEGER")  # the ussd_sessions row of this login
        add_column(db, "contacts", "enc", "INTEGER NOT NULL DEFAULT 0")  # 1 = name/phone are ciphertext
        add_column(db, "users", "email_verified", "INTEGER NOT NULL DEFAULT 0")
        # Notification choices: the in-app inbox, and which kinds (security alerts, messages from the team)
        add_column(db, "users", "notify_inapp", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "notify_security", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "notify_messages", "INTEGER NOT NULL DEFAULT 1")
        # What USSD "SMS me my contacts" sends: 1 all contacts, 0 only priority ones.
        # NULL (never chosen in the app) follows show_others, as before this setting existed.
        add_column(db, "users", "sms_all", "INTEGER")
        add_column(db, "user_emails", "notify", "INTEGER NOT NULL DEFAULT 1")  # this email gets notifications
        # Accounts can have up to MAX_EMAILS emails (user_emails); move the old single users.email there
        db.execute(
            sql(
                "INSERT INTO user_emails (owner, email, verified, created_at) SELECT phone, email, email_verified, ? "
                "FROM users u WHERE email IS NOT NULL AND NOT EXISTS (SELECT 1 FROM user_emails e WHERE e.email = u.email)"
            ),
            (int(time.time()),),
        )
        db.execute("UPDATE users SET email = NULL, email_verified = 0 WHERE email IS NOT NULL")
        check_contacts_key(db)
        db.commit()
    finally:
        db.close()
