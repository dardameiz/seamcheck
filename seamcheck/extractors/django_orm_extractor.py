"""The Postgres half of a Django project: tables, columns, and the queries that touch them.

This did not exist. Tables and columns were read for SQL-schema and Supabase projects, and
for a Django project - the stack this tool is built for - the scan knew model NAMES and
nothing else: 65 of them on the reference project, every one `uncertain`, with the note
"no ORM-usage extractor yet". So the question the data layer exists to answer - which
table does this page read, and where - could not be asked at all on Django.

Read from source, not from a running Django. `manage.py seamcheck` has the app registry to
hand, but `seamcheck scan` in a checkout does not, and a lens that only works one of those
two ways is a lens most people never see. Django's own naming rules are simple enough to
follow exactly: `Meta.db_table` when it is set, `<app>_<model>` lowercased otherwise, and a
ForeignKey stored as `<name>_id`.

What it deliberately does NOT do: follow a relation. `Push.objects.filter(user__username=…)`
crosses into another model, and this reads one model at a time - so it says nothing rather
than reporting a column on a table it never looked at.
"""
from __future__ import annotations

import ast
import os

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.pyscope import owners_of

_SKIP = set(SKIP_DIRS) | {"migrations"}

# Queryset methods whose keyword arguments name columns, and those whose string arguments
# do. `filter(is_active=True)` and `values("name")` are the same question asked twice.
_KWARG_METHODS = frozenset({
    "filter", "exclude", "get", "get_or_create", "update_or_create", "create", "update",
})
# `aggregate(total=Sum("count"))` DEFINES the name `total`. It is the name of the result,
# not a column, and no table has it - reading these as columns reported 34 correct
# aggregations as missing columns on the reference project.
_DEFINING_METHODS = frozenset({"annotate", "aggregate", "alias"})
_STRING_METHODS = frozenset({
    "values", "values_list", "only", "defer", "order_by", "distinct",
    "select_related", "prefetch_related", "dates", "datetimes",
})
# `get_or_create(name=…, defaults={…})`: `defaults` is how the call works, not a field.
_CONTROL_KWARGS = frozenset({"defaults", "using", "output_field", "through_defaults"})
# Everything that says "this queryset ran": the table is touched even when no column is
# named, which is most reads.
# What puts a row in, or takes one out. Everything else on a queryset reads.
_TABLE_WRITES = frozenset({
    "create", "get_or_create", "update_or_create", "update", "delete",
    "bulk_create", "bulk_update", "select_for_update",
})
_QUERYSET_METHODS = _KWARG_METHODS | _DEFINING_METHODS | _STRING_METHODS | frozenset({
    "all", "first", "last", "count", "exists", "delete", "bulk_create", "bulk_update",
    "iterator", "in_bulk", "latest", "earliest", "none", "select_for_update",
})

# `is_active=True` is the column `is_active`; `started_at__gte=x` is the column
# `started_at` and a lookup. Reading the whole thing as a column reports every range query
# as a missing one.
_LOOKUPS = frozenset({
    "exact", "iexact", "contains", "icontains", "in", "gt", "gte", "lt", "lte",
    "startswith", "istartswith", "endswith", "iendswith", "range", "date", "year",
    "iso_year", "month", "day", "week", "week_day", "iso_week_day", "quarter", "time",
    "hour", "minute", "second", "isnull", "regex", "iregex", "unaccented", "overlap",
    "contained_by", "len", "trigram_similar", "search",
})


