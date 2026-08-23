import io
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

from app.services.image_uploads import _detect_image_type_and_size


NOTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
NOTE_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
NOTE_TOTAL_ATTACHMENT_BYTES = 100 * 1024 * 1024
NOTE_IMAGE_MAX_WIDTH = 4096
NOTE_IMAGE_MAX_HEIGHT = 4096

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOCUMENT_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
}
NOTE_FILE_ACCEPT = ".pdf,.txt,.md,.csv,.docx,.xlsx,.pptx"
NOTE_IMAGE_ACCEPT = "image/jpeg,image/png,image/webp"


@deconstructible
class PrivateNoteStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=getattr(settings, "PRIVATE_MEDIA_ROOT", settings.BASE_DIR / "private_media"))


private_note_storage = PrivateNoteStorage()


def note_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"notes/{instance.note_id}/{instance.file_id}{extension}"


def validate_note_upload(upload, *, kind):
    if not upload:
        raise ValidationError("Bitte wähle eine Datei aus.")

    extension = Path(getattr(upload, "name", "")).suffix.lower()
    content_type = (getattr(upload, "content_type", "") or "").lower()
    max_bytes = NOTE_IMAGE_MAX_BYTES if kind == "image" else NOTE_ATTACHMENT_MAX_BYTES
    data = _read_upload(upload, max_bytes)

    if kind == "image":
        _validate_image(data, extension, content_type)
        return

    allowed_types = DOCUMENT_CONTENT_TYPES.get(extension)
    if not allowed_types:
        raise ValidationError("Erlaubt sind PDF, TXT, Markdown, CSV, DOCX, XLSX und PPTX.")
    if content_type and content_type not in allowed_types and content_type != "application/octet-stream":
        raise ValidationError("Dateiendung und gemeldeter Dateityp passen nicht zusammen.")

    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValidationError("Die PDF-Datei ist ungültig.")
    if extension in {".txt", ".md", ".csv"} and b"\x00" in data:
        raise ValidationError("Die Textdatei enthält ungültige Binärdaten.")
    if extension in {".docx", ".xlsx", ".pptx"}:
        _validate_office_archive(data, extension)


def _read_upload(upload, max_bytes):
    size = getattr(upload, "size", 0) or 0
    if size > max_bytes:
        raise ValidationError(f"Die Datei darf maximal {max_bytes // (1024 * 1024)} MB groß sein.")
    try:
        position = upload.tell()
    except (AttributeError, OSError):
        position = 0
    try:
        upload.seek(0)
        data = upload.read(max_bytes + 1)
    except OSError as error:
        raise ValidationError("Die Datei konnte nicht gelesen werden.") from error
    finally:
        try:
            upload.seek(position)
        except (AttributeError, OSError):
            pass
    if len(data) > max_bytes:
        raise ValidationError(f"Die Datei darf maximal {max_bytes // (1024 * 1024)} MB groß sein.")
    return data


def _validate_image(data, extension, content_type):
    if extension not in IMAGE_EXTENSIONS:
        raise ValidationError("Erlaubt sind JPG-, PNG- und WebP-Bilder.")
    try:
        image_type, width, height = _detect_image_type_and_size(data)
    except ValidationError:
        raise
    expected_extensions = {
        "jpeg": {".jpg", ".jpeg"},
        "png": {".png"},
        "webp": {".webp"},
    }
    if image_type not in expected_extensions or extension not in expected_extensions[image_type]:
        raise ValidationError("Dateiendung und erkanntes Bildformat passen nicht zusammen.")
    expected_types = {
        "jpeg": {"image/jpeg", "image/pjpeg"},
        "png": {"image/png"},
        "webp": {"image/webp"},
    }
    if content_type and content_type not in expected_types[image_type]:
        raise ValidationError("Der gemeldete Bildtyp ist ungültig.")
    if width < 1 or height < 1 or width > NOTE_IMAGE_MAX_WIDTH or height > NOTE_IMAGE_MAX_HEIGHT:
        raise ValidationError("Bilder dürfen maximal 4096×4096 Pixel groß sein.")


def _validate_office_archive(data, extension):
    expected_root = {".docx": "word/", ".xlsx": "xl/", ".pptx": "ppt/"}[extension]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as error:
        raise ValidationError("Die Office-Datei ist beschädigt oder ungültig.") from error
    if "[Content_Types].xml" not in names or not any(name.startswith(expected_root) for name in names):
        raise ValidationError("Die Office-Datei passt nicht zu ihrer Dateiendung.")


def new_attachment_uuid():
    return uuid.uuid4()
