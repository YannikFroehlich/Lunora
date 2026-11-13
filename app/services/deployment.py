import re
from pathlib import Path

from django.conf import settings

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
