"""Self-service account deletion."""

from django.db import transaction

from app.models import ChatMessageAttachment, NoteAttachment, Profile


def account_deletion_blocked_reason(user):
    if user.is_superuser:
        return "Administratorkonten können hier nicht gelöscht werden. Nutze dafür die Django-Administration."
    return None


@transaction.atomic
def delete_account(user):
    """Delete the user and everything that cascades from them, including their stored files.

    Django's cascade only removes rows, so attachment and profile image files are collected
    first and removed from storage once the transaction has committed — a rollback keeps both.
    """
    files = [
        attachment.file for attachment in NoteAttachment.objects.filter(note__owner=user) if attachment.file
    ]
    files += [
        attachment.file
        for attachment in ChatMessageAttachment.objects.filter(message__sender=user)
        if attachment.file
    ]
    profile = Profile.objects.filter(user=user).first()
    if profile and profile.profile_image:
        files.append(profile.profile_image)

    user.delete()

    def delete_files():
        for field_file in files:
            field_file.storage.delete(field_file.name)

    transaction.on_commit(delete_files)
