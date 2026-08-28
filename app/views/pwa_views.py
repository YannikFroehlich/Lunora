from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET


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
