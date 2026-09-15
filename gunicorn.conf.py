"""Cloud Run provides PORT; access logs deliberately exclude URLs and tokens."""
import os

port = os.environ.get("PORT", "8080")
if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
    raise ValueError("PORT must be an integer between 1 and 65535")
bind = "0.0.0.0:" + port
workers = 1
worker_class = "gthread"
threads = 4
timeout = 60
graceful_timeout = 8
accesslog = None
errorlog = "-"
loglevel = "warning"
# Do not trust client-supplied forwarded identity or scheme headers.
forwarded_allow_ips = ""
