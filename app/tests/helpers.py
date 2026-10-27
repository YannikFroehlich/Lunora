"""Shared fixtures and fake HTTP responses for the test modules."""

import json

PNG_1X1_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def note_document(text="Gedanke"):
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"textAlign": None},
                "content": [{"type": "text", "text": text, "marks": [{"type": "bold"}]}],
            }
        ],
    }


class FakeJsonResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return self.payload


class FakeWeatherResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return json.dumps(self.payload).encode("utf-8")


class FakeWeatherTileResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return b"png-bytes"


def postgres_connection():
    """A PostgreSQL backend that never connects, for compiling production-only SQL.

    The suite runs on SQLite, so PostgreSQL-specific query shapes are only checked
    by compiling them against this backend.
    """
    from django.db.backends.postgresql.base import DatabaseWrapper

    connection = DatabaseWrapper(
        {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "lunora",
            "USER": "lunora",
            "PASSWORD": "",
            "HOST": "127.0.0.1",
            "PORT": "5432",
            "OPTIONS": {},
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False,
            "TIME_ZONE": None,
            "TEST": {"CHARSET": None, "COLLATION": None, "NAME": None, "MIRROR": None},
        },
        "postgres-sql-check",
    )
    # select_for_update() refuses to compile in autocommit; asking would open a connection.
    connection.get_autocommit = lambda: False
    return connection
