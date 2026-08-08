r"""Tests for :mod:`app.sql.registry`.

The cast test is the reason this file exists
-------------------------------------------
SQLAlchemy's bind-parameter regex ends with a negative lookahead ``(?![:\w$])``,
so ``:param`` followed by a colon is **never recognised as a parameter**. Write
``CAST`` the PostgreSQL shorthand way — ``:date_from::date`` — and the text
reaches the server literally, unbound. asyncpg then rejects the statement, or
worse, a query silently compares against nothing.

Phase 6 hit this and the resolution was a rule: **every cast must be
``CAST(:param AS type)``**. A rule that lives only in a comment decays, so
:func:`test_no_query_casts_a_parameter_with_double_colon_shorthand` scans all the
SQL on disk and fails the build if the shorthand reappears.

Everything else here checks the registry's own guarantees: fragments resolve,
names are unique, and the parameter set it reports for a query really is what the
SQL declares — that last one matters because ``bind_params`` trusts it completely
and drops anything the registry does not list.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy.sql.elements import TextClause

from app.core.exceptions import QueryNotFoundError
from app.sql.registry import (
    SQL_FRAGMENTS_DIR,
    SQL_QUERIES_DIR,
    SqlRegistry,
    get_registry,
    init_registry,
)

#: SQLAlchemy's own bind regex, so this test cannot disagree with the registry.
PARAM_RE = re.compile(r"(?<![:\w$]):([\w$]+)(?![:\w$])", re.UNICODE)

#: A colon-prefixed identifier immediately followed by the cast shorthand. This
#: is the dangerous form. Bare ``expr::type`` on a column or literal is fine and
#: is used throughout the SQL, so the pattern requires the leading colon.
BAD_CAST_RE = re.compile(r"(?<![:\w$]):[\w$]+::")


@pytest.fixture(scope="module")
def registry() -> SqlRegistry:
    """Return the loaded process registry."""
    return init_registry()


def sql_files() -> list:
    """Return every query file on disk, sorted."""
    return sorted(SQL_QUERIES_DIR.rglob("*.sql"))


# ---------------------------------------------------------------------------
# The asyncpg cast trap
# ---------------------------------------------------------------------------


def test_no_query_casts_a_parameter_with_double_colon_shorthand() -> None:
    """``:param::type`` is never bound, so it must not appear in any query.

    Scanned from disk rather than through the registry, so a file that fails to
    load for some other reason still gets checked.
    """
    offenders: list[str] = []
    for path in sql_files():
        text = path.read_text(encoding="utf-8")
        for match in BAD_CAST_RE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(SQL_QUERIES_DIR)}:{line} -> {match.group(0)}")

    assert not offenders, (
        "These parameters use the ``:param::type`` cast shorthand, which "
        "SQLAlchemy's bind regex does not recognise — the text reaches Postgres "
        "unbound. Rewrite each as CAST(:param AS type):\n  " + "\n  ".join(offenders)
    )


def test_no_fragment_casts_a_parameter_with_double_colon_shorthand() -> None:
    """The same rule inside the shared fragments, which are spliced into queries."""
    if not SQL_FRAGMENTS_DIR.is_dir():
        pytest.skip("no fragments directory")

    offenders: list[str] = []
    for path in sorted(SQL_FRAGMENTS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in BAD_CAST_RE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line} -> {match.group(0)}")

    assert not offenders, "Fragments using :param::type shorthand:\n  " + "\n  ".join(offenders)


def test_the_bad_cast_pattern_actually_matches_the_shorthand() -> None:
    """Guard the guard.

    A scanner with a broken pattern reports "no offenders" forever and reads as a
    passing test. These assertions prove it catches the bad form and tolerates
    the legitimate ones.
    """
    assert BAD_CAST_RE.search("WHERE d >= :date_from::date")
    assert BAD_CAST_RE.search("SELECT :limit::int")
    # Legitimate: casting a column, a literal, or a function result.
    assert not BAD_CAST_RE.search("COUNT(*)::numeric")
    assert not BAD_CAST_RE.search("u.signup_date::date")
    assert not BAD_CAST_RE.search("'2026-01-01'::date")
    # The correct form for a parameter.
    assert not BAD_CAST_RE.search("CAST(:date_from AS date)")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_every_sql_file_on_disk_is_registered(registry: SqlRegistry) -> None:
    """The registry's contents equal the files present, with no silent drops.

    Asserted against the filesystem rather than a hardcoded count, so adding a
    query cannot make this test stale and wrong at the same time.
    """
    expected = {
        f"{path.parent.name}/{path.stem}" if path.parent != SQL_QUERIES_DIR else path.stem
        for path in sql_files()
    }
    assert set(registry.names()) == expected
    assert len(registry) == len(expected)


def test_the_registry_is_not_empty(registry: SqlRegistry) -> None:
    """A registry that loaded nothing would make every other test here vacuous."""
    assert len(registry) > 40


def test_every_query_composes_to_non_empty_sql(registry: SqlRegistry) -> None:
    """No query is blank, and every include resolved."""
    for name in registry.names():
        raw = registry.raw(name)
        assert raw.strip(), name
        # An unresolved include would leave the literal `{{name}}` behind.
        assert "{{" not in raw, f"{name} has an unresolved fragment include"


def test_every_query_returns_an_executable_statement(registry: SqlRegistry) -> None:
    """``get`` hands back a compiled ``TextClause``, cached per name."""
    for name in registry.names():
        statement = registry.get(name)
        assert isinstance(statement, TextClause)
        # Same object on the second call: these are built once at load.
        assert registry.get(name) is statement


def test_reported_params_match_what_the_sql_declares(registry: SqlRegistry) -> None:
    """The registry's parameter set is exactly what the composed SQL contains.

    Load-bearing: ``bind_params`` intersects the caller's mapping against this
    set and *discards the rest*. A parameter the registry fails to report would
    be dropped before it ever reached the driver — the query would then run with
    a missing bind rather than fail loudly.
    """
    for name in registry.names():
        found = set(PARAM_RE.findall(registry.raw(name)))
        assert registry.params(name) == frozenset(found), name


def test_params_is_a_frozenset(registry: SqlRegistry) -> None:
    """Immutable, so a caller cannot mutate the registry's own record."""
    name = registry.names()[0]
    assert isinstance(registry.params(name), frozenset)