class _Model:
    """A table, its columns, and - separately - the names a query may legally use.

    The two are not the same list, and treating them as one is what made every correct
    query on the reference project look broken. A ForeignKey `user` is stored in a column
    called `user_id` and may be named either way; `pk` is an alias for whichever column is
    the primary key; a ManyToMany is a legal name with no column on this table at all.
    """

    __slots__ = ("table", "columns", "names", "translated", "file", "line", "managers",
                 "managed", "bases", "proxy", "abstract", "own_table", "_settled",
                 "declares_fields", "fully_read", "reverse", "relations")

    def __init__(self, table: str, file: str, line: int) -> None:
        self.table = table
        self.columns: dict[str, tuple[str, int]] = {}
        # query name -> the column it means, or "" for a name that is legal here but
        # stored elsewhere (a ManyToMany lives in its own join table).
        self.names: dict[str, str] = {}
        self.translated: set[str] = set()
        self.file = file
        self.line = line
        self.managers: set[str] = {"objects"}
        self.managed = True
        self.bases: list[str] = []
        self.proxy = False
        self.abstract = False
        # True once `Meta.db_table` was read, so inheritance never overwrites it.
        self.own_table = False
        self._settled = False
        self.declares_fields = False
        # False once something at class level could not be named. An unread base or an
        # unrecognised field constructor may hide any number of columns, and a model
        # whose fields are not all known is in no position to call one missing.
        self.fully_read = True
        # (target model name, the name that target may be queried BY). The other end of
        # a relation is a legal name on a model that never declares it.
        self.reverse: list[tuple[str, str]] = []
        # field name -> the model it points at, so `select_related("config")` can be
        # read as what it is: a read of the config table.
        self.relations: dict[str, str] = {}

    def column_for(self, name: str) -> tuple[bool, str]:
        """(is this name legal, which column it means)."""
        if name in self.names:
            return True, self.names[name]
        if name in self.columns:
            return True, name
        # `title_es` on a model registered with django-modeltranslation. The column is
        # real; which languages exist lives in settings, not here.
        if any(name.startswith(f"{field}_") for field in self.translated):
            return True, ""
        return False, ""


def _string(node) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _keyword_node(call: ast.Call, name: str):
    for word in call.keywords:
        if word.arg == name:
            return word.value
    return None


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _keyword(call: ast.Call, name: str) -> str:
    for word in call.keywords:
        if word.arg == name:
            return _string(word.value)
    return ""


