"""When each account was created (for the statistics dashboards). Existing accounts get their first login (app or USSD),
the best record there is; new accounts get the time they register."""


def up(db, m):
    m.add_column("users", "created_at", "INTEGER")
    db.execute(
        """UPDATE users SET created_at = (
               SELECT MIN(t) FROM (
                   SELECT MIN(created_at) AS t FROM app_sessions WHERE app_sessions.owner = users.phone
                   UNION ALL SELECT MIN(created_at) AS t FROM login_logs WHERE login_logs.owner = users.phone
               ) AS firsts
           ) WHERE created_at IS NULL"""
    )
