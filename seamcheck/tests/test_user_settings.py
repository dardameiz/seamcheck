"""The one decision that belongs to the machine, not to the project.

Reported from use: "seamcheck should serve always a link for phone, because people and
LLMs have the links on the phone outside" - outside the wifi, where the LAN address is
just a number that times out. The public link answers that, and it is also the one thing
in seamcheck that leaves the machine, so it is opt-in ONCE per machine rather than either
a flag to remember every run or a default that publishes a stranger's private codebase.
"""
import json
import os
import pathlib
import tempfile

from django.test import SimpleTestCase


class WhereTheSettingLivesTests(SimpleTestCase):
    def test_it_is_per_machine_not_per_project(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            path = usersettings.settings_path({"XDG_CONFIG_HOME": home})

            self.assertEqual(path, pathlib.Path(home) / "seamcheck" / "settings.json")

    def test_without_xdg_it_falls_back_to_the_home_directory(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            path = usersettings.settings_path({"HOME": home})

            self.assertEqual(path,
                             pathlib.Path(home) / ".config" / "seamcheck" / "settings.json")

    def test_writing_creates_the_directory_and_says_where(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = {"XDG_CONFIG_HOME": os.path.join(home, "nothing", "here")}

            path = usersettings.write("tunnel", "always", env)

            self.assertTrue(path.exists(), path)
            self.assertEqual(json.loads(path.read_text()), {"tunnel": "always"})
            self.assertEqual(usersettings.read(env), {"tunnel": "always"})

    def test_a_second_write_keeps_the_other_keys(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = {"XDG_CONFIG_HOME": home}
            usersettings.write("something_else", "kept", env)

            usersettings.write("tunnel", "always", env)

            self.assertEqual(usersettings.read(env),
                             {"something_else": "kept", "tunnel": "always"})

    def test_a_corrupt_file_reads_as_no_settings_rather_than_crashing(self):
        # Half a JSON file is not a reason for `seamcheck map` to stop working.
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = {"XDG_CONFIG_HOME": home}
            path = usersettings.settings_path(env)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"tunnel": "alwa')

            self.assertEqual(usersettings.read(env), {})


class WhichAnswerWinsTests(SimpleTestCase):
    """The ladder, highest first: the flag typed now, the environment, the machine, then
    the default. Each step has to be able to say NO as loudly as it says yes."""

    def _env(self, home, **rest):
        return {"XDG_CONFIG_HOME": home, **rest}

    def test_by_default_nothing_leaves_the_machine(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            wanted, why = usersettings.wants_tunnel(env=self._env(home))

            self.assertFalse(wanted)
            self.assertIn("not set", why)

    def test_the_setting_turns_it_on_for_every_later_run(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            usersettings.write("tunnel", "always", env)

            wanted, why = usersettings.wants_tunnel(env=env)

            self.assertTrue(wanted)
            self.assertIn("settings.json", why)

    def test_local_only_beats_the_setting(self):
        # The one flag whose whole meaning is "nothing off this machine". If a stored
        # preference could overrule it, the flag would be a lie.
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            usersettings.write("tunnel", "always", env)

            wanted, why = usersettings.wants_tunnel(local_only=True, env=env)

            self.assertFalse(wanted)
            self.assertIn("--local-only", why)

    def test_the_flag_wins_over_a_setting_that_says_never(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = self._env(home)
            usersettings.write("tunnel", "never", env)

            wanted, why = usersettings.wants_tunnel(flag=True, env=env)

            self.assertTrue(wanted)
            self.assertIn("--tunnel", why)

    def test_the_environment_wins_over_the_machine_setting(self):
        # For a shell where the answer differs from this machine's usual one: CI, a
        # customer's laptop, a screen share.
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = self._env(home, SEAMCHECK_TUNNEL="never")
            usersettings.write("tunnel", "always", env)

            wanted, why = usersettings.wants_tunnel(env=env)

            self.assertFalse(wanted)
            self.assertIn("SEAMCHECK_TUNNEL", why)

    def test_the_environment_can_also_turn_it_on(self):
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            wanted, why = usersettings.wants_tunnel(
                env=self._env(home, SEAMCHECK_TUNNEL="always"))

            self.assertTrue(wanted)
            self.assertIn("SEAMCHECK_TUNNEL", why)

    def test_a_value_nobody_defined_is_ignored_rather_than_guessed(self):
        # "maybe" is not a decision to publish somebody's codebase.
        from seamcheck import usersettings

        with tempfile.TemporaryDirectory() as home:
            env = self._env(home, SEAMCHECK_TUNNEL="maybe")

            wanted, _ = usersettings.wants_tunnel(env=env)

            self.assertFalse(wanted)


class ItIsVisibleWhereItIsLookedForTests(SimpleTestCase):
    """`seamcheck config` is where a person goes to ask "why is there no public link".

    Both renderers used to return early when a directory had no project config to show,
    which hid the machine's own setting in exactly the place somebody would look for it -
    and, worse, in the directories most likely to have no config at all.
    """

    def _in_an_empty_directory(self, run):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as empty:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                run(empty)
            return out.getvalue()

    def test_the_plain_renderer_shows_it_with_no_project_config(self):
        from seamcheck.cli import _show_config_plain

        printed = self._in_an_empty_directory(_show_config_plain)

        self.assertIn("public link on this machine", printed)
        self.assertIn("seamcheck config --tunnel", printed)

    def test_the_django_renderer_shows_it_with_no_project_config(self):
        import io

        from seamcheck.management.commands.seamcheck import Command

        command = Command()
        command.stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as empty:
            command._show_config(empty)

        printed = command.stdout.getvalue()
        self.assertIn("public link on this machine", printed)
        self.assertIn("seamcheck config --tunnel", printed)
