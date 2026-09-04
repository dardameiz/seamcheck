"""A planted data layer: one table with a known set of columns.

Not imported by anything. The ORM lens reads it from source, which is the point - it has
to give the same answer in a checkout that cannot be imported.
"""
from django.db import models


class Season(models.Model):
    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)

    class Meta:
        db_table = "fixture_season"