def _is_field(node: ast.Call) -> str:
    """The Django field class this call constructs, or "" - `models.CharField(…)`."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _target_of(call: ast.Call) -> str:
    """The model a relation points at: `ForeignKey(Email, …)`, `ForeignKey("app.Email")`.

    The app prefix is dropped - this resolves by class name, the same way a query does.
    """
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Name):
        return first.id
    named = _string(first)
    return named.rpartition(".")[2] if named else ""


def _is_relation(kind: str) -> bool:
    """A field that stores a foreign key, whoever wrote the class.

    Sentry's models are built from `FlexibleForeignKey` and `HybridCloudForeignKey`;
    django-extensions, wagtail and half the ecosystem ship their own. Matching only
    Django's three names left `organization_id`, `user_id` and `project` - the three
    commonest columns in that project - looking like fields nobody declared.
    """
    return "ForeignKey" in kind or "OneToOne" in kind


def _column_of(name: str, call: ast.Call, kind: str) -> str:
    """The column a field is stored in: its own `db_column`, else the field name, and
    `_id` for a relation because that is what the database holds and what a query names."""
    explicit = _keyword(call, "db_column")
    if explicit:
        return explicit
    if _is_relation(kind):
        return f"{name}_id"
    return name


def _app_label(path: str, root: str) -> str:
    """Django's default: the app directory's own name."""
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    parts = [p for p in relative.split("/") if p and not p.endswith(".py")]
    return parts[-1] if parts else "app"


def _assignments(body: list) -> list[tuple[str, ast.AST, int]]:
    """(name, value, line) for every `x = …` in a class body, annotated or not.

    `type: models.Field[int, int] = BoundedPositiveIntegerField(...)` is an AnnAssign,
    and reading only Assign skipped it - which on sentry meant skipping most of the
    fields on most of the models.
    """
    out = []
    for item in body:
        if isinstance(item, ast.Assign) and item.targets:
            name = getattr(item.targets[0], "id", "")
        elif isinstance(item, ast.AnnAssign):
            name = getattr(item.target, "id", "")
        else:
            continue
        if name and item.value is not None:
            out.append((name, item.value, item.lineno))
    return out


def _models_in(tree: ast.AST, path: str, root: str) -> dict[str, _Model]:
    found: dict[str, _Model] = {}
    label = _app_label(path, root)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [
            base.attr if isinstance(base, ast.Attribute) else
            (base.id if isinstance(base, ast.Name) else "")
            for base in node.bases
        ]
        # Anything at all may turn out to be a model: a proxy, or a child of an abstract
        # base, inherits `models.Model` through its parent and never names it. Which of
        # these classes are models is settled in a second pass, once every file is read.
        model = _Model(f"{label}_{node.name.lower()}", path, node.lineno)
        model.bases = [base for base in bases if base]
        declared_pk = ""
        for item in node.body:
            if not isinstance(item, ast.ClassDef) or item.name != "Meta":
                continue
            for name, value, _line in _assignments(item.body):
                if name == "db_table" and _string(value):
                    model.table = _string(value)
                    model.own_table = True
                elif name == "app_label" and _string(value) and not model.own_table:
                    # Django's table name is `<app_label>_<model>`, and the app label is
                    # not always the directory: sentry keeps 900 models under one label.
                    model.table = f"{_string(value)}_{node.name.lower()}"
                elif isinstance(value, ast.Constant) and value.value is False:
                    if name == "managed":
                        model.managed = False
                elif isinstance(value, ast.Constant) and value.value is True:
                    if name == "proxy":
                        model.proxy = True
                    elif name == "abstract":
                        model.abstract = True
        for item in node.body:
            # `create(url=…)` where `url` is a @property does not raise: Django pops any
            # kwarg naming a property and swallows a missing setter. It writes nothing,
            # which is worth knowing - but it is not the FieldError this would claim.
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    getattr(d, "id", getattr(d, "attr", "")) in ("property", "cached_property")
                    for d in item.decorator_list):
                model.names.setdefault(item.name, "")
        for name, value, line in _assignments(node.body):
            if not isinstance(value, ast.Call):
                continue
            kind = _is_field(value)
            if kind.endswith("Manager") or kind.endswith("QuerySet"):
                model.managers.add(name)
                continue
            # A ManyToMany is stored in its own join table, so it is not a column here -
            # but `filter(tags=…)` is perfectly good Django and must not be called broken.
            if "ManyToMany" in kind:
                model.declares_fields = True
                model.names[name] = ""
                target = _target_of(value)
                if target:
                    model.relations[name] = target
                if target:
                    model.reverse.append(
                        (target, _keyword(value, "related_name") or node.name.lower()))
                continue
            if not (kind.endswith("Field") or _is_relation(kind)):
                # Something at class level this cannot name. It may well be a field -
                # `history = HistoricalRecords()` adds several - so the model is no
                # longer safe to make claims about.
                model.fully_read = False
                continue
            model.declares_fields = True
            if _is_relation(kind) or "ManyToMany" in kind:
                target = _target_of(value)
                if target:
                    model.relations[name] = target
                if target:
                    # `related_name="action"` if given, else Django's default reverse
                    # query name, which is the source model lowercased.
                    model.reverse.append(
                        (target, _keyword(value, "related_name") or node.name.lower()))
            column = _column_of(name, value, kind)
            model.columns[column] = (path, line)
            if "Money" in kind:
                # django-money writes two columns per field: the amount and the currency
                # it is in. Nothing else in the source says the second one exists.
                model.columns.setdefault(f"{column}_currency", (path, line))
            # A relation answers to both `user` and `user_id`, and to BOTH even when
            # `db_column` renames the column underneath - Django's attname is always
            # `<field>_id`, and it does not follow db_column.
            if _is_relation(kind):
                model.names[name] = column
                model.names[f"{name}_id"] = column
            elif column != name:
                model.names[name] = column
            if _is_true(_keyword_node(value, "primary_key")):
                declared_pk = column
        # Django adds an `id` to every model that did not declare its own primary key,
        # and `pk` always means whichever column that is. Neither is in the source, and
        # missing them called 119 correct queries broken.
        if declared_pk:
            model.names["pk"] = declared_pk
        else:
            model.columns.setdefault("id", (path, node.lineno))
            model.names["pk"] = "id"
        found[node.name] = model
    return found


# Bases that mean "this is a model". Only `Model` is Django's own and empty; the rest
# belong to packages this never opens and each brings fields of its own (`created`,
# `modified`, `deleted_at`), so a model built on one is not fully read.
_ROOTS = ("Model", "TimeStampedModel", "SafeDeleteModel")


def _pick(name: str, file: str, per_file: dict[str, dict[str, _Model]],
          unique: dict[str, _Model]) -> _Model | None:
    """The class a name means, seen from `file`: the one beside it, else the one the
    whole project agrees on. A name two apps both use resolves to neither."""
    beside = per_file.get(file, {}).get(name)
    return beside if beside is not None else unique.get(name)


def _resolve_inheritance(
    per_file: dict[str, dict[str, _Model]], unique: dict[str, _Model],
) -> list[tuple[str, _Model]]:
    """Decide which classes are models, and settle what each one inherits.

    Three shapes, and each answers "where does this column live" differently:

    * **abstract** - no table of its own; every child gets its own copy of the columns.
    * **proxy** - the parent's table, exactly; a second table would be a fiction.
    * **multi-table** - its own table, and the parent's fields stay in the PARENT's
      table. `Special.objects.filter(name=…)` is legal and `name` is not a column here,
      which is the "legal name, column elsewhere" case the ManyToMany already uses.
    """
    def is_model(name: str, model: _Model, seen: frozenset[str] = frozenset()) -> bool:
        if model is None or name in seen:
            return False
        # A class in a models.py that declares Django fields is a model even when its
        # base comes from a package this never reads - which is the only thing asking
        # Django's own registry could still tell us, and it needed django-extensions,
        # a subprocess, and a project that imports cleanly to say it.
        if model.declares_fields and os.path.basename(model.file) == "models.py":
            return True
        for base_name in model.bases:
            if base_name in _ROOTS:
                return True
            base = _pick(base_name, model.file, per_file, unique)
            if base is not None and is_model(base_name, base, seen | {name}):
                return True
        return False

    models = [(name, m) for holds in per_file.values() for name, m in holds.items()
              if is_model(name, m)]
    kept_files: dict[str, dict[str, _Model]] = {}
    for name, model in models:
        kept_files.setdefault(model.file, {})[name] = model
    kept_unique = {}
    seen_names: set[str] = set()
    for name, model in models:
        if name in seen_names:
            kept_unique.pop(name, None)
        else:
            seen_names.add(name)
            kept_unique[name] = model

    def settle(name: str, model: _Model, seen: frozenset[str] = frozenset()) -> None:
        if name in seen or model._settled:  # noqa: SLF001
            return
        model._settled = True  # noqa: SLF001
        for base_name in model.bases:
            base = _pick(base_name, model.file, kept_files, kept_unique)
            if base is None:
                # A base from a package this never opened. It may declare anything -
                # and `Model` itself is the one that declares nothing.
                if base_name != "Model":
                    model.fully_read = False
                continue
            settle(base_name, base, seen | {name})
            model.fully_read = model.fully_read and base.fully_read
            if base.abstract:
                # The child stores these itself, so they are its own columns.
                for column, where in base.columns.items():
                    model.columns.setdefault(column, where)
                for query_name, column in base.names.items():
                    model.names.setdefault(query_name, column)
                model.managers |= base.managers
            elif model.proxy:
                if not model.own_table:
                    model.table = base.table
                model.columns = dict(base.columns)
                model.names = dict(base.names)
                model.managers |= base.managers
            else:
                # Legal to name, stored in the parent's table.
                for query_name in list(base.columns) + list(base.names):
                    model.names.setdefault(query_name, "")

    for name, model in models:
        settle(name, model)
    return models


def _annotation_names(tree: ast.AST) -> set[str]:
    """Every name `annotate()`, `alias()` or `aggregate()` invents in this file.

    `.annotate(m=Subquery(...)).values("m")` is one queryset, and `m` is a real name on
    it that belongs to no table. Reading `values("m")` as a column produced 59 claims on
    pretix alone, every one about a name the line above had just created.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _DEFINING_METHODS):
            names |= {word.arg for word in node.keywords if word.arg}
    return names


