"""Serving a stored note or chat attachment, with single byte ranges for inline playback."""

from django.http import FileResponse, HttpResponse
from django.utils.http import content_disposition_header


def attachment_response(request, attachment, disposition, *, content_type=None):
    """The file of `attachment` (anything with `file`, `size`, `content_type`, `original_name`).

    Inline answers honour one ``Range: bytes=…`` header with 206, because Safari only plays
    <audio> from servers that do."""
    content_type = content_type or attachment.content_type or "application/octet-stream"
    byte_range = requested_byte_range(request, attachment.size) if disposition == "inline" else None
    if byte_range == "invalid":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{attachment.size}"
        return response
    if byte_range:
        start, end = byte_range
        with attachment.file.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(end - start + 1)
        response = HttpResponse(chunk, status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{attachment.size}"
    else:
        response = FileResponse(attachment.file.open("rb"), content_type=content_type)
    response["Accept-Ranges"] = "bytes" if disposition == "inline" else "none"
    response["Content-Disposition"] = content_disposition_header(
        disposition == "download", attachment.original_name
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def requested_byte_range(request, size):
    """Parse a single ``bytes=start-end`` range; ``None`` serves the whole file."""
    header = request.headers.get("Range", "")
    if not header.startswith("bytes=") or "," in header or size <= 0:
        return None
    start_text, _, end_text = header[len("bytes=") :].strip().partition("-")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            suffix = int(end_text)
            if suffix <= 0:
                return "invalid"
            start, end = max(size - suffix, 0), size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)
