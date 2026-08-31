"""GraphQL: a query names fields, a schema defines them, nothing checks they agree.

A GraphQL API has ONE route, so every route adapter reports `/graphql` as a single
connected endpoint and stops. saleor is GraphQL-first and a scan of 847,000 lines found
nine REST routes and said nothing about the actual API.

The one rule that matters most here is about what is NOT claimed. A schema field nothing
in this repository queries is not dead: a GraphQL API is normally consumed by clients in
other repositories. It is `uncertain` with the reason, never `unused` - getting that wrong
would tell a team to delete their public API.

Every other case below was found by running against saleor and saleor-dashboard, where the
first version reported 5,604 phantom findings.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.graphql_extractor import extract_graphql, schema_fields
from seamcheck.graph import Status

SCHEMA = """
type Query {
  orders(first: Int): OrderConnection
  legacyReport: Report
}
type Order {
  id: ID!
  totalPrice: Money
  oldField: String
}
"""


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _status(root: str) -> dict[str, str]:
    symbols, _ = extract_graphql(root)
    return {s.id: s.status.value for s in symbols}


class TheSeam(unittest.TestCase):
    def test_a_field_the_schema_does_not_define_is_unresolved(self):
        root = _repo({
            "schema.graphql": SCHEMA,
            "q.ts": "const Q = gql`query { orders { id missingField } }`;",
        })
        status = _status(root)
        self.assertEqual(status["graphql_selection:missingField"], "unresolved")
        self.assertEqual(status["graphql_selection:id"], "connected")

    def test_a_queried_field_marks_the_schema_field_connected(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`query { orders { totalPrice } }`;"})
        self.assertEqual(_status(root)["graphql_field:Order.totalPrice"], "connected")

    def test_an_unqueried_schema_field_is_uncertain_not_unused(self):
        """The API's clients usually live somewhere this scan cannot see."""
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`query { orders { id } }`;"})
        symbols, _ = extract_graphql(root)
        field = next(s for s in symbols if s.id == "graphql_field:Order.oldField")
        self.assertEqual(field.status, Status.UNCERTAIN)
        self.assertIn("another repository", field.note)
        self.assertNotEqual(field.status, Status.UNUSED)

    def test_no_schema_means_no_claims_at_all(self):
        symbols, edges = extract_graphql(_repo({"q.ts": "const Q = gql`query { x }`;"}))
        self.assertEqual((symbols, edges), ([], []))


class WhatIsNotAField(unittest.TestCase):
    def test_the_operation_name_is_not_a_field(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`query Orders { orders { id } }`;"})
        self.assertNotIn("graphql_selection:Orders", _status(root))

    def test_a_fragment_name_and_its_type_condition_are_not_fields(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`{ orders { ...Bits } } fragment Bits on Order { id }`;"})
        status = _status(root)
        self.assertNotIn("graphql_selection:Bits", status)
        self.assertNotIn("graphql_selection:Order", status)

    def test_variables_and_arguments_are_not_fields(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`query O($first: Int) { orders(first: $first) { id } }`;"})
        status = _status(root)
        self.assertNotIn("graphql_selection:first", status)
        self.assertNotIn("graphql_selection:Int", status)

    def test_a_directive_is_not_a_field(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": 'const Q = gql`{ orders { id @include(if: $x) } }`;'})
        status = _status(root)
        self.assertNotIn("graphql_selection:include", status)

    def test_an_interpolated_fragment_reference_is_not_a_field(self):
        """`${OrderBitsDoc}` is JavaScript spliced into the template, not a selection."""
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`{ orders { id } } ${OrderBitsFragmentDoc}`;"})
        self.assertNotIn("graphql_selection:OrderBitsFragmentDoc", _status(root))

    def test_an_enum_value_is_not_a_field(self):
        root = _repo({
            "schema.graphql": SCHEMA + "\nenum Status { ACCOUNT_ACTIVATED DRAFT }\n",
            "q.ts": "const Q = gql`{ orders { id } }`;",
        })
        self.assertNotIn("graphql_selection:ACCOUNT_ACTIVATED", _status(root))

    def test_an_introspection_query_is_not_checked_against_the_schema(self):
        """__schema's fields are defined by GraphQL itself, not by this API."""
        root = _repo({"schema.graphql": SCHEMA,
                      "q.ts": "const Q = gql`{ __schema { types { name fields { name } } } }`;"})
        status = _status(root)
        self.assertNotIn("graphql_selection:types", status)
        self.assertNotIn("graphql_selection:__schema", status)

    def test_a_schema_file_is_never_read_as_a_query(self):
        """saleor's schema.graphql yielded 85,162 'selections' before this."""
        root = _repo({"schema.graphql": SCHEMA + "\nenum E { A B }\n"})
        self.assertFalse([k for k in _status(root) if k.startswith("graphql_selection:")])

    def test_test_files_are_not_the_applications_own_usage(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "tests/test_q.py": '"""query { orders { nonsense } }"""'})
        self.assertNotIn("graphql_selection:nonsense", _status(root))

    def test_generated_files_are_not_read(self):
        root = _repo({"schema.graphql": SCHEMA,
                      "hooks.generated.ts": "const Q = gql`{ orders { phantom } }`;"})
        self.assertNotIn("graphql_selection:phantom", _status(root))


class SchemaParsing(unittest.TestCase):
    def test_a_description_containing_a_brace_does_not_truncate_the_type(self):
        """saleor's Mutation type lost every field after the first braced description."""
        root = _repo({"schema.graphql": '''
type Query {
  """A description with a { brace } in it."""
  first: String
  second: String
}
'''})
        names = {f for v in schema_fields(root).values() for f, _, _ in v}
        self.assertEqual(names, {"first", "second"})

    def test_a_field_with_a_directive_in_its_arguments_is_still_found(self):
        """`@deprecated(reason: "...")` nests a paren that a regex stopped at."""
        root = _repo({"schema.graphql": '''
type Query {
  draftOrders(
    filter: OrderFilterInput @deprecated(reason: "Use `where` instead.")
    where: DraftOrderWhereInput
  ): OrderCountableConnection
  after: String
}
'''})
        names = {f for v in schema_fields(root).values() for f, _, _ in v}
        self.assertIn("draftOrders", names)
        self.assertIn("after", names, "the field after the nested paren survives")
        self.assertNotIn("filter", names, "an argument is not a field")

    def test_a_single_line_type_yields_every_field(self):
        root = _repo({"schema.graphql": "type Order { id: ID! total: Money name: String }"})
        names = {f for v in schema_fields(root).values() for f, _, _ in v}
        self.assertEqual(names, {"id", "total", "name"})

    def test_input_and_interface_blocks_count(self):
        root = _repo({"schema.graphql": "interface Node { id: ID! }\ninput Filter { term: String }"})
        self.assertEqual(set(schema_fields(root)), {"Node", "Filter"})


if __name__ == "__main__":
    unittest.main()
