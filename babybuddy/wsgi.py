from django.core.wsgi import get_wsgi_application

from dotenv import load_dotenv, find_dotenv

# Environment variables
# Check for and load environment variables from a .env file.
load_dotenv(find_dotenv())

application = get_wsgi_application()

# Serve uploaded media (child photos) from the app process when no separate
# web server or cloud storage handles it (e.g. gunicorn-only deploys such as
# Railway). Enabled via SERVE_MEDIA_FROM_APP in the active settings module.
# autorefresh is required so files uploaded after boot are picked up.
from django.conf import settings  # noqa: E402

if getattr(settings, "SERVE_MEDIA_FROM_APP", False):
    from whitenoise import WhiteNoise

    application = WhiteNoise(
        application,
        root=settings.MEDIA_ROOT,
        prefix=settings.MEDIA_URL,
        autorefresh=True,
    )