def _locally_bound(tree: ast.AST) -> set[str]:
    """Names this file assigns to - so a receiver that looks like a model class is not.

    `Season = apps.get_model("app", "Season")` in a migration test is the model as it
    was at that migration, with fields the current class no longer has. The name reads
    exactly like an import and is a different object.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _model_bodies(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(first line, last line, class name) for every class in a file.

    `cls.objects.all()` inside a model's own classmethod is how a cached config reads
    itself. Without knowing which class the line sits in, the receiver is the bare word
    `cls` and the query resolves to nothing.
    """
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            spans.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name))
    # Innermost first, so a nested class wins over the one containing it.
    return sorted(spans, key=lambda s: s[1] - s[0])


def _admin_registered(trees: list[tuple[str, ast.AST]]) -> set[str]:
    """Models registered in the Django admin.

    An admin-registered table is written by a PERSON, at runtime, through a form this
    scan will never see. Reading such a table and never writing it in code is not a
    finding - it is how every config table in every Django project works.
    """
    found: set[str] = set()
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register" and node.args):
                name = getattr(node.args[0], "id", "")
                if name:
                    found.add(name)
            # `@admin.register(Thing)` on a ModelAdmin class.
            if isinstance(node, ast.ClassDef):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call)
                            and getattr(decorator.func, "attr", "") == "register"):
                        found |= {getattr(a, "id", "") for a in decorator.args
                                  if getattr(a, "id", "")}
    return found


