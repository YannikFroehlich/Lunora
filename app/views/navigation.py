from django.utils.http import url_has_allowed_host_and_scheme


def local_return_to(request):
    """The POSTed return_to when it is a local path on this host, else None (see CLAUDE.md, return_to)."""
    return_to = request.POST.get("return_to")
    if (
        return_to
        and return_to.startswith("/")
        and url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return return_to
    return None
