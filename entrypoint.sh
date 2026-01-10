#!/bin/sh
set -e; echo "Making migrations..."; python manage.py makemigrations; echo "Applying migrations..."; python manage.py migrate; echo "Starting server..."; exec gunicorn core.wsgi:application --bind 0.0.0.0:8080 --workers 4 --access-logfile - --error-logfile - --log-level info
