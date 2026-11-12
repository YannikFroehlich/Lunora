import tempfile
from pathlib import Path

import django
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import override_settings


def _test_settings(root):
    return override_settings(
        MEDIA_ROOT=Path(root) / "media",
        PRIVATE_MEDIA_ROOT=Path(root) / "private_media",
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    )


def _setup_spawned_worker(root):
    """--parallel workers started with "spawn" (Windows, macOS) don't inherit the main process's
    overrides; forked ones (Linux) do and never call this. Django calls it before django.setup(),
    which must come first: overriding still-unloaded settings would wrap nothing."""
    django.setup()
    _test_settings(root).enable()


class IsolatedParallelTestSuite(ParallelTestSuite):
    process_setup = _setup_spawned_worker


class IsolatedMediaTestRunner(DiscoverRunner):
    """Point MEDIA_ROOT and PRIVATE_MEDIA_ROOT at a throwaway directory for the whole run,
    so uploads made by tests never land in the developer's real media folders.

    Also swaps in a fast password hasher: the production PBKDF2 cost made every test that
    creates or logs in a user take about two seconds, turning the suite into hours."""

    parallel_test_suite = IsolatedParallelTestSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_dir = tempfile.TemporaryDirectory(prefix="lunora-test-media-", ignore_cleanup_errors=True)
        self._settings_override = _test_settings(self._media_dir.name)
        self._settings_override.enable()

    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        if isinstance(suite, ParallelTestSuite):
            suite.process_setup_args = (self._media_dir.name,)
        return suite

    def teardown_test_environment(self, **kwargs):
        self._settings_override.disable()
        self._media_dir.cleanup()
        super().teardown_test_environment(**kwargs)
