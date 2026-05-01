"""
Test-only settings — uses SQLite so tests never touch Neon Postgres
and teardown always succeeds cleanly.

Usage:
    .venv/bin/python manage.py test --settings=core.test_settings <labels>
"""

from core.settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR + "/test_db.sqlite3"
        if isinstance(BASE_DIR, str)
        else str(BASE_DIR / "test_db.sqlite3"),
    }
}

# Silence Cloudinary — not relevant for unit tests
DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"
