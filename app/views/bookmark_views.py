from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from app.forms import BookmarkForm
from app.models import Bookmark
from app.services.bookmarks import (
    MAX_BOOKMARKS,
    add_bookmark,
    export_bookmark_file,
    find_duplicate,
    host_of,
    import_bookmark_file,
    normalize_tags,
    refresh_preview,
    search_bookmarks,
    tag_counts,
)
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_date
from app.views.navigation import posted_id


def _form_errors(form):
    return " ".join(error for errors in form.errors.values() for error in errors)


@login_required
def bookmarks(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    if request.method == "POST":
        _handle_post(request)
        # Back to the same search, so a change doesn't lose the filter.
        return redirect(request.get_full_path())

    query = request.GET.get("q", "").strip()
    tag = normalize_tags(request.GET.get("tag", "")).split(" ")[0]
    broken_only = request.GET.get("broken") == "1"
    own = Bookmark.objects.filter(owner=request.user)
    items = search_bookmarks(own, query, tag)
    if broken_only:
        items = items.filter(unreachable_since__isnull=False)
    items = items.order_by("-created_at", "-id")
    return render(
        request,
        "app/bookmarks.html",
        {
            "active_page": "tools",
            "bookmark_items": [
                {
                    "bookmark": item,
                    "host": host_of(item.url),
                    "unreachable_label": format_user_date(item.unreachable_since, request.user)
                    if item.unreachable_since
                    else "",
                    "form": BookmarkForm(instance=item, auto_id=False),
                }
                for item in items
            ],
            "bookmark_total": own.count(),
            "broken_total": own.filter(unreachable_since__isnull=False).count(),
            "broken_only": broken_only,
            "bookmark_limit": MAX_BOOKMARKS,
            "bookmark_form": BookmarkForm(auto_id="bookmark_new_%s"),
            "tags": tag_counts(request.user),
            "query": query,
            "active_tag": tag,
        },
    )


@login_required
@require_GET
def bookmarks_export(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    response = HttpResponse(export_bookmark_file(request.user), content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="lunora-lesezeichen.html"'
    response["Cache-Control"] = "private, no-store"
    return response


def _handle_post(request):
    form_name = request.POST.get("form_name")
    if form_name == "bookmark_add":
        form = BookmarkForm(request.POST)
        if not form.is_valid():
            django_messages.error(request, _form_errors(form))
            return
        _bookmark, error = add_bookmark(request.user, form)
        if error:
            django_messages.error(request, error)
        else:
            django_messages.success(request, "Lesezeichen hinzugefügt.")
        return
    if form_name == "bookmark_import":
        uploaded = request.FILES.get("bookmark_file")
        if uploaded is None:
            django_messages.error(request, "Bitte wähle eine Lesezeichen-Datei aus.")
            return
        try:
            imported, skipped = import_bookmark_file(request.user, uploaded)
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
            return
        text = f"{imported} Lesezeichen importiert."
        if skipped:
            text += f" {skipped} übersprungen (schon vorhanden, keine Web-Adresse oder Limit von {MAX_BOOKMARKS} erreicht)."
        django_messages.success(request, text)
        return
    if form_name == "bookmark_delete_broken":
        # Bookmarks have no trash, so this deletes for good; the button asks first.
        deleted, _detail = Bookmark.objects.filter(
            owner=request.user, unreachable_since__isnull=False
        ).delete()
        django_messages.success(request, f"{deleted} nicht erreichbare Lesezeichen gelöscht.")
        return

    bookmark = Bookmark.objects.filter(owner=request.user, pk=posted_id(request, "bookmark_id")).first()
    if bookmark is None:
        return
    if form_name == "bookmark_edit":
        old_url = bookmark.url
        form = BookmarkForm(request.POST, instance=bookmark)
        if not form.is_valid():
            django_messages.error(request, _form_errors(form))
            return
        bookmark = form.save(commit=False)
        existing = find_duplicate(request.user, bookmark.url, exclude_id=bookmark.pk)
        if existing is not None:
            django_messages.error(request, f"Diese Adresse ist schon als „{existing.title}“ gespeichert.")
            return
        bookmark.title = bookmark.title or host_of(bookmark.url)[:80]
        if bookmark.url != old_url:
            # The old description belongs to the old page.
            bookmark.description = ""
        bookmark.save()
        django_messages.success(request, "Lesezeichen gespeichert.")
    elif form_name == "bookmark_refresh":
        if refresh_preview(request.user, bookmark):
            django_messages.success(request, "Vorschau aktualisiert.")
        else:
            django_messages.error(request, "Die Seite liefert keine Beschreibung.")
    elif form_name == "bookmark_delete":
        bookmark.delete()
        django_messages.success(request, "Lesezeichen gelöscht.")