# ---------------------------------------------------------------------------
# Namespaces and lookup
# ---------------------------------------------------------------------------


def test_names_can_be_filtered_by_namespace(registry: SqlRegistry) -> None:
    """The namespace is the directory, and filtering returns only its queries."""
    namespaces = {name.split("/")[0] for name in registry.names() if "/" in name}
    assert namespaces, "expected queries to be organised into namespace directories"

    for namespace in sorted(namespaces):
        selected = registry.names(namespace)
        assert selected, namespace
        assert all(name.startswith(f"{namespace}/") for name in selected)

    # And the union over namespaces accounts for every namespaced query.
    total = sum(len(registry.names(ns)) for ns in namespaces)
    assert total == len([n for n in registry.names() if "/" in n])


def test_an_unknown_namespace_returns_nothing(registry: SqlRegistry) -> None:
    """Empty, not an error: asking about a namespace that does not exist is fine."""
    assert registry.names("no_such_namespace") == []


def test_names_are_sorted(registry: SqlRegistry) -> None:
    """Stable ordering, so a coverage report reads the same way twice."""
    assert registry.names() == sorted(registry.names())


def test_membership_test_works(registry: SqlRegistry) -> None:
    """``in`` is supported and agrees with ``names()``."""
    name = registry.names()[0]
    assert name in registry
    assert "no_such_namespace/nope" not in registry


@pytest.mark.parametrize("method", ["get", "raw", "params"])
def test_an_unknown_query_raises_the_domain_exception(registry: SqlRegistry, method: str) -> None:
    """Every accessor fails the same way, with the project's own exception type."""
    with pytest.raises(QueryNotFoundError):
        getattr(registry, method)("no_such_namespace/no_such_query")


def test_get_registry_returns_the_populated_singleton(registry: SqlRegistry) -> None:
    """``get_registry`` is what the repositories call; it must be the loaded one."""
    assert get_registry() is registry
    assert len(get_registry()) == len(registry)


# ---------------------------------------------------------------------------
# Query hygiene
# ---------------------------------------------------------------------------


