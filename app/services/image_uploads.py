from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


PROFILE_IMAGE_ALLOWED_TYPES = {
    "jpeg": {
        "content_types": {"image/jpeg", "image/pjpeg"},
        "extensions": {".jpg", ".jpeg"},
    },
    "png": {
        "content_types": {"image/png"},
        "extensions": {".png"},
    },
    "webp": {
        "content_types": {"image/webp"},
        "extensions": {".webp"},
    },
}
PROFILE_IMAGE_ACCEPT = "image/jpeg,image/png,image/webp"
PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
PROFILE_IMAGE_MAX_WIDTH = 4096
PROFILE_IMAGE_MAX_HEIGHT = 4096


def validate_profile_image_file(image):
    if not image:
        return

    max_bytes = getattr(settings, "PROFILE_IMAGE_MAX_BYTES", PROFILE_IMAGE_MAX_BYTES)
    max_width = getattr(settings, "PROFILE_IMAGE_MAX_WIDTH", PROFILE_IMAGE_MAX_WIDTH)
    max_height = getattr(settings, "PROFILE_IMAGE_MAX_HEIGHT", PROFILE_IMAGE_MAX_HEIGHT)
    size = getattr(image, "size", None)

    if size and size > max_bytes:
        raise ValidationError(f"Profilbilder duerfen maximal {max_bytes // (1024 * 1024)} MB gross sein.")

    data = _read_image_bytes(image, max_bytes)
    image_type, width, height = _detect_image_type_and_size(data)
    allowed = PROFILE_IMAGE_ALLOWED_TYPES.get(image_type)
    if not allowed:
        raise ValidationError("Bitte lade ein gueltiges JPG-, PNG- oder WebP-Bild hoch.")

    extension = Path(getattr(image, "name", "")).suffix.lower()
    if extension not in allowed["extensions"]:
        raise ValidationError("Die Dateiendung passt nicht zum erkannten Bildformat.")

    content_type = getattr(image, "content_type", "")
    if content_type and content_type.lower() not in allowed["content_types"]:
        raise ValidationError("Der Dateityp passt nicht zum erkannten Bildformat.")

    if width < 1 or height < 1:
        raise ValidationError("Das Profilbild hat ungueltige Abmessungen.")
    if width > max_width or height > max_height:
        raise ValidationError(f"Profilbilder duerfen maximal {max_width}x{max_height} Pixel gross sein.")


def _read_image_bytes(image, max_bytes):
    try:
        original_position = image.tell()
    except (AttributeError, OSError):
        original_position = None

    try:
        image.seek(0)
    except (AttributeError, OSError):
        pass

    try:
        data = image.read(max_bytes + 1)
    except OSError as error:
        raise ValidationError("Das Profilbild konnte nicht gelesen werden.") from error
    if len(data) > max_bytes:
        raise ValidationError(f"Profilbilder duerfen maximal {max_bytes // (1024 * 1024)} MB gross sein.")

    try:
        image.seek(original_position or 0)
    except (AttributeError, OSError):
        pass

    return data


def _detect_image_type_and_size(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_size(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp_size(data)
    return None, 0, 0


def _png_size(data):
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValidationError("Das PNG-Bild ist unvollstaendig oder beschaedigt.")
    return "png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data):
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }

    while index < len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break

        marker = data[index]
        index += 1
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if index + 2 > len(data):
            break

        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break

        segment_start = index + 2
        if marker in sof_markers:
            if segment_start + 5 > len(data):
                break
            height = int.from_bytes(data[segment_start + 1 : segment_start + 3], "big")
            width = int.from_bytes(data[segment_start + 3 : segment_start + 5], "big")
            return "jpeg", width, height

        index += segment_length

    raise ValidationError("Das JPG-Bild ist unvollstaendig oder beschaedigt.")


def _webp_size(data):
    if len(data) < 30:
        raise ValidationError("Das WebP-Bild ist unvollstaendig oder beschaedigt.")

    chunk_type = data[12:16]
    if chunk_type == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return "webp", width, height

    if chunk_type == b"VP8L":
        if data[20] != 0x2F:
            raise ValidationError("Das WebP-Bild ist unvollstaendig oder beschaedigt.")
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return "webp", width, height

    if chunk_type == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise ValidationError("Das WebP-Bild ist unvollstaendig oder beschaedigt.")
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return "webp", width, height

    raise ValidationError("Das WebP-Bild ist unvollstaendig oder beschaedigt.")
