"""The Postgres half of a Django project, which did not exist.

Tables, columns and queries were read for SQL-schema and Supabase projects. For a Django
project - the stack this tool is built for - it knew 65 model NAMES on the reference
project and nothing else: no table, no column, no query, and every model `uncertain` with
"no ORM-usage extractor yet". So the one question the data layer is for - which table does
this page read, and where - could not be asked at all.
"""
import pathlib
import tempfile
import textwrap

from django.test import SimpleTestCase

from seamcheck.extractors.django_orm_extractor import extract_django_orm


def _project(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(root)


MODELS = """
    from django.db import models

    class Season(models.Model):
        name = models.CharField(max_length=50)
        is_active = models.BooleanField(default=False)
        started_at = models.DateTimeField(null=True)

        class Meta:
            db_table = "pointless_season"

    class Push(models.Model):
        user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
        count = models.IntegerField(default=0)
"""


def _rows(root: str, kind: str) -> dict[str, str]:
    symbols, _ = extract_django_orm(root)
    return {s.label: s.status.value for s in symbols if s.kind == kind}


class TablesAndColumnsTests(SimpleTestCase):
    def test_a_model_is_a_table(self):
        tables = _rows(_project({"app/models.py": MODELS}), "db_table")
        # `Meta.db_table` wins where it is set; otherwise Django's own rule -
        # `<app>_<model>` lowercased - which is what the database actually holds.
        self.assertIn("pointless_season", tables)
        self.assertIn("app_push", tables)

    def test_a_field_is_a_column(self):
        columns = _rows(_project({"app/models.py": MODELS}), "db_column")
        self.assertIn("pointless_season.name", columns)
        self.assertIn("pointless_season.is_active", columns)
        # A ForeignKey is stored as `<name>_id`, which is the column a query names.
        self.assertIn("app_push.user_id", columns)

    def test_an_explicit_db_column_wins(self):
        columns = _rows(_project({"app/models.py": """
            from django.db import models

            class Thing(models.Model):
                label = models.CharField(max_length=10, db_column="the_label")
        """}), "db_column")
        self.assertIn("app_thing.the_label", columns)
        self.assertNotIn("app_thing.label", columns)


class QueriesTests(SimpleTestCase):
    def test_a_queryset_reads_its_table(self):
        symbols, edges = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def current():
                    return Season.objects.filter(is_active=True).first()
            """,
        }))
        uses = [s for s in symbols if s.kind == "db_table_use"]
        self.assertEqual([(s.label, s.status.value) for s in uses],
                         [("pointless_season", "connected")])
        self.assertTrue(any(e.to_id == "db_table:pointless_season" for e in edges))

    def test_a_filter_names_its_columns(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def current():
                    return Season.objects.filter(is_active=True).values("name")
            """,
        }))
        columns = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(columns.get("pointless_season.is_active"), "connected")
        self.assertEqual(columns.get("pointless_season.name"), "connected")

    def test_a_lookup_suffix_is_not_part_of_the_column(self):
        # `started_at__gte` is the column `started_at` and the lookup `gte`. Reading the
        # whole thing as a column name reports every range query as a missing column.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def recent(when):
                    return Season.objects.filter(started_at__gte=when)
            """,
        }))
        columns = {s.label for s in symbols if s.kind == "db_column_use"}
        self.assertIn("pointless_season.started_at", columns)

    def test_a_column_the_model_does_not_have_is_a_finding(self):
        # `values("naem")` is a FieldError the moment that line runs, and nothing else in
        # a source scan catches it.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def broken():
                    return Season.objects.values("naem")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.naem"), "unresolved")

    def test_a_related_lookup_is_not_claimed(self):
        # `user__username` crosses into another model, and this reads one model at a
        # time. Silence beats a claim about a column on a table it did not look at.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Push

                def by_name(name):
                    return Push.objects.filter(user__username=name)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertNotIn("app_push.user__username", rows)
        self.assertNotIn("app_push.username", rows)

    def test_a_table_nothing_queries_is_unused(self):
        tables = _rows(_project({"app/models.py": MODELS}), "db_table")
        self.assertEqual(tables["pointless_season"], "unused")

    def test_the_manager_is_not_required_to_be_called_objects(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Thing(models.Model):
                    live = models.Manager()
                    name = models.CharField(max_length=5)
            """,
            "app/views.py": """
                from app.models import Thing

                def go():
                    return Thing.live.filter(name="x")
            """,
        }))
        uses = {s.label for s in symbols if s.kind == "db_table_use"}
        self.assertIn("app_thing", uses)

    def test_a_name_that_is_not_a_model_is_ignored(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                def go(session):
                    return session.objects.filter(is_active=True)
            """,
        }))
        self.assertEqual([s for s in symbols if s.kind == "db_table_use"], [])


class ChainsTests(SimpleTestCase):
    def test_a_chained_queryset_still_names_its_table(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def go():
                    return Season.objects.filter(is_active=True).exclude(name="x").first()
            """,
        }))
        self.assertIn("pointless_season",
                      {s.label for s in symbols if s.kind == "db_table_use"})

    def test_a_call_that_is_not_an_attribute_chain_ends_the_walk(self):
        # `foo().filter(...)`: the unwrap has nowhere to go, and the version that set
        # `owner = owner` here spun forever - a scan that never returns, which is worse
        # than any wrong answer because nothing says what happened.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                def go(foo):
                    return foo().filter(is_active=True)
            """,
        }))
        self.assertEqual([s for s in symbols if s.kind == "db_table_use"], [])


class NamesDjangoAcceptsTests(SimpleTestCase):
    """The names a queryset may legally use are not the list of columns.

    The first run against the reference project produced 744 `unresolved` columns and
    every one was wrong. Django accepts a relation by its own name as well as its `_id`
    column, gives every model an `id` it never declares, reads `-x` as a direction, and
    lets `annotate(total=…)` invent a name that is not a column at all.
    """

    def test_a_foreign_key_may_be_named_without_id(self):
        # `filter(user=request.user)` is the commonest line in a Django project. Reading
        # it as the column `user` - which is stored as `user_id` - called 463 correct
        # queries broken.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Push

                def mine(user):
                    return Push.objects.filter(user=user)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_push.user_id"), "connected")
        self.assertNotIn("app_push.user", rows)

    def test_every_model_has_an_id_it_never_declares(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def one(n):
                    return Season.objects.filter(id=n).exclude(pk=2)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.id"), "connected")
        # `pk` is an alias for whichever column is the primary key, not a column itself.
        self.assertNotIn("pointless_season.pk", rows)

    def test_an_explicit_primary_key_replaces_the_implicit_id(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Pusher(models.Model):
                    user = models.OneToOneField("auth.User", on_delete=models.PROTECT,
                                                primary_key=True)
            """,
            "app/views.py": """
                from app.models import Pusher

                def one(u):
                    return Pusher.objects.filter(pk=u)
            """,
        }))
        columns = {s.label for s in symbols if s.kind == "db_column"}
        self.assertNotIn("app_pusher.id", columns)
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_pusher.user_id"), "connected")

    def test_an_ordering_direction_is_not_part_of_the_name(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def newest():
                    return Season.objects.order_by("-started_at", "?")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.started_at"), "connected")
        self.assertNotIn("pointless_season.-started_at", rows)
        # `order_by("?")` is "shuffle", and there is no column called `?`.
        self.assertEqual([r for r in rows if r.endswith(".?")], [])

    def test_an_annotation_defines_a_name_rather_than_naming_a_column(self):
        # `aggregate(total=Sum("count"))` names `total`, which no table has and none
        # should - it is the name of the result.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from django.db.models import Sum
                from app.models import Push

                def total():
                    return Push.objects.aggregate(total=Sum("count"))
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertNotIn("app_push.total", rows)
        # The table is still read, which is the part that matters for the map.
        self.assertIn("app_push", {s.label for s in symbols if s.kind == "db_table_use"})

    def test_get_or_create_defaults_is_not_a_column(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def ensure():
                    return Season.objects.get_or_create(name="x", defaults={"count": 0})
            """,
        }))
        rows = {s.label for s in symbols if s.kind == "db_column_use"}
        self.assertIn("pointless_season.name", rows)
        self.assertNotIn("pointless_season.defaults", rows)

    def test_a_many_to_many_is_a_name_but_not_a_column(self):
        # It is stored in a join table, so it is not a column here - but `filter(tags=x)`
        # is valid and must not be called broken.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Rotation(models.Model):
                    tags = models.ManyToManyField("app.Tag")

                class Tag(models.Model):
                    name = models.CharField(max_length=5)
            """,
            "app/views.py": """
                from app.models import Rotation

                def with_tag(t):
                    return Rotation.objects.filter(tags=t)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        # Present and `uncertain` rather than absent: the query is on the map where the
        # reader can see it, and the row says the column is in the join table, which is
        # something to learn. What it must never be is a claim that the name is wrong.
        self.assertEqual(rows.get("app_rotation.tags"), "uncertain")
        self.assertNotIn("app_rotation.tags_id", rows)

    def test_only_the_first_argument_of_dates_is_a_column(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def days():
                    return Season.objects.dates("started_at", "day")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.started_at"), "connected")
        self.assertNotIn("pointless_season.day", rows)

    def test_a_translated_field_is_not_claimed_broken(self):
        # django-modeltranslation adds `title_<lang>` for every language in settings. The
        # columns are real; which languages exist is not in the source, so this is an
        # honest unknown rather than a finding.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Preset(models.Model):
                    title = models.CharField(max_length=10)
            """,
            "app/translation.py": """
                from modeltranslation.translator import translator, TranslationOptions
                from .models import Preset

                class PresetTranslationOptions(TranslationOptions):
                    fields = ('title',)

                translator.register(Preset, PresetTranslationOptions)
            """,
            "app/views.py": """
                from app.models import Preset

                def go():
                    return Preset.objects.update(title_es="hola")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_preset.title_es"), "uncertain")

    def test_a_field_the_model_really_lacks_is_still_a_finding(self):
        # The point of every exemption above is that THIS one survives them.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def go():
                    return Season.objects.create(timezone="UTC")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.timezone"), "unresolved")


class OwnClassTests(SimpleTestCase):
    def test_a_classmethod_querying_its_own_model_counts(self):
        # `cls.objects.all()` inside the model is how a cached config reads itself, and
        # it appears 48 times in the reference project's models.py alone. Not resolving
        # it reported two live tables as "nothing queries this".
        symbols, _ = extract_django_orm(_project({"app/models.py": """
            from django.db import models

            class Promo(models.Model):
                name = models.CharField(max_length=5)

                @classmethod
                def active(cls):
                    return cls.objects.filter(name="x").first()
        """}))
        # The table is READ here and written nowhere, which is its own finding now -
        # what this test is about is that `cls.objects` was resolved at all.
        uses = [s for s in symbols if s.kind == "db_table_use" and s.label == "app_promo"]
        self.assertEqual([s.sub for s in uses], ["reads a table"])
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_promo.name"), "connected")

    def test_cls_outside_a_model_is_not_a_model(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/other.py": """
                class Helper:
                    @classmethod
                    def go(cls):
                        return cls.objects.filter(is_active=True)
            """,
        }))
        self.assertEqual([s for s in symbols if s.kind == "db_table_use"], [])

    def test_an_unmanaged_model_has_no_table_to_call_unused(self):
        # `Meta.managed = False` on an admin-only view model: Django neither creates nor
        # owns the table, so "no queryset reads this table" is not a finding about it.
        symbols, _ = extract_django_orm(_project({"app/models.py": """
            from django.db import models

            class Monitor(models.Model):
                class Meta:
                    db_table = "bot_detection_monitor"
                    managed = False
        """}))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_table"}
        self.assertEqual(rows["bot_detection_monitor"], "uncertain")


class InheritanceTests(SimpleTestCase):
    """A model's base class is where a third of Django's columns come from.

    The reference project's registry holds 65 models and reading only `models.Model`
    subclasses found 61: the four missing were proxies, and `Division.objects.filter(…)`
    - a proxy over the season table - was invisible.
    """

    def test_a_proxy_reads_the_table_it_proxies(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS + """

    class Division(Season):
        class Meta:
            proxy = True
""",
            "app/views.py": """
                from app.models import Division

                def go():
                    return Division.objects.filter(is_active=True)
            """,
        }))
        uses = {s.label for s in symbols if s.kind == "db_table_use"}
        self.assertIn("pointless_season", uses)
        # A proxy is the same table, so it must not appear as a second one.
        tables = [s.label for s in symbols if s.kind == "db_table"]
        self.assertEqual(tables.count("pointless_season"), 1)
        self.assertNotIn("app_division", tables)
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.is_active"), "connected")

    def test_an_abstract_base_gives_its_columns_away(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Stamped(models.Model):
                    created_at = models.DateTimeField(auto_now_add=True)

                    class Meta:
                        abstract = True

                class Note(Stamped):
                    body = models.TextField()
            """,
            "app/views.py": """
                from app.models import Note

                def recent():
                    return Note.objects.order_by("-created_at")
            """,
        }))
        tables = {s.label for s in symbols if s.kind == "db_table"}
        # An abstract base has no table of its own; its children each get a copy.
        self.assertNotIn("app_stamped", tables)
        self.assertIn("app_note", tables)
        columns = {s.label for s in symbols if s.kind == "db_column"}
        self.assertIn("app_note.created_at", columns)
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_note.created_at"), "connected")

    def test_an_inherited_field_is_a_legal_name_on_the_child(self):
        # Multi-table inheritance keeps the parent's fields in the parent's table, so
        # `Child.objects.filter(name=…)` is legal and the column is elsewhere.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Item(models.Model):
                    name = models.CharField(max_length=5)

                class Special(Item):
                    extra = models.IntegerField()
            """,
            "app/views.py": """
                from app.models import Special

                def go():
                    return Special.objects.filter(name="x", extra=1)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_special.extra"), "connected")
        self.assertEqual(rows.get("app_special.name"), "uncertain")


