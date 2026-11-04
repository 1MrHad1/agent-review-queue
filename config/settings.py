"""Settings kept deliberately small. Anything environment-specific reads from
the environment with a local default, so the same code runs on a laptop and in
CI without a branch."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"] if DEBUG else os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    # Needed for AddIndexConcurrently / AddConstraintNotValid in migration 0004-0005.
    "django.contrib.postgres",
    "rest_framework",
    "reviews",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "DIRS": []}]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "reviewqueue"),
        "USER": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "HOST": os.environ.get("PGHOST", "localhost"),
        # Local Homebrew Postgres on this machine listens on 5433; CI uses 5432.
        "PORT": os.environ.get("PGPORT", "5433"),
        # Tests spawn real threads to prove the locking works, and each thread
        # needs its own connection, so connections are not pooled across them.
        "CONN_MAX_AGE": 0,
        "TEST": {"NAME": "test_reviewqueue"},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