def test_strip_sql_noise_handles_apostrophes_inside_comments() -> None:
    """Guard the guard — this helper was silently wrong when first written.

    The two hygiene checks below inspect whatever :func:`_strip_sql_noise`
    returns, so a bug in it makes them assert against the wrong text rather than
    fail. The first version stripped string literals before comments, and the
    apostrophe in a comment like *the dataset's maximum date* opened a phantom
    literal that ran to the next real quote — deleting the ``SELECT`` it was
    supposed to be checking for. 21 of the query files contain that pattern.

    These cases pin the behaviour in both nesting directions.
    """
    # The regression: an apostrophe in a comment must not eat the code after it.
    text = "-- anchored to the dataset's maximum date\nSELECT 1 FROM t\n"
    assert "SELECT 1 FROM t" in _strip_sql_noise(text)

    # The other direction: a `--` inside a literal must not start a comment.
    assert "SELECT" in _strip_sql_noise("SELECT '--not a comment' AS s")
    # ...and the literal's contents are gone, so keywords inside it cannot fire.
    assert "not a comment" not in _strip_sql_noise("SELECT '--not a comment' AS s")

    # A comment is removed outright.
    assert "hidden" not in _strip_sql_noise("SELECT 1 -- hidden truncate\n")
    # A doubled quote is an escaped quote, not a terminator.
    scrubbed = _strip_sql_noise("SELECT 'it''s fine' AS s, 2 AS n")
    assert "2 AS n" in scrubbed
    # Semicolons and keywords inside prose are invisible to the checks.
    assert ";" not in _strip_sql_noise("-- see note; then continue\nSELECT 1\n")
    assert "delete from" not in _strip_sql_noise("-- never delete from core\nSELECT 1\n").lower()


def test_no_query_contains_a_semicolon_terminated_second_statement(
    registry: SqlRegistry,
) -> None:
    """One statement per file.

    A trailing semicolon is harmless, but text after one would mean two
    statements in a single ``execute`` — which asyncpg refuses in prepared-
    statement mode, and which would make the parameter accounting above wrong.
    """
    for name in registry.names():
        body = registry.raw(name).strip()
        without_trailing = body.removesuffix(";").strip()
        assert ";" not in _strip_sql_noise(without_trailing), name


def test_every_query_selects_something(registry: SqlRegistry) -> None:
    """These are all read queries. Nothing here should mutate.

    A guard against a stray ``INSERT``/``UPDATE``/``DELETE``/``TRUNCATE`` landing
    in a directory the API executes with a read-only session. The API would fail
    at runtime, but failing here says why.
    """
    forbidden = ("insert into", "update ", "delete from", "truncate", "drop ", "alter ")
    for name in registry.names():
        text = _strip_sql_noise(registry.raw(name)).lower()
        assert "select" in text, name
        for keyword in forbidden:
            assert keyword not in text, f"{name} contains {keyword!r}"


def _strip_sql_noise(sql: str) -> str:
    """Remove line comments and single-quoted literals from SQL text.

    Both can legitimately contain semicolons and SQL keywords, which would make
    the hygiene checks above fire on prose rather than on code.

    Single pass, not two regexes, because the two constructs can nest either way
    and the order of a two-pass strip is wrong in one direction or the other:

    * Strings first: ``-- anchored to the dataset's maximum date`` — the
      apostrophe in *dataset's* opens a phantom literal that runs to the next
      real quote further down the file, swallowing whole clauses. 21 of the query
      files contain an apostrophe inside a comment, so this is the common case,
      and it silently emptied the text these assertions inspect.
    * Comments first: a literal containing ``--`` would be truncated instead.

    Whichever delimiter opens first wins, which is exactly PostgreSQL's own rule.
    Block comments are not handled: none of the queries use them, and a scanner
    that claimed to strip them without being tested on them would be worse than
    one that does not.
    """
    out: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char == "'":
            # A quoted literal. '' is an escaped quote, not a terminator.
            index += 1
            while index < length:
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            out.append("''")
        elif char == "-" and index + 1 < length and sql[index + 1] == "-":
            # A line comment: drop through to the newline, which is preserved so
            # line numbers in any failure message still line up.
            while index < length and sql[index] != "\n":
                index += 1
        else:
            out.append(char)
            index += 1

    return "".join(out)