class ThirdPartyBaseTests(SimpleTestCase):
    def test_a_class_in_models_py_with_fields_is_a_model_whatever_its_base(self):
        # `class Thing(SomeLibraryBase)` inherits `models.Model` through a package this
        # never reads. The fields are the evidence: nothing but a Django model declares
        # `models.CharField(...)` at class level in a models.py.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models
                from vendor.base import AuditedModel

                class Thing(AuditedModel):
                    name = models.CharField(max_length=5)
            """,
            "app/views.py": """
                from app.models import Thing

                def go():
                    return Thing.objects.filter(name="x")
            """,
        }))
        self.assertIn("app_thing", {s.label for s in symbols if s.kind == "db_table"})
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_thing.name"), "connected")

    def test_a_plain_class_elsewhere_is_not_a_model(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/forms.py": """
                from django import forms

                class Login(forms.Form):
                    name = forms.CharField(max_length=5)
            """,
        }))
        self.assertNotIn("app_login", {s.label for s in symbols if s.kind == "db_table"})


class ReadingRealModelsTests(SimpleTestCase):
    """Sentry's `Activity` model, and 15,903 claims that came from not reading it.

    Adding data-layer columns to the corpus gate showed the lens claiming 17,080 missing
    columns across 34 projects while the reference project produced 5. Every one of the
    top ten names - `organization_id`, `group`, `user_id`, `project` - was a field the
    model really has, declared in a shape this could not see.
    """

    SENTRY_ISH = """
        from django.db import models

        class Activity(Model):
            project = FlexibleForeignKey("sentry.Project")
            type: models.Field[int, int] = BoundedPositiveIntegerField(choices=())
            ident = models.CharField(max_length=64, null=True)

            class Meta:
                app_label = "sentry"
                db_table = "sentry_activity"
    """

    def test_an_annotated_field_is_still_a_field(self):
        # `type: models.Field[int, int] = BoundedPositiveIntegerField(...)` is an
        # AnnAssign, not an Assign, and reading only Assign skipped it entirely.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": self.SENTRY_ISH,
            "app/views.py": """
                def go(Activity):
                    return Activity.objects.filter(type=1)
            """,
        }))
        columns = {s.label for s in symbols if s.kind == "db_column"}
        self.assertIn("sentry_activity.type", columns)
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("sentry_activity.type"), "connected")

    def test_a_relation_field_from_a_library_is_still_a_relation(self):
        # `FlexibleForeignKey` and `HybridCloudForeignKey` are sentry's own; the name is
        # the evidence, and `project` is stored in `project_id` either way.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": self.SENTRY_ISH,
            "app/views.py": """
                def go(Activity, p):
                    return Activity.objects.filter(project=p)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("sentry_activity.project_id"), "connected")

    def test_meta_app_label_names_the_table(self):
        symbols, _ = extract_django_orm(_project({"deep/nested/models.py": """
            from django.db import models

            class Thing(models.Model):
                name = models.CharField(max_length=5)

                class Meta:
                    app_label = "billing"
        """}))
        self.assertIn("billing_thing", {s.label for s in symbols if s.kind == "db_table"})

    def test_a_model_this_could_not_fully_read_never_claims_a_missing_column(self):
        # The safety net under every rule above: a base class from a package this never
        # opened may declare any number of fields, so "no such column" is not something
        # it is in a position to say.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models
                from vendor.base import AuditedModel

                class Thing(AuditedModel):
                    name = models.CharField(max_length=5)
            """,
            "app/views.py": """
                from app.models import Thing

                def go():
                    return Thing.objects.filter(audited_at=1, name="x")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_thing.audited_at"), "uncertain")
        # The fields it COULD read still connect - the doubt is about what it could not.
        self.assertEqual(rows.get("app_thing.name"), "connected")

    def test_two_models_with_the_same_name_do_not_answer_for_each_other(self):
        # 383 class names are defined twice in sentry. Keying by the bare name let the
        # last file walked answer for all of them - a claim about one table built from
        # another table's fields.
        symbols, _ = extract_django_orm(_project({
            "billing/models.py": """
                from django.db import models

                class Item(models.Model):
                    price = models.IntegerField()
            """,
            "store/models.py": """
                from django.db import models

                class Item(models.Model):
                    label = models.CharField(max_length=5)
            """,
            "app/views.py": """
                def go(Item):
                    return Item.objects.filter(price=1)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual([r for r, v in rows.items() if v == "unresolved"], [])

    def test_a_query_beside_its_own_model_still_resolves(self):
        symbols, _ = extract_django_orm(_project({
            "billing/models.py": """
                from django.db import models

                class Item(models.Model):
                    price = models.IntegerField()

                def cheap():
                    return Item.objects.filter(price=1)
            """,
            "store/models.py": """
                from django.db import models

                class Item(models.Model):
                    label = models.CharField(max_length=5)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("billing_item.price"), "connected")


class AnnotationAliasTests(SimpleTestCase):
    def test_a_name_annotate_defined_is_legal_further_down_the_chain(self):
        # `.annotate(m=Subquery(...)).values("m")` - pretix does this 59 times, and every
        # one was reported as a column its model does not have. The alias IS the name,
        # and it exists because two lines earlier something made it.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from django.db.models import Count
                from app.models import Season

                def report():
                    return (Season.objects
                            .annotate(m=Count("id"))
                            .values("m")
                            .filter(m__gte=2))
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.m"), "uncertain")
        self.assertNotIn("unresolved", set(rows.values()))


class ReverseRelationTests(SimpleTestCase):
    def test_the_other_end_of_a_relation_is_a_name_too(self):
        # `WorkflowActionEmail.objects.filter(action=None)` in paperless-ngx: `action` is
        # the related_name of somebody else's ForeignKey pointing HERE. Reading only the
        # fields a model declares itself sees one end of every relation.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Email(models.Model):
                    address = models.CharField(max_length=50)

                class Action(models.Model):
                    email = models.ForeignKey(Email, on_delete=models.CASCADE,
                                              related_name="action")
            """,
            "app/views.py": """
                from app.models import Email

                def orphans():
                    return Email.objects.filter(action=None)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_email.action"), "uncertain")
        self.assertNotIn("unresolved", set(rows.values()))

    def test_a_relation_with_no_related_name_still_has_its_default(self):
        # Django's default reverse query name is the source model, lowercased.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Email(models.Model):
                    address = models.CharField(max_length=50)

                class Action(models.Model):
                    email = models.ForeignKey("app.Email", on_delete=models.CASCADE)
            """,
            "app/views.py": """
                from app.models import Email

                def used():
                    return Email.objects.filter(action__isnull=False)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertNotIn("unresolved", set(rows.values()))


class HistoricalModelTests(SimpleTestCase):
    def test_a_model_fetched_at_runtime_is_not_the_class_this_read(self):
        # `apps.get_model("paperless", "ApplicationConfiguration")` in a migration test
        # is the model as it was at migration 0007, with fields the current class no
        # longer has. The bare name looks identical to an import and is not one.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/tests.py": """
                def before(apps):
                    Season = apps.get_model("app", "Season")
                    Season.objects.create(retired_field="x")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.retired_field"), "uncertain")
        # The table is still read, and that is the part the map draws.
        self.assertIn("pointless_season",
                      {s.label for s in symbols if s.kind == "db_table_use"})


class PropertiesAndSharedAliasesTests(SimpleTestCase):
    def test_a_property_is_a_legal_keyword(self):
        # `Invoice.objects.create(url=…)` where `url` is a read-only @property does NOT
        # raise: Django pops any kwarg that names a property, and a missing setter is
        # swallowed. Saleor does this six times, and calling it a FieldError is a claim
        # about a line that runs fine.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Invoice(models.Model):
                    external_url = models.URLField(null=True)

                    @property
                    def url(self):
                        return self.external_url
            """,
            "app/tests.py": """
                from app.models import Invoice

                def make():
                    return Invoice.objects.create(url="http://example.com")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_invoice.url"), "uncertain")

    def test_an_annotation_from_a_custom_queryset_counts_anywhere(self):
        # `Stock.objects.annotate_reserved_quantity()` is a queryset method in another
        # file; the alias it makes is read three files away. Scoping aliases to one file
        # made saleor's `reserved_quantity` and `available_quantity` look missing.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models
                from django.db.models import Sum

                class StockQuerySet(models.QuerySet):
                    def with_reserved(self):
                        return self.annotate(reserved_quantity=Sum("quantity"))

                class Stock(models.Model):
                    quantity = models.IntegerField()
            """,
            "app/loaders.py": """
                from app.models import Stock

                def load():
                    return Stock.objects.values_list("reserved_quantity")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_stock.reserved_quantity"), "uncertain")


class LibraryBasesTests(SimpleTestCase):
    def test_a_base_this_only_knows_by_name_leaves_the_model_incomplete(self):
        # `TimeStampedModel` is recognised as a model root so the class counts as a
        # model - but its `created` and `modified` live in a package this never opens,
        # and readthedocs' `filter(created=…)` was called a missing column.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django_extensions.db.models import TimeStampedModel
                from django.db import models

                class BuildData(TimeStampedModel):
                    build = models.IntegerField()
            """,
            "app/tasks.py": """
                from app.models import BuildData

                def old(when):
                    return BuildData.objects.filter(created__lt=when)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_builddata.created"), "uncertain")

    def test_a_money_field_also_stores_its_currency(self):
        # django-money writes two columns per field: `price` and `price_currency`.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from djmoney.models.fields import MoneyField
                from django.db import models

                class Break(models.Model):
                    price = MoneyField(max_digits=19, decimal_places=6)
            """,
            "app/tests.py": """
                from app.models import Break

                def make():
                    return Break.objects.create(price=1, price_currency="USD")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_break.price_currency"), "connected")

    def test_a_relation_answers_to_name_id_even_with_a_custom_column(self):
        # pretix: `organizer = ForeignKey(..., db_column='organizer_link_id')`. The
        # column is `organizer_link_id`; the names Django accepts are still `organizer`
        # and `organizer_id`, because the attname does not follow db_column.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class LogEntry(models.Model):
                    organizer = models.ForeignKey("app.Organizer", null=True,
                                                  on_delete=models.PROTECT,
                                                  db_column="organizer_link_id")

                class Organizer(models.Model):
                    name = models.CharField(max_length=5)
            """,
            "app/tasks.py": """
                from app.models import LogEntry

                def go():
                    return LogEntry.objects.order_by("organizer_id")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("app_logentry.organizer_link_id"), "connected")
        self.assertNotIn("app_logentry.organizer_id", rows)


class TablePairingTests(SimpleTestCase):
    """A table read and written is wired. One only written is not, and should say so.

    `connected` meant "some queryset mentions this table", which put 59 of the reference
    project's 61 tables in the same bucket and asked nobody to look at anything. Redis has
    paired writes against reads from the start; a table is the same question, and the
    answer is the same shape: **written and never read** is a table filled for nobody.
    """

    SHOP = """
        from django.db import models

        class Tier(models.Model):
            config = models.ForeignKey("app.Config", on_delete=models.CASCADE,
                                       related_name="tier_set")
            bonus = models.IntegerField()

        class Config(models.Model):
            name = models.CharField(max_length=5)

        class Ledger(models.Model):
            amount = models.IntegerField()

        class Archive(models.Model):
            note = models.CharField(max_length=5)
    """

    def _tables(self, files):
        symbols, _ = extract_django_orm(_project(files))
        return {s.label: (s.status.value, s.sub) for s in symbols if s.kind == "db_table"}

    def test_a_table_written_and_never_read_says_so(self):
        rows = self._tables({
            "app/models.py": self.SHOP,
            "app/seed.py": """
                from app.models import Ledger

                def seed():
                    Ledger.objects.create(amount=1)
                    Ledger.objects.filter(amount=0).delete()
            """,
        })
        self.assertEqual(rows["app_ledger"][0], "unused")
        self.assertIn("write", rows["app_ledger"][1])

    def test_a_table_read_and_written_is_connected(self):
        rows = self._tables({
            "app/models.py": self.SHOP,
            "app/views.py": """
                from app.models import Ledger

                def show():
                    Ledger.objects.create(amount=1)
                    return Ledger.objects.filter(amount=1).first()
            """,
        })
        self.assertEqual(rows["app_ledger"][0], "connected")
        self.assertEqual(rows["app_ledger"][1], "1 write / 1 read")

    def test_a_reverse_accessor_is_a_read_of_the_table_it_returns(self):
        # `self.tier_set.order_by(...)` on the config returns TIER rows. This is how the
        # reference project reads its bonus tiers, and reading only `Model.objects` saw
        # those tables written by a seeding command and read by nobody.
        rows = self._tables({
            "app/models.py": """
                from django.db import models

                class Tier(models.Model):
                    config = models.ForeignKey("app.Config", on_delete=models.CASCADE,
                                               related_name="tier_set")
                    bonus = models.IntegerField()

                class Config(models.Model):
                    name = models.CharField(max_length=5)

                    def tiers(self):
                        return self.tier_set.order_by('bonus').values_list('bonus')
            """,
            "app/seed.py": """
                from app.models import Tier

                def seed(config):
                    Tier.objects.bulk_create([Tier(config=config, bonus=1)])
            """,
        })
        self.assertEqual(rows["app_tier"], ("connected", "1 write / 1 read"))

    def test_select_related_reads_the_table_it_names(self):
        # A join IS a read of the table at the other end, which is the whole point of
        # `select_related`. It used to be skipped entirely.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": self.SHOP,
            "app/views.py": """
                from app.models import Tier

                def rows():
                    return Tier.objects.select_related("config").all()
            """,
        }))
        joined = [s for s in symbols
                  if s.kind == "db_table_use" and s.label == "app_config"]
        self.assertEqual([(s.sub, s.snippet) for s in joined],
                         [("reads a table", 'select_related("config")')])

    def test_a_table_only_the_admin_writes_is_not_called_unwritten(self):
        # An admin-registered model is written by a person through the admin, at runtime.
        # Reading it and never writing it in code is exactly how config tables work.
        rows = self._tables({
            "app/models.py": self.SHOP,
            "app/admin.py": """
                from django.contrib import admin

                from .models import Config

                admin_site.register(Config, ConfigAdmin)
            """,
            "app/views.py": """
                from app.models import Config

                def current():
                    return Config.objects.filter(name="x").first()
            """,
        })
        self.assertEqual(rows["app_config"][0], "connected")
        self.assertIn("admin", (rows["app_config"][1] or "").lower())

    def test_a_table_read_and_written_by_nothing_and_nobody_is_the_claim(self):
        rows = self._tables({"app/models.py": self.SHOP})
        self.assertEqual(rows["app_archive"][0], "unused")

    def test_a_reverse_name_two_models_share_answers_for_neither(self):
        # saleor declares `related_name="lines"` on OrderLine, FulfillmentLine and more.
        # Keeping the first one seen made `fulfillment.lines.create(...)` a write to the
        # ORDER line table, and checked its keywords against the wrong model's fields:
        # 609 invented missing columns on that project alone.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Order(models.Model):
                    ref = models.CharField(max_length=5)

                class Fulfillment(models.Model):
                    ref = models.CharField(max_length=5)

                class OrderLine(models.Model):
                    order = models.ForeignKey(Order, on_delete=models.CASCADE,
                                              related_name="lines")
                    quantity = models.IntegerField()

                class FulfillmentLine(models.Model):
                    fulfillment = models.ForeignKey(Fulfillment, on_delete=models.CASCADE,
                                                    related_name="lines")
                    stock = models.IntegerField()
            """,
            "app/views.py": """
                def make(fulfillment, line):
                    return fulfillment.lines.create(order_line=line, stock=1)
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual([r for r, v in rows.items() if v == "unresolved"], [],
                         "an ambiguous accessor must not claim anything")
        self.assertEqual([s.label for s in symbols if s.kind == "db_table_use"], [],
                         "nor attribute the write to whichever model was seen first")

    def test_a_method_in_the_middle_of_a_chain_ends_the_walk(self):
        # `request.profile.checks_from_all_projects().only("code")` - the method returns
        # a queryset of a DIFFERENT model, and unwrapping calls blindly walked straight
        # past it to `profile`, then checked `code` against the profile's fields.
        symbols, _ = extract_django_orm(_project({
            "app/models.py": """
                from django.db import models

                class Profile(models.Model):
                    user = models.OneToOneField("auth.User", on_delete=models.CASCADE,
                                                related_name="profile")

                class Check(models.Model):
                    code = models.CharField(max_length=8)
            """,
            "app/views.py": """
                def uncloak(request):
                    return request.profile.checks_from_all_projects().only("code")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual([r for r, v in rows.items() if v == "unresolved"], [])

    def test_a_queryset_chain_still_walks(self):
        symbols, _ = extract_django_orm(_project({
            "app/models.py": MODELS,
            "app/views.py": """
                from app.models import Season

                def go():
                    return Season.objects.filter(is_active=True).exclude(name="x").only("name")
            """,
        }))
        rows = {s.label: s.status.value for s in symbols if s.kind == "db_column_use"}
        self.assertEqual(rows.get("pointless_season.name"), "connected")
