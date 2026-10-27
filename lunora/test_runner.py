import tempfile
from pathlib import Path

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class IsolatedMediaTestRunner(DiscoverRunner):
    """Point MEDIA_ROOT and PRIVATE_MEDIA_ROOT at a throwaway directory for the whole run,
    so uploads made by tests never land in the developer's real media folders.

    Also swaps in a fast password hasher: the production PBKDF2 cost made every test that
    creates or logs in a user take about two seconds, turning the suite into hours."""

    # ponytail: overrides live in this process only; --parallel workers started with "spawn"
    # (the Windows default) would still use the real folders. Move the override into the
    # workers' setup if the suite ever runs in parallel.
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_dir = tempfile.TemporaryDirectory(prefix="lunora-test-media-", ignore_cleanup_errors=True)
        root = Path(self._media_dir.name)
        self._settings_override = override_settings(
            MEDIA_ROOT=root / "media",
            PRIVATE_MEDIA_ROOT=root / "private_media",
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
        )
        self._settings_override.enable()

    def teardown_test_environment(self, **kwargs):
        self._settings_override.disable()
        self._media_dir.cleanup()
        super().teardown_test_environment(**kwargs)
