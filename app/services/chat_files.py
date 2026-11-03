from pathlib import Path

from django.core.exceptions import ValidationError

from app.services.note_files import IMAGE_EXTENSIONS, _read_upload, private_note_storage, validate_note_upload

__all__ = [
    "AUDIO_EXTENSIONS",
    "CHAT_AUDIO_MAX_BYTES",
    "CHAT_VOICE_MAX_SECONDS",
    "chat_upload_to",
    "infer_attachment_kind",
    "private_note_storage",
    "validate_chat_upload",
]

# Voice messages are recorded in the browser with MediaRecorder: Chrome and Firefox produce
# WebM/Opus or Ogg/Opus, Safari MP4/AAC (stored as .m4a). MP3 covers voice memos from phones.
# The stored content type is derived from the extension, never taken from the client.
AUDIO_CONTENT_TYPES = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
}
AUDIO_EXTENSIONS = set(AUDIO_CONTENT_TYPES)
CHAT_AUDIO_MAX_BYTES = 10 * 1024 * 1024
CHAT_VOICE_MAX_SECONDS = 5 * 60
# Browsers report recordings with varying container types (Firefox may say video/webm).
_ACCEPTED_CLIENT_AUDIO_TYPES = {"", "application/octet-stream", "video/webm", "video/mp4", "video/ogg"}


def chat_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"chat/{instance.message.conversation_id}/{instance.file_id}{extension}"


def infer_attachment_kind(filename):
    extension = Path(filename).suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return "file"


def audio_content_type(filename):
    return AUDIO_CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def validate_chat_upload(upload):
    kind = infer_attachment_kind(getattr(upload, "name", ""))
    if kind == "audio":
        _validate_audio(upload)
    else:
        validate_note_upload(upload, kind=kind)
    return kind


def _validate_audio(upload):
    extension = Path(upload.name).suffix.lower()
    content_type = (getattr(upload, "content_type", "") or "").lower()
    if not content_type.startswith("audio/") and content_type not in _ACCEPTED_CLIENT_AUDIO_TYPES:
        raise ValidationError("Dateiendung und gemeldeter Dateityp passen nicht zusammen.")
    data = _read_upload(upload, CHAT_AUDIO_MAX_BYTES)
    if not _audio_signature_matches(data, extension):
        raise ValidationError("Die Audiodatei ist ungültig.")


def _audio_signature_matches(data, extension):
    if extension == ".webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    if extension in {".ogg", ".oga"}:
        return data.startswith(b"OggS")
    if extension == ".m4a":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if extension == ".mp3":
        return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    return False
