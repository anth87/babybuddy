from .base import *  # noqa

# Baby Buddy settings for deployment on Railway.
#
# SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS and DEBUG are read from
# environment variables (see base.py). Set DJANGO_SETTINGS_MODULE to
# "babybuddy.settings.railway" in the Railway service variables to use this file.
#
# The SQLite database and uploaded media are kept under DATA_DIR, which should be
# a persistent Railway volume (mount path /app/data) so they survive redeploys.

DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")  # noqa: F405

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(DATA_DIR, "db.sqlite3"),
    }
}

MEDIA_ROOT = os.path.join(DATA_DIR, "media")
