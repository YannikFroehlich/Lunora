from pathlib import Path

from app.services.note_files import IMAGE_EXTENSIONS, private_note_storage, validate_note_upload

__all__ = ["chat_upload_to", "infer_attachment_kind", "private_note_storage", "validate_note_upload"]


def chat_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"chat/{instance.message.conversation_id}/{instance.file_id}{extension}"


def infer_attachment_kind(filename):
    return "image" if Path(filename).suffix.lower() in IMAGE_EXTENSIONS else "file"
