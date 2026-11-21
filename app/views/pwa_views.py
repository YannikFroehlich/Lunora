from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from app.models import WishList
from app.services.data_import import import_shared_note
from app.services.system_settings import disabled_feature_response, feature_enabled


@require_GET
def service_worker(request):
    response = HttpResponse(
        render_to_string("app/service-worker.js"),
        content_type="application/javascript; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def offline(request):
    response = HttpResponse(
        render_to_string("app/offline.html"),
        content_type="text/html; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Robots-Tag"] = "noindex, noarchive"
    return response


@login_required
@require_GET
def share_target(request):
    """Where the installed app's share sheet lands. The service worker hands the shared title,
    text and link over in the URL fragment, which share.js puts into the form; its buttons post
    to the pages that already create tasks, bookmarks, recipes and wish list items."""
    wish_lists = WishList.objects.filter(owner=request.user) if feature_enabled("tools") else []
    return render(request, "app/share.html", {"wish_lists": wish_lists})


@login_required
@require_POST
def share_note(request):
    if not feature_enabled("notes"):
        return disabled_feature_response(request, "notes")
    try:
        note = import_shared_note(
            request.user,
            request.POST.get("title", "")[:200],
            request.POST.get("text", ""),
            request.POST.get("url", "")[:500],
        )
    except ValidationError as error:
        django_messages.error(request, " ".join(error.messages))
        return redirect("share_target")
    return redirect("note_detail", note_id=note.id)
