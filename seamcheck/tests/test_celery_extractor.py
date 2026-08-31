"""Celery: code reached with no HTTP request, and the outage class that follows.

A task is defined in one file, scheduled by a STRING in another, and called by a third.
Nothing checks the string matches anything, and when it does not the scheduler raises where
nobody is looking and the job simply never runs. The reference project lost challenges and
season rollover for fourteen days that way.

The asymmetry is the point. A task with no caller is `uncertain` - it may be sent by name
from another service. A beat entry naming a task that does not exist is a FINDING.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.celery_extractor import extract_celery
from seamcheck.graph import Status


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _by_id(root: str):
    symbols, edges = extract_celery(root)
    return {s.id: s for s in symbols}, edges


TASKS = ("from celery import shared_task\n\n"
         "@shared_task\ndef send_receipt(order_id):\n    pass\n")


class Tasks(unittest.TestCase):
    def test_a_task_with_a_caller_is_connected(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "views.py": "from tasks import send_receipt\nsend_receipt.delay(1)\n",
        }))
        self.assertEqual(symbols["celery_task:tasks.send_receipt"].status, Status.CONNECTED)

    def test_apply_async_counts_as_a_caller(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "views.py": "send_receipt.apply_async(args=[1])\n",
        }))
        self.assertEqual(symbols["celery_task:tasks.send_receipt"].status, Status.CONNECTED)

    def test_a_task_with_no_caller_is_uncertain_not_unused(self):
        """It may be sent by name from another service, or run by an operator."""
        symbols, _ = _by_id(_repo({"tasks.py": TASKS}))
        task = symbols["celery_task:tasks.send_receipt"]
        self.assertEqual(task.status, Status.UNCERTAIN)
        self.assertIn("no caller here", task.note)

    def test_an_explicit_name_is_the_task_name(self):
        symbols, _ = _by_id(_repo({"tasks.py":
            "from celery import shared_task\n"
            "@shared_task(name='billing.send_receipt')\ndef send_receipt():\n    pass\n"}))
        self.assertIn("celery_task:billing.send_receipt", symbols)


class Schedules(unittest.TestCase):
    def test_a_beat_entry_naming_a_missing_task_is_a_finding(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "settings.py": "CELERY_BEAT_SCHEDULE = {'nightly': "
                           "{'task': 'billing.tasks.nope', 'schedule': 3600}}\n",
        }))
        entry = symbols["celery_schedule:billing.tasks.nope"]
        self.assertEqual(entry.status, Status.UNRESOLVED)
        self.assertIn("silently never runs", entry.note)

    def test_a_beat_entry_naming_a_real_task_connects_to_it(self):
        symbols, edges = _by_id(_repo({
            "tasks.py": "from celery import shared_task\n"
                        "@shared_task(name='billing.send_receipt')\ndef send_receipt():\n    pass\n",
            "settings.py": "CELERY_BEAT_SCHEDULE = {'n': "
                           "{'task': 'billing.send_receipt', 'schedule': 60}}\n",
        }))
        self.assertEqual(symbols["celery_schedule:billing.send_receipt"].status, Status.CONNECTED)
        self.assertTrue(edges, "the schedule should reach the task")

    def test_scheduling_a_task_counts_as_reaching_it(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": "from celery import shared_task\n"
                        "@shared_task(name='billing.send_receipt')\ndef send_receipt():\n    pass\n",
            "settings.py": "CELERY_BEAT_SCHEDULE = {'n': "
                           "{'task': 'billing.send_receipt', 'schedule': 60}}\n",
        }))
        self.assertEqual(symbols["celery_task:billing.send_receipt"].status, Status.CONNECTED)

    def test_send_task_by_name_is_read(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "views.py": "app.send_task('tasks.missing_one')\n",
        }))
        self.assertEqual(symbols["celery_schedule:tasks.missing_one"].status, Status.UNRESOLVED)


class WhatIsNotASchedule(unittest.TestCase):
    def test_a_dict_with_a_task_key_but_no_schedule_is_not_a_beat_entry(self):
        """The reference project writes an audit record shaped exactly like this."""
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "audit.py": "r.hset(key, mapping={'item_id': 1, 'redis_cleaned': 3, "
                        "'task': 'cascade_redis_cleanup'})\n",
        }))
        self.assertNotIn("celery_schedule:cascade_redis_cleanup", symbols)

    def test_a_test_fixture_is_not_a_scheduled_job(self):
        symbols, _ = _by_id(_repo({
            "tasks.py": TASKS,
            "tests/test_health.py": "SCHEDULE = {'x': {'task': 'a.b.c', 'schedule': 1}}\n",
        }))
        self.assertNotIn("celery_schedule:a.b.c", symbols)

    def test_a_project_with_no_celery_at_all_claims_nothing(self):
        self.assertEqual(extract_celery(_repo({"a.py": "print(1)\n"})), ([], []))


if __name__ == "__main__":
    unittest.main()
