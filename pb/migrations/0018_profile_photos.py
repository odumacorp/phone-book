"""Profile photos: one small square picture per account (the app resizes it before upload), kept in the database
so it survives redeploys. data is base64; mime is image/jpeg, image/png or image/webp."""


def up(db, m):
    db.execute(
        """CREATE TABLE IF NOT EXISTS user_photos (
               phone TEXT PRIMARY KEY,
               mime TEXT NOT NULL,
               data TEXT NOT NULL,
               updated_at INTEGER NOT NULL
           )"""
    )
