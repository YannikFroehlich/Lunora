import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations

INDEX = django.contrib.postgres.indexes.GinIndex(fields=["search_vector"], name="app_note_search_vector_gin")


def create_index_and_backfill(apps, schema_editor):
    """GIN index plus the initial vectors; PostgreSQL only (SQLite has neither GIN nor tsvector)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    from django.contrib.postgres.search import SearchVector

    Note = apps.get_model("app", "Note")
    schema_editor.add_index(Note, INDEX)
    # Frozen copy of app.services.note_search.note_search_vector().
    Note.objects.using(schema_editor.connection.alias).update(
        search_vector=SearchVector("title", weight="A", config="german")
        + SearchVector("plain_text", weight="B", config="german")
    )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.remove_index(apps.get_model("app", "Note"), INDEX)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0070_task_status_and_shares"),
    ]

    operations = [
        migrations.AddField(
            model_name="note",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(blank=True, editable=False, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.AddIndex(model_name="note", index=INDEX)],
            database_operations=[migrations.RunPython(create_index_and_backfill, drop_index)],
        ),
    ]
