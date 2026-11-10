from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from app.services.system_settings import disabled_feature_response
from app.services.trash import (
    TRASH_RETENTION,
    RestoreError,
    delete_item,
    empty_trash,
    enabled_kinds,
    restore_item,
    trash_items,
)


@login_required
def trash(request):
    kinds = enabled_kinds()
    if not kinds:
        return disabled_feature_response(request, "tasks")

    form_name = request.POST.get("form_name") if request.method == "POST" else None
    item_id = request.POST.get("item_id") or 0
    if form_name == "trash_restore":
        try:
            item = restore_item(request.user, item_id)
        except RestoreError as error:
            django_messages.error(request, str(error))
        else:
            if item is not None:
                django_messages.success(
                    request, f"{item.get_kind_display()} „{item.title}“ wiederhergestellt."
                )
        return redirect("trash")
    if form_name == "trash_delete":
        if delete_item(request.user, item_id):
            django_messages.success(request, "Endgültig gelöscht.")
        return redirect("trash")
    if form_name == "trash_empty":
        if empty_trash(request.user):
            django_messages.success(request, "Papierkorb geleert.")
        return redirect("trash")

    return render(
        request,
        "app/trash.html",
        {
            "active_page": "tasks" if "task" in kinds else "calendar",
            "items": trash_items(request.user),
            "retention_days": TRASH_RETENTION.days,
            "shows_tasks": "task" in kinds,
            "shows_events": "event" in kinds,
        },
    )