def _translated_fields(trees: list[tuple[str, ast.AST]]) -> dict[str, set[str]]:
    """model name -> its django-modeltranslation fields.

    `title` registered for translation means the database also holds `title_es`,
    `title_hu` and one column per language in settings. The registration is in the
    source; the language list is not, so these names are known-to-exist-somehow rather
    than known-good, and the caller reports them `uncertain`.
    """
    options: dict[str, set[str]] = {}
    registered: dict[str, str] = {}
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    getattr(base, "id", getattr(base, "attr", "")) == "TranslationOptions"
                    for base in node.bases):
                for item in node.body:
                    if (isinstance(item, ast.Assign) and item.targets
                            and getattr(item.targets[0], "id", "") == "fields"
                            and isinstance(item.value, (ast.Tuple, ast.List))):
                        options[node.name] = {
                            _string(e) for e in item.value.elts if _string(e)
                        }
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register" and len(node.args) >= 2):
                model = getattr(node.args[0], "id", "")
                option = getattr(node.args[1], "id", "")
                if model and option:
                    registered[model] = option
    return {
        model: options.get(option, set()) for model, option in registered.items()
    }


def _column_from_kwarg(arg: str) -> str:
    """`started_at__gte` -> `started_at`; `user__username` -> "" (another model's)."""
    # `order_by("-started_at")` sorts descending on `started_at`; `"?"` shuffles. The
    # direction is not part of the name, and reading it as one reported 72 correct
    # orderings as missing columns.
    arg = arg.lstrip("-")
    if not arg or arg == "?":
        return ""
    if "__" not in arg:
        return arg
    head, _, tail = arg.partition("__")
    # One trailing lookup is this model's column with a comparison on it. Anything else
    # crosses a relation, and this reads one model at a time.
    return head if tail in _LOOKUPS else ""


