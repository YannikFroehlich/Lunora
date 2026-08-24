"""
Django settings for the Lunora project.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(env_path):
    """Load local key=value secrets before Django reads the settings."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_value = value.strip().strip('"').strip("'")
        os.environ.setdefault(clean_key, clean_value)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name, default=0):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} muss eine ganze Zahl sein.") from error


def env_admins(name, default=""):
    """Parse "Name:email,Name:email" (or plain emails) into Django's ADMINS format."""
    admins = []
    for entry in env_list(name, default):
        admin_name, _sep, email = entry.partition(":")
        admins.append((admin_name.strip(), email.strip() or admin_name.strip()))
    return admins


def database_config(debug):
    """Build the database configuration for local and production environments."""
    engine = os.getenv("DJANGO_DATABASE_ENGINE", "sqlite").strip().lower()

    if engine in {"sqlite", "sqlite3"}:
        if not debug:
            raise ImproperlyConfigured(
                "DJANGO_DATABASE_ENGINE muss bei DJANGO_DEBUG=false auf postgresql gesetzt sein."
            )

        sqlite_path = os.getenv("DJANGO_SQLITE_PATH", "db.sqlite3").strip()
        if not sqlite_path:
            raise ImproperlyConfigured("DJANGO_SQLITE_PATH darf nicht leer sein.")

        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / sqlite_path,
            }
        }

    if engine in {"postgres", "postgresql"}:
        required_values = {
            "DJANGO_DB_NAME": os.getenv("DJANGO_DB_NAME", "").strip(),
            "DJANGO_DB_USER": os.getenv("DJANGO_DB_USER", "").strip(),
        }
        if not debug:
            required_values["DJANGO_DB_PASSWORD"] = os.getenv(
                "DJANGO_DB_PASSWORD", ""
            ).strip()
        missing = [
            name
            for name, value in required_values.items()
            if not value or (not debug and value == "change-me")
        ]
        if missing:
            raise ImproperlyConfigured(
                f"{', '.join(missing)} muss für PostgreSQL gesetzt sein."
            )

        options = {
            "connect_timeout": env_int("DJANGO_DB_CONNECT_TIMEOUT", 10),
        }
        ssl_mode = os.getenv("DJANGO_DB_SSLMODE", "").strip()
        if ssl_mode:
            options["sslmode"] = ssl_mode

        return {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": required_values["DJANGO_DB_NAME"],
                "USER": required_values["DJANGO_DB_USER"],
                "PASSWORD": os.getenv("DJANGO_DB_PASSWORD", ""),
                "HOST": os.getenv("DJANGO_DB_HOST", "127.0.0.1").strip(),
                "PORT": os.getenv("DJANGO_DB_PORT", "5432").strip(),
                "CONN_MAX_AGE": env_int("DJANGO_DB_CONN_MAX_AGE", 60),
                "CONN_HEALTH_CHECKS": True,
                "OPTIONS": options,
            }
        }

    raise ImproperlyConfigured(
        "DJANGO_DATABASE_ENGINE muss sqlite oder postgresql sein."
    )


load_env_file(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY or (not DEBUG and SECRET_KEY == "change-me"):
    if DEBUG:
        SECRET_KEY = "django-insecure-lunora-local-dev-key-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY muss gesetzt sein, wenn DJANGO_DEBUG=false ist.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS muss gesetzt sein, wenn DJANGO_DEBUG=false ist.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "app.middleware.UserTimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "lunora.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "app.context_processors.appearance_settings",
                "app.context_processors.system_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "lunora.wsgi.application"

DATABASES = database_config(DEBUG)

CACHE_URL = os.getenv("DJANGO_CACHE_URL", "").strip()
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "lunora",
            "TIMEOUT": 300,
        }
    }
elif DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "lunora-local",
        }
    }
else:
    raise ImproperlyConfigured(
        "DJANGO_CACHE_URL muss gesetzt sein, wenn DJANGO_DEBUG=false ist."
    )

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = os.getenv("DJANGO_LANGUAGE_CODE", "de-de")
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / os.getenv("DJANGO_STATIC_ROOT", "staticfiles")
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / os.getenv("DJANGO_PRIVATE_MEDIA_ROOT", "private_media")
PROFILE_IMAGE_MAX_BYTES = env_int("PROFILE_IMAGE_MAX_BYTES", 2 * 1024 * 1024)
PROFILE_IMAGE_MAX_WIDTH = env_int("PROFILE_IMAGE_MAX_WIDTH", 4096)
PROFILE_IMAGE_MAX_HEIGHT = env_int("PROFILE_IMAGE_MAX_HEIGHT", 4096)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/home/"
LOGOUT_REDIRECT_URL = "/login/"

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = os.getenv("DJANGO_SECURE_REFERRER_POLICY", "same-origin")
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = env_bool("DJANGO_CSRF_COOKIE_HTTPONLY", False)

EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("DJANGO_EMAIL_PORT", 587)
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("DJANGO_EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("DJANGO_EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "Lunora <noreply@localhost>")

if EMAIL_USE_SSL and EMAIL_USE_TLS:
    raise ImproperlyConfigured(
        "DJANGO_EMAIL_USE_SSL und DJANGO_EMAIL_USE_TLS dürfen nicht gleichzeitig aktiv sein."
    )

if not DEBUG and EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    missing_email_settings = [
        name
        for name, value in {
            "DJANGO_EMAIL_HOST": EMAIL_HOST,
            "DJANGO_EMAIL_HOST_USER": EMAIL_HOST_USER,
            "DJANGO_EMAIL_HOST_PASSWORD": EMAIL_HOST_PASSWORD,
        }.items()
        if not value or value == "change-me"
    ]
    if missing_email_settings:
        raise ImproperlyConfigured(
            f"{', '.join(missing_email_settings)} muss für SMTP in Produktion gesetzt sein."
        )

# Empty by default: harmless while running locally with DEBUG=true. Set DJANGO_ADMINS
# once this is deployed with DEBUG=false and a real e-mail backend, and Django emails
# these addresses automatically on unhandled server errors.
ADMINS = env_admins("DJANGO_ADMINS")
MANAGERS = ADMINS
SERVER_EMAIL = os.getenv("DJANGO_SERVER_EMAIL", DEFAULT_FROM_EMAIL)

CLOUDFLARE_TURNSTILE_REQUIRED = env_bool(
    "CLOUDFLARE_TURNSTILE_REQUIRED", not DEBUG
)
CLOUDFLARE_TURNSTILE_SITE_KEY = os.getenv(
    "CLOUDFLARE_TURNSTILE_SITE_KEY", ""
).strip()
CLOUDFLARE_TURNSTILE_SECRET_KEY = os.getenv(
    "CLOUDFLARE_TURNSTILE_SECRET_KEY", ""
).strip()
CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME = os.getenv(
    "CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME", ""
).strip()
CLOUDFLARE_TURNSTILE_TIMEOUT = env_int("CLOUDFLARE_TURNSTILE_TIMEOUT", 5)

if CLOUDFLARE_TURNSTILE_REQUIRED:
    missing_turnstile_settings = [
        name
        for name, value in {
            "CLOUDFLARE_TURNSTILE_SITE_KEY": CLOUDFLARE_TURNSTILE_SITE_KEY,
            "CLOUDFLARE_TURNSTILE_SECRET_KEY": CLOUDFLARE_TURNSTILE_SECRET_KEY,
            "CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME": CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME,
        }.items()
        if not value or value == "change-me"
    ]
    if missing_turnstile_settings:
        raise ImproperlyConfigured(
            f"{', '.join(missing_turnstile_settings)} muss für die geschützte Registrierung gesetzt sein."
        )

def _skip_disabled_feature_response(record):
    # 503 in this app only ever means "feature disabled" (see disabled_feature_response) -
    # expected, routine traffic, not a server error worth paging an admin about.
    return getattr(record, "status_code", None) != 503


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "skip_disabled_feature_response": {"()": "django.utils.log.CallbackFilter", "callback": _skip_disabled_feature_response},
    },
    "handlers": {
        "console": {
            "level": "WARNING",
            "class": "logging.StreamHandler",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false", "skip_disabled_feature_response"],
            "class": "django.utils.log.AdminEmailHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "mail_admins"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

LUNORA_AUTOMATION_INTERVAL_SECONDS = env_int("LUNORA_AUTOMATION_INTERVAL_SECONDS", 60)
LUNORA_WEEKLY_SUMMARY_HOUR = env_int("LUNORA_WEEKLY_SUMMARY_HOUR", 8)

if env_bool("DJANGO_USE_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", False)

# Weather API settings are kept server-side so browser code never exposes keys.
WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY") or os.getenv("WEATHER_API_KEY", "")
WEATHER_API_BASE_URL = os.getenv(
    "OPENWEATHER_API_BASE_URL",
    "https://api.openweathermap.org/data/2.5",
)
WEATHER_GEO_API_BASE_URL = os.getenv(
    "OPENWEATHER_GEO_API_BASE_URL",
    "https://api.openweathermap.org/geo/1.0",
)
WEATHER_TILE_BASE_URL = os.getenv(
    "OPENWEATHER_TILE_BASE_URL",
    "https://tile.openweathermap.org/map",
)
WEATHER_DEFAULT_CITY = os.getenv("WEATHER_DEFAULT_CITY", "Bünde,de")
WEATHER_CACHE_SECONDS = env_int("WEATHER_CACHE_SECONDS", 600)
