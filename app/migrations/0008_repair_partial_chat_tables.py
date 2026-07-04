# SQLite-only repair migration for local databases where app.0007 was partially applied.

from django.db import migrations


CHAT_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS "app_conversation" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "title" varchar(140) NOT NULL,
    "is_group" bool NOT NULL,
    "created_at" datetime NOT NULL,
    "updated_at" datetime NOT NULL,
    "created_by_id" integer NULL REFERENCES "auth_user" ("id") DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS "app_conversationmember" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "is_archived" bool NOT NULL,
    "last_read_at" datetime NULL,
    "joined_at" datetime NOT NULL,
    "conversation_id" bigint NOT NULL REFERENCES "app_conversation" ("id") DEFERRABLE INITIALLY DEFERRED,
    "user_id" integer NOT NULL REFERENCES "auth_user" ("id") DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS "app_chatmessage" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "body" text NOT NULL,
    "created_at" datetime NOT NULL,
    "edited_at" datetime NULL,
    "sender_id" integer NOT NULL REFERENCES "auth_user" ("id") DEFERRABLE INITIALLY DEFERRED,
    "conversation_id" bigint NOT NULL REFERENCES "app_conversation" ("id") DEFERRABLE INITIALLY DEFERRED
);
"""

CHAT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS "app_conversation_created_by_id_a2304738"
    ON "app_conversation" ("created_by_id");
CREATE INDEX IF NOT EXISTS "app_conversationmember_conversation_id_853531f6"
    ON "app_conversationmember" ("conversation_id");
CREATE INDEX IF NOT EXISTS "app_conversationmember_user_id_df749e4c"
    ON "app_conversationmember" ("user_id");
CREATE UNIQUE INDEX IF NOT EXISTS "unique_conversation_member"
    ON "app_conversationmember" ("conversation_id", "user_id");
CREATE INDEX IF NOT EXISTS "app_chatmessage_sender_id_c2ea3a84"
    ON "app_chatmessage" ("sender_id");
CREATE INDEX IF NOT EXISTS "app_chatmessage_conversation_id_5bd2dd0f"
    ON "app_chatmessage" ("conversation_id");
CREATE INDEX IF NOT EXISTS "app_chatmes_convers_fb6bdc_idx"
    ON "app_chatmessage" ("conversation_id", "created_at");
CREATE INDEX IF NOT EXISTS "app_chatmes_sender__a7a0ce_idx"
    ON "app_chatmessage" ("sender_id", "created_at");
"""

DROP_CHAT_TABLES_SQL = """
DROP TABLE IF EXISTS "app_chatmessage";
DROP TABLE IF EXISTS "app_conversationmember";
DROP TABLE IF EXISTS "app_conversation";
"""

REQUIRED_COLUMNS = {
    "app_conversation": {"id", "title", "is_group", "created_at", "updated_at", "created_by_id"},
    "app_conversationmember": {"id", "conversation_id", "user_id", "is_archived", "last_read_at", "joined_at"},
    "app_chatmessage": {"id", "conversation_id", "sender_id", "body", "created_at", "edited_at"},
}


def _existing_columns(connection, table_name):
    with connection.cursor() as cursor:
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        return {row[1] for row in cursor.fetchall()}


def _execute_sql_script(schema_editor, sql_script):
    for statement in [part.strip() for part in sql_script.split(";") if part.strip()]:
        schema_editor.execute(statement + ";")


def repair_partial_chat_tables(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "sqlite":
        return

    existing_tables = set(connection.introspection.table_names())

    needs_rebuild = False
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        if table_name not in existing_tables:
            needs_rebuild = True
            break
        if not required_columns.issubset(_existing_columns(connection, table_name)):
            needs_rebuild = True
            break

    if needs_rebuild:
        schema_editor.execute("PRAGMA foreign_keys = OFF;")
        _execute_sql_script(schema_editor, DROP_CHAT_TABLES_SQL)
        _execute_sql_script(schema_editor, CHAT_TABLES_SQL)
        schema_editor.execute("PRAGMA foreign_keys = ON;")

    _execute_sql_script(schema_editor, CHAT_INDEXES_SQL)


def noop_reverse(apps, schema_editor):
    # This migration repairs a broken local schema. Reversing it should not remove data.
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("app", "0007_conversation_conversationmember_and_more"),
    ]

    operations = [
        migrations.RunPython(repair_partial_chat_tables, reverse_code=noop_reverse),
    ]
