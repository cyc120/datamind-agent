"""The one publish policy: what makes a `document_assets` row publicly readable.

Audit [5.4](</Users/caicai/DataMind Agent/reports/project_audit_20261009_561316c.md>) warns
that permissions, parsing, storage and UI state are edited across layers, so a new route can
miss a rule that was only ever spelled out next to its callers. The "this document is
published" judgement was written three times — a Python predicate in `api.py`, two inline
SQL filters in `knowledge.py`, and a second Python predicate in `authority.py` — identical
in intent and already drifted in shape. R03 (an unreviewed upload's contract text readable
anonymously) is what that drift costs.

This module is the single definition. It offers:

* the published status and public ACL constants, and the collection allow-list resolved
  from its existing home (`knowledge.COLLECTIONS`) rather than copied;
* :func:`is_published` — the Python predicate, for a caller that already holds the row;
* :func:`published_document_sql` — the SQL condition, for a caller that must filter in the
  database, with the collection allow-list bound as a parameter so the caller cannot drop
  it or bind it in the wrong position;
* :func:`public_reviewed_sql` — the readiness probe behind `/health`, which deliberately
  omits the allow-list (see its docstring).

The judgement itself is security relevant and unchanged::

    status == 'indexed' and 'public' in acl and not needs_review
        and collection in COLLECTIONS

`knowledge.py`'s row read additionally requires the *chunk* to carry the public ACL
(`'public'=ANY(c.acl)`). That is a per-chunk ACL, not part of the document publish gate, and
it stays in `knowledge.py` next to the join it belongs to.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import NamedTuple


#: `document_assets.status` value that makes a row eligible for public reads.
PUBLISHED_STATUS = "indexed"
#: The one ACL entry that means "anonymous public read".
PUBLIC_ACL = "public"

_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class SqlFragment(NamedTuple):
    """A SQL condition together with the parameters it binds, used as one unit.

    :attr:`text` contains exactly the placeholders :attr:`params` binds, in text order.
    Splicing the two at one point in the caller's statement (see
    :func:`published_document_sql`) is the whole convention.
    """

    text: str
    params: tuple


def publishable_collections() -> frozenset[str]:
    """The collections a document may be published from, from their one existing literal.

    The lookup is deferred to call time because `knowledge` imports this module for the SQL
    condition; a module-level import here would be an import cycle. Nothing is duplicated:
    the only literal still lives in `datamind.knowledge`.
    """
    from .knowledge import COLLECTIONS

    return frozenset(COLLECTIONS)


def has_public_acl(row: Mapping | None) -> bool:
    """Whether a document row carries the anonymous-public ACL entry."""
    return bool(row) and PUBLIC_ACL in (row.get("acl") or ())


def collection_is_publishable(collection: str | None) -> bool:
    """Whether a collection is one the knowledge base may publish from."""
    return collection in publishable_collections()


def is_published(row: Mapping | None) -> bool:
    """The publish predicate for a row already in hand.

    `row` is a `document_assets` mapping — a `psycopg2.extras.RealDictRow` qualifies — or
    `None` when the id is unknown; an unknown row is never published.
    """
    if not row:
        return False
    return (row.get("status") == PUBLISHED_STATUS
            and has_public_acl(row)
            and not row.get("needs_review")
            and collection_is_publishable(row.get("collection")))


def _checked_alias(alias: str) -> str:
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
        raise ValueError("SQL_ALIAS_INVALID")
    return alias


def published_document_sql(alias: str, collections: Iterable[str]) -> SqlFragment:
    """The publish gate as a parenthesised SQL condition over one `document_assets` alias.

    Returns ``(text, params)`` — a two-field tuple, so both spellings work::

        gate = published_document_sql("d", collections)
        cur.execute(f"SELECT ... FROM chunks c JOIN document_assets d USING(document_id) "
                    f"WHERE c.document_version=d.doc_version AND {gate.text} "
                    f"  AND c.document_id=ANY(%s)",
                    (*gate.params, ids))
        # or, equivalently:
        where, values = published_document_sql("d", collections)

    **Binding convention.** `text` binds exactly one placeholder: the collection
    allow-list, as a `text[]`, and `params` is the matching one-element tuple. Splice the
    text and the parameters at the *same* point and in that order — psycopg2 binds
    positionally in text order, so the fragment must be written before any placeholder that
    follows it in the statement, and the caller must pass ``*params`` first. Keeping the
    text and its parameters together (rather than a text-returning and a params-returning
    helper) is what makes forgetting the allow-list impossible.

    `alias` is a code constant (validated as a bare SQL identifier). The status and ACL
    literals are code constants too, never caller data, so the allow-list is the only bound
    value and no caller value is ever interpolated into the text.
    """
    table = _checked_alias(alias)
    return SqlFragment(
        f"({table}.status='{PUBLISHED_STATUS}' AND '{PUBLIC_ACL}'=ANY({table}.acl)"
        f" AND NOT {table}.needs_review AND {table}.collection=ANY(%s))",
        (list(collections),))


def public_reviewed_sql(alias: str) -> SqlFragment:
    """The readiness probe behind `/health`: the publish gate *without* the allow-list.

    `/health`'s `document_count` has always counted public, review-cleared, indexed rows of
    **any** collection, and that number decides the `knowledge` readiness field. Adding the
    allow-list would change the signal (today no row differs, because every such row happens
    to sit in an allowed collection), so the distinction is kept deliberately — stated once
    here rather than re-typed inline at the route. It binds no parameters.
    """
    table = _checked_alias(alias)
    return SqlFragment(
        f"({table}.status='{PUBLISHED_STATUS}' AND '{PUBLIC_ACL}'=ANY({table}.acl)"
        f" AND NOT {table}.needs_review)", ())
