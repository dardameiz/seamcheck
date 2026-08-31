import logging
import warnings

from django.test import SimpleTestCase

from seamcheck.quiet import quiet


class QuietTests(SimpleTestCase):
    def test_it_drops_the_host_projects_warnings(self):
        # The project this was built against logs four WARNING lines and raises a
        # RuntimeWarning on import, twice, before seamcheck has said anything.
        with quiet(), self.assertLogs("test.quiet", level="ERROR") as captured:
            logger = logging.getLogger("test.quiet")
            logger.warning("monkey-patched something")
            logger.error("this one matters")

        self.assertEqual(captured.output, ["ERROR:test.quiet:this one matters"])

    def test_an_error_still_gets_through(self):
        # Suppression is a level threshold, not a mute button. A tidy terminal is not
        # worth hiding a real failure.
        self.assertTrue(logging.getLogger("test.quiet").isEnabledFor(logging.ERROR))
        with quiet():
            self.assertTrue(logging.getLogger("test.quiet").isEnabledFor(logging.ERROR))
            self.assertFalse(logging.getLogger("test.quiet").isEnabledFor(logging.WARNING))

    def test_it_swallows_warnings_raised_inside_the_block(self):
        with quiet(), warnings.catch_warnings(record=True) as seen:
            warnings.warn(
                "accessing the database during app initialization",
                RuntimeWarning, stacklevel=2,
            )

        self.assertEqual(list(seen), [])

    def test_it_puts_everything_back_afterwards(self):
        # A CLI process is short-lived, but a library caller's logging is not seamcheck's
        # to leave switched off.
        before = logging.root.manager.disable

        with quiet():
            pass

        self.assertEqual(logging.root.manager.disable, before)

    def test_disabled_changes_nothing(self):
        # What --verbose does: the noise is the point when the import itself is broken.
        with quiet(enabled=False):
            self.assertTrue(logging.getLogger("test.quiet").isEnabledFor(logging.WARNING))

    def test_a_caller_who_had_already_disabled_something_gets_theirs_back(self):
        logging.disable(logging.DEBUG)
        try:
            with quiet():
                pass

            self.assertEqual(logging.root.manager.disable, logging.DEBUG)
        finally:
            logging.disable(logging.NOTSET)