def _receiver(node: ast.Attribute) -> tuple[str, str]:
    """`Season.objects.filter` -> ("Season", "objects"), following the chain leftwards."""
    owner = node.value
    # Unwrap a chain - `Season.objects.filter(...).exclude(...)` - and STOP when the next
    # step is not one: `owner = owner` on a plain `foo().filter()` is an infinite loop,
    # which is a scan that never ends rather than a wrong answer.
    #
    # And stop at a call this cannot name. `request.profile.checks_from_all_projects()`
    # returns a queryset of ANOTHER model; walking past it landed on `profile` and
    # checked the columns of the wrong table. A queryset method returns the same model
    # and is safe to walk through; anything else is a method whose return type is not
    # in this expression.
    while isinstance(owner, ast.Call):
        if not isinstance(owner.func, ast.Attribute):
            return "", ""
        if owner.func.attr not in _QUERYSET_METHODS:
            return "", ""
        owner = owner.func.value
    if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
        return owner.value.id, owner.attr
    return "", ""


def extract_django_orm(root: str) -> tuple[list[Symbol], list[Edge]]:
    """(symbols, edges) for the tables, columns and queries of a Django project."""
    files: list[tuple[str, ast.AST]] = []
    # Every class, kept per file. Keying one dict by the bare class name let whichever
    # file was walked last answer for all of them - and 383 names are defined twice in
    # sentry alone, so that is a claim about one table built from another table's fields.
    per_file: dict[str, dict[str, _Model]] = {}
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in _SKIP and not d.startswith(".")
        ]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(here, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError, ValueError):
                continue
            files.append((os.path.relpath(path, root), tree))
            per_file[path] = _models_in(tree, path, root)

    unique: dict[str, _Model] = {}
    seen_names: set[str] = set()
    for holds in per_file.values():
        for class_name, model in holds.items():
            if class_name in seen_names:
                unique.pop(class_name, None)
            else:
                seen_names.add(class_name)
                unique[class_name] = model

    # An abstract base has no table; it gave its columns away in the pass above.
    models = [(n, m) for n, m in _resolve_inheritance(per_file, unique) if not m.abstract]
    if not models:
        return [], []

    by_file: dict[str, dict[str, _Model]] = {}
    for name, model in models:
        by_file.setdefault(model.file, {})[name] = model
    by_name: dict[str, _Model] = {}
    seen_names = set()
    for name, model in models:
        if name in seen_names:
            by_name.pop(name, None)
        else:
            seen_names.add(name)
            by_name[name] = model

    for _name, model in models:
        for target_name, query_name in model.reverse:
            target = _pick(target_name, model.file, by_file, by_name)
            if target is not None:
                # Legal to name, and stored nowhere on the target's own row.
                target.names.setdefault(query_name, "")

    # Every name any annotate() in the project invents. Not per file: saleor annotates
    # `reserved_quantity` inside a custom QuerySet method and reads it three files away,
    # so a file-scoped set called two live annotations missing columns.
    aliases: set[str] = set()
    for _rel, tree in files:
        aliases |= _annotation_names(tree)

    # `config.tier_set.order_by(...)` returns TIER rows, so the read belongs to the table
    # that DECLARED the relation. This is how the reference project reads its bonus
    # tiers, and reading only `Model.objects` saw those tables written by a seeding
    # command and read by nobody.
    # ...and only when ONE model answers to the name. saleor declares
    # `related_name="lines"` on OrderLine, FulfillmentLine and more; keeping the first
    # seen made `fulfillment.lines.create(...)` a write to the order line table and
    # checked its keywords against that model's fields - 609 invented missing columns on
    # that project alone. Same rule as a class name two apps share: it answers for
    # neither.
    accessors: dict[str, _Model] = {}
    ambiguous_accessors: set[str] = set()
    for _name, model in models:
        for _target_name, query_name in model.reverse:
            if query_name in accessors and accessors[query_name] is not model:
                ambiguous_accessors.add(query_name)
            accessors[query_name] = model
    for query_name in ambiguous_accessors:
        accessors.pop(query_name, None)

    admin_models = _admin_registered(files)

    translated = _translated_fields(files)
    for name, model in models:
        if name in translated:
            model.translated = translated[name]

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    used_tables: set[str] = set()
    used_columns: set[str] = set()
    table_reads: dict[str, int] = {}
    table_writes: dict[str, int] = {}
    # (table, file, line) -> (is a write, snippet, owner)
    touches: dict[tuple[str, str, int], tuple[bool, str, str]] = {}

    for rel, tree in files:
        owners = owners_of(tree)
        bodies = _model_bodies(tree)
        rebound = _locally_bound(tree)
        here = os.path.join(root, rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in _QUERYSET_METHODS:
                continue
            model_name, manager = _receiver(node.func)
            if model_name in ("cls", "self"):
                # The model this line sits inside - which is always one declared in this
                # very file, so the file's own models are the only candidates.
                model_name = next(
                    (name for start, end, name in bodies
                     if start <= node.lineno <= end and name in by_file.get(here, {})), "")
            model = _pick(model_name, here, by_file, by_name) if model_name else None
            # A reverse accessor names the table by the relation rather than by the class:
            # whatever `config` is, `config.tier_set` is Tier.
            through_relation = manager in accessors and (
                model is None or manager not in model.managers)
            if through_relation:
                model = accessors[manager]
                model_name = model.table
            elif not model or manager not in model.managers:
                continue
            line = node.lineno
            owner = owners.get(line, "")
            used_tables.add(model.table)
            writing = method in _TABLE_WRITES
            # One row per table per PLACE. `Model.objects.filter(...).first()` is two
            # queryset methods on one line and one query, and counting both made every
            # chained read read as two. A write outranks a read: if the line writes the
            # table at all, that is what the line does.
            at = (model.table, rel, line)
            standing = touches.get(at)
            if standing is None or (writing and not standing[0]):
                touches[at] = (writing, f"{model_name}.{manager}.{method}(...)", owner)

            # `select_related("user")` names a relation, not a column of this table -
            # it is a read of the table at the OTHER end, which is the whole point of it.
            if method in ("select_related", "prefetch_related"):
                for argument in node.args:
                    named = _string(argument).partition("__")[0]
                    target = _pick(model.relations.get(named, ""), here, by_file, by_name)
                    if target is None:
                        continue
                    used_tables.add(target.table)
                    touches.setdefault((target.table, rel, line),
                                       (False, f'{method}("{named}")', owner))
                continue
            named: list[str] = []
            if method in _KWARG_METHODS:
                named += [_column_from_kwarg(w.arg) for w in node.keywords
                          if w.arg and w.arg not in _CONTROL_KWARGS]
            if method in _STRING_METHODS:
                # `dates("created_at", "day")`: the second argument is the granularity,
                # and no table has a column called `day`.
                args = node.args[:1] if method in ("dates", "datetimes") else node.args
                named += [_column_from_kwarg(_string(a)) for a in args if _string(a)]
            for name in {c for c in named if c}:
                legal, column = model.column_for(name)
                full = f"{model.table}.{column or name}"
                if legal and column:
                    used_columns.add(full)
                column_id = f"db_column_use:{full}:{rel}:{line}"
                if legal and not column:
                    # A ManyToMany or a translated field: the query is fine, and there is
                    # no column here to point at.
                    status, note = Status.UNCERTAIN, (
                        f"`{name}` is a legal name on `{model_name}` that is not a column "
                        f"of `{model.table}` - a ManyToMany lives in its own join table, "
                        f"and a translated field has one column per language in settings.")
                elif legal:
                    status, note = Status.CONNECTED, ""
                elif name in aliases:
                    status, note = Status.UNCERTAIN, (
                        f"`{name}` is a name an annotate() or aggregate() in this file "
                        f"invents, not a column of `{model.table}`.")
                elif model_name in rebound:
                    status, note = Status.UNCERTAIN, (
                        f"`{model_name}` is assigned in this file, so it is whatever that "
                        f"line produced - `apps.get_model(...)` returns the model as it "
                        f"was at one migration - and not necessarily the class read here.")
                elif not model.fully_read:
                    status, note = Status.UNCERTAIN, (
                        f"`{model_name}` has a base class or a field constructor this "
                        f"could not read, so it cannot say whether `{name}` is a column.")
                else:
                    status, note = Status.UNRESOLVED, (
                        f"`{model_name}` has no field stored in a column called "
                        f"`{name}`. Django raises FieldError the moment this line runs.")
                symbols.append(Symbol(
                    id=column_id, kind="db_column_use", label=full, sub="names a column",
                    file=rel, line=line, status=status,
                    snippet=f"{method}({name}=…)" if method in _KWARG_METHODS
                    else f'{method}("{name}")',
                    chain=[model.table, column or name], owner=owner, note=note,
                ))
                if legal and column:
                    edges.append(Edge(column_id, f"db_column:{full}", Status.CONNECTED))

    seen_tables: set[str] = set()
    # Proxies last, so the table is drawn under the model that actually declares it.
    # Every table touch, one row per PLACE, now that the whole project is walked.
    for (table, rel, line), (writing, snippet, owner) in sorted(touches.items()):
        counter = table_writes if writing else table_reads
        counter[table] = counter.get(table, 0) + 1
        use_id = f"db_table_use:{table}:{rel}:{line}"
        symbols.append(Symbol(
            id=use_id, kind="db_table_use", label=table,
            sub="writes a table" if writing else "reads a table",
            file=rel, line=line, status=Status.CONNECTED, snippet=snippet,
            chain=[table], owner=owner, note="",
        ))
        edges.append(Edge(use_id, f"db_table:{table}", Status.CONNECTED))

    for model_name, model in sorted(models, key=lambda kv: (kv[1].table, kv[1].proxy)):
        if model.table in seen_tables:
            # A proxy is a second name for a table that is already on the map.
            continue
        seen_tables.add(model.table)
        # Reads paired against writes, the same question Redis has always been asked.
        # `connected` used to mean "some queryset mentions this table", which put 59 of
        # the reference project's 61 tables in one bucket and asked nobody to look at
        # anything. A table only ever written is filled for nobody; a table only ever
        # read returns nothing, unless a person fills it through the admin.
        reads = table_reads.get(model.table, 0)
        writes = table_writes.get(model.table, 0)
        in_admin = model_name in admin_models
        counted = f"{writes} write / {reads} read"
        if not model.managed:
            status, note, sub = Status.UNCERTAIN, (
                "`Meta.managed = False`: Django neither creates nor owns this table, so "
                "nothing here can say whether it is used."), "table"
        elif reads and writes:
            status, note, sub = Status.CONNECTED, "", counted
        elif in_admin and reads:
            status, note, sub = Status.CONNECTED, (
                "Read here and written only through the Django admin, where a person "
                "fills it in a form this cannot see. That is how a config table works."
            ), counted + " · admin"
        elif reads:
            status, note, sub = Status.UNRESOLVED, (
                "Read here and written nowhere - no queryset in this repository puts a "
                "row in this table, and it is not registered in the admin either, so "
                "every read of it can only ever come back empty."), counted
        elif writes:
            status, note, sub = Status.UNUSED, (
                "Written here and read nowhere in this repository. Rows go in and "
                "nothing takes them out again."), counted
        else:
            status, note, sub = Status.UNUSED, (
                "No queryset in this repository reads or writes this table. A model "
                "reached only through the admin, a migration or raw SQL will look like "
                "this too."), counted
        symbols.append(Symbol(
            id=f"db_table:{model.table}", kind="db_table", label=model.table, sub=sub,
            file=os.path.relpath(model.file, root), line=model.line,
            status=status,
            snippet=f"class {model_name}(models.Model): ...", chain=[model.table],
            note=note,
        ))
        for column, (cfile, cline) in model.columns.items():
            full = f"{model.table}.{column}"
            symbols.append(Symbol(
                id=f"db_column:{full}", kind="db_column", label=full, sub="column",
                file=os.path.relpath(cfile, root), line=cline,
                # A column no query names is NOT unused: `all()` reads every column and
                # names none of them, which is how most code reads a row.
                status=Status.CONNECTED if full in used_columns else Status.UNCERTAIN,
                snippet=full, chain=[model.table, column],
                note="" if full in used_columns else (
                    "Not named in any queryset. `all()` and `get()` read every column "
                    "without naming one, so this says nothing about whether it is used."),
            ))
    return symbols, edges
