"""The one network call the package makes: a version check, and the promise that it never
turns a working command into a slow or broken one. See seamcheck/updatecheck.py for why
this exists despite "seamcheck makes no network call" being the rule everywhere else.
"""
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import updatecheck


class EnabledTests(SimpleTestCase):
    def test_on_by_default(self):
        self.assertTrue(updatecheck.enabled({}))

    def test_off_switch(self):
        self.assertFalse(updatecheck.enabled({"SEAMCHECK_NO_UPDATE_CHECK": "1"}))
        self.assertFalse(updatecheck.enabled({"SEAMCHECK_NO_UPDATE_CHECK": "true"}))

    def test_off_switch_ignores_a_falsy_value(self):
        # "0"/"false"/"" mean the switch was set to OFF-meaning-off, not opted out.
        self.assertTrue(updatecheck.enabled({"SEAMCHECK_NO_UPDATE_CHECK": "0"}))
        self.assertTrue(updatecheck.enabled({"SEAMCHECK_NO_UPDATE_CHECK": ""}))

    def test_ci_is_skipped_automatically(self):
        # The README promises "no network... in CI" for `seamcheck check --since` - this
        # is the line that keeps that true without CI ever hearing of the update check.
        self.assertFalse(updatecheck.enabled({"CI": "true"}))


class ParseTests(SimpleTestCase):
    def test_plain_semver(self):
        self.assertEqual(updatecheck._parse("0.13.0"), (0, 13, 0))

    def test_short_version_pads_with_zero(self):
        self.assertEqual(updatecheck._parse("1.2"), (1, 2, 0))

    def test_a_prerelease_suffix_does_not_raise(self):
        self.assertEqual(updatecheck._parse("0.14.0rc1"), (0, 14, 0))


class LatestVersionTests(SimpleTestCase):
    def _env(self, tmp):
        return {"XDG_CACHE_HOME": tmp}

    def test_first_call_fetches_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = mock.Mock(return_value="0.14.0")
            first = updatecheck.latest_version(self._env(tmp), fetch=fetch, now=1000.0)
            second = updatecheck.latest_version(self._env(tmp), fetch=fetch, now=1001.0)

            self.assertEqual(first, "0.14.0")
            self.assertEqual(second, "0.14.0")
            fetch.assert_called_once()  # the second call is inside the cache window

    def test_a_stale_cache_is_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = mock.Mock(side_effect=["0.14.0", "0.15.0"])
            updatecheck.latest_version(self._env(tmp), fetch=fetch, now=1000.0)
            later = updatecheck.latest_version(
                self._env(tmp), fetch=fetch, now=1000.0 + updatecheck._CHECK_INTERVAL + 1)

            self.assertEqual(later, "0.15.0")
            self.assertEqual(fetch.call_count, 2)

    def test_a_failed_fetch_falls_back_to_the_last_good_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = mock.Mock(return_value="0.14.0")
            updatecheck.latest_version(self._env(tmp), fetch=good, now=1000.0)

            broken = mock.Mock(return_value=None)
            later = updatecheck.latest_version(
                self._env(tmp), fetch=broken, now=1000.0 + updatecheck._CHECK_INTERVAL + 1)

            self.assertEqual(later, "0.14.0")

    def test_a_failed_fetch_with_no_prior_cache_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = mock.Mock(return_value=None)
            self.assertIsNone(updatecheck.latest_version(self._env(tmp), fetch=broken, now=1000.0))


class NoticeTests(SimpleTestCase):
    def _env(self, tmp, **extra):
        return {"XDG_CACHE_HOME": tmp, **extra}

    def test_a_newer_version_produces_a_message(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "seamcheck.updatecheck._installed_version", return_value="0.13.0"):
            message = updatecheck.notice(
                self._env(tmp), fetch=lambda: "0.14.0", now=1000.0)

            self.assertIn("0.13.0", message)
            self.assertIn("0.14.0", message)
            self.assertIn("pip install -U seamcheck", message)

    def test_already_current_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "seamcheck.updatecheck._installed_version", return_value="0.13.0"):
            self.assertIsNone(updatecheck.notice(
                self._env(tmp), fetch=lambda: "0.13.0", now=1000.0))

    def test_a_newer_installed_dev_version_is_silent(self):
        # A local build ahead of the last PyPI release is not "behind" - the comparison
        # has to be able to say so, not just "not equal".
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "seamcheck.updatecheck._installed_version", return_value="0.14.0"):
            self.assertIsNone(updatecheck.notice(
                self._env(tmp), fetch=lambda: "0.13.0", now=1000.0))

    def test_disabled_is_silent_and_never_calls_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = mock.Mock(return_value="99.0.0")
            message = updatecheck.notice(
                self._env(tmp, SEAMCHECK_NO_UPDATE_CHECK="1"), fetch=fetch, now=1000.0)

            self.assertIsNone(message)
            fetch.assert_not_called()

    def test_no_installed_version_is_silent(self):
        # A source checkout with no `pip install` behind it has nothing to compare
        # PyPI's number against.
        from importlib.metadata import PackageNotFoundError

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "seamcheck.updatecheck._installed_version", side_effect=PackageNotFoundError):
            self.assertIsNone(updatecheck.notice(
                self._env(tmp), fetch=lambda: "99.0.0", now=1000.0))

    def test_a_broken_fetch_is_silent_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "seamcheck.updatecheck._installed_version", return_value="0.13.0"):
            def _boom():
                raise OSError("network is down")

            self.assertIsNone(updatecheck.notice(self._env(tmp), fetch=_boom, now=1000.0))
