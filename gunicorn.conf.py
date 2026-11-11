# Read by gunicorn automatically (Render's start command sets the workers and port).
# Threads: a slow request (e.g. the database waking up) no longer blocks every other USSD screen.
worker_class = "gthread"
threads = 4  # = the database pool's max_size in app.py
timeout = 30  # restart a worker stuck this long
