import io

from django.test import SimpleTestCase

from seamcheck.progress import Progress, null


class _Tty(io.StringIO):
    """A stream that claims to be a terminal, which is the only thing that turns it on."""

    def isatty(self):
        return True


class ProgressTests(SimpleTestCase):
    def test_it_draws_nothing_when_the_stream_is_not_a_terminal(self):
        # `seamcheck json > graph.json` must produce JSON, and a build log must not
        # collect 400 carriage returns. A plain StringIO reports isatty() False.
        stream = io.StringIO()
        bar = Progress(3, stream=stream)

        bar.step("one")
        bar.finish()

        self.assertEqual(stream.getvalue(), "")

    def test_it_draws_on_a_terminal(self):
        stream = _Tty()
        bar = Progress(2, stream=stream)

        bar.step("reading templates")

        self.assertIn("reading templates", stream.getvalue())
        self.assertIn("1/2", stream.getvalue())

    def test_it_redraws_one_line_rather_than_appending(self):
        # Appending would scroll the bar down the terminal, one line per phase.
        stream = _Tty()
        bar = Progress(2, stream=stream)

        bar.step("first")
        bar.step("second")

        self.assertEqual(stream.getvalue().count("\n"), 0)
        self.assertEqual(stream.getvalue().count("\r"), 2)

    def test_finish_erases_the_line_so_the_summary_starts_clean(self):
        stream = _Tty()
        bar = Progress(1, stream=stream)
        bar.step("only")

        bar.finish()

        self.assertTrue(stream.getvalue().endswith("\r\x1b[K"))

    def test_finish_writes_nothing_when_nothing_was_drawn(self):
        # A command that took the no-scan path (triage, a cached report) must not emit a
        # stray escape sequence into someone's output.
        stream = _Tty()

        Progress(3, stream=stream).finish()

        self.assertEqual(stream.getvalue(), "")

    def test_the_total_grows_rather_than_reporting_nineteen_of_fourteen(self):
        # `total` is a hint. A bar that says 19/14, or sits at 100% while work continues,
        # teaches a reader to stop looking at it.
        stream = _Tty()
        bar = Progress(2, stream=stream)

        bar.step("a")
        bar.step("b")
        bar.step("c")

        self.assertEqual(bar.total, 3)
        self.assertIn("3/3", stream.getvalue())

    def test_a_long_label_is_truncated_rather_than_wrapped(self):
        # A wrapped line cannot be overwritten by the next carriage return.
        stream = _Tty()
        bar = Progress(1, stream=stream, width=4)

        bar.step("x" * 500)

        longest = max(len(line) for line in stream.getvalue().split("\r"))
        self.assertLess(longest, 200)

    def test_null_is_silent_and_still_counts(self):
        bar = null()

        bar.step("anything")

        self.assertEqual(bar.count, 1)
        self.assertFalse(bar.enabled)
