import hashlib
from functools import lru_cache
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static


register = template.Library()


@lru_cache(maxsize=256)
def _content_digest(file_path, modified_ns, size):
    """Return a cached digest that is invalidated when the file changes."""
    del modified_ns, size
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as static_file:
        for chunk in iter(lambda: static_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _static_digest(path):
    resolved_path = finders.find(path)
    if not resolved_path:
        return ""
    if isinstance(resolved_path, (list, tuple)):
        resolved_path = resolved_path[0]

    file_path = Path(resolved_path)
    file_stat = file_path.stat()
    return _content_digest(
        str(file_path),
        file_stat.st_mtime_ns,
        file_stat.st_size,
    )


@register.simple_tag
def versioned_static(path):
    """Build a static URL with a version derived from the file contents."""
    url = static(path)
    digest = _static_digest(path)
    if not digest:
        return url

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={digest}"


@register.simple_tag
def static_version(*paths):
    """Build one stable version from several static files."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.encode("utf-8"))
        digest.update(_static_digest(path).encode("ascii"))
    return digest.hexdigest()[:12]
