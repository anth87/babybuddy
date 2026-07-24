#!/bin/bash

python manage.py migrate

# Create the initial superuser from environment variables on first boot.
# Uses DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD.
# Harmlessly no-ops on later boots once the user exists.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py createsuperuser --noinput || true
fi

gunicorn babybuddy.wsgi:application --bind 0.0.0.0:${PORT:-8000} --timeout 30 --log-file -