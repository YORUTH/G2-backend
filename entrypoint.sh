#!/bin/bash
set -e

# Start the Telegram bot in the background
echo "Starting Telegram bot..."
python manage.py run_telegram_bot &

# Start the web server (foreground — keeps the container alive)
echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:${PORT:-8001} --workers 3 core.wsgi:application
