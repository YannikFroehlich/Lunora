"""What is deployed and set up, for the superuser-only administration page."""

import logging
import re
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def deployed_commit(git_dir=None):
    """The commit the checkout is on, read from ``.git`` without running git, or None.

    The production checkout is updated with ``git merge --ff-only`` and the services are restarted
    right after, so this is the running release. No git subprocess: the web service user may not
    own the checkout, which makes git refuse it as a "dubious ownership" repository.
    """
    git_dir = Path(git_dir or settings.BASE_DIR / ".git")
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: "):
            ref = head.removeprefix("ref: ")
            ref_file = git_dir / ref
            if ref_file.is_file():
                head = ref_file.read_text(encoding="ascii").strip()
            else:
                packed = (git_dir / "packed-refs").read_text(encoding="ascii").splitlines()
                head = next((line.split()[0] for line in packed if line.endswith(f" {ref}")), "")
    except (OSError, UnicodeDecodeError):
        return None
    return head if COMMIT_PATTERN.fullmatch(head) else None


def send_test_mail(user):
    """Send a test mail to ``user``'s own address; True on success. The cause only goes to the log."""
    try:
        send_mail(
            "Lunora-Testmail",
            "Diese Testmail wurde über die Administration verschickt. Der E-Mail-Versand funktioniert.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
    except Exception:
        logger.exception("Test mail to user %s failed", user.pk)
        return False
    return True
