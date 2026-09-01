"""Ranked full-text search across notes.

PostgreSQL (production) matches a weighted ``tsvector`` built from the title and
the server-derived ``plain_text``, so hits are stemmed and ordered by relevance.
SQLite (development, and therefore the test suite) has no ``tsvector`` and falls
back to a substring match with an equivalent SQL-side ranking expression. Both
backends share ``parse_search_query`` and the same substring safety net, so the
observable behaviour stays the same and SQLite tests still say something about
production.

Two German-specific choices are worth knowing before changing anything here:

* German compounds mean a search for "Rakete" must still find "Raketenstart".
  Every term is therefore matched as a ``:*`` prefix in the tsquery *and* kept as
  a substring match, so the infix case ("start" -> "Raketenstart") survives too.
  The tsvector is what adds stemming and relevance ordering; the substring branch
  only guards recall.
* The tsvector is built per query rather than stored in a column. That keeps
  ``Note`` free of a denormalised field to keep in sync on every save. If the
  note count ever makes this slow, the upgrade path is a stored
  ``SearchVectorField`` plus a GIN index, populated in ``save_note``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import connection
from django.db.models import Case, FloatField, Q, Value, When

SEARCH_CONFIG = "german"
MAX_TERMS = 12
MAX_TERM_LENGTH = 60
SNIPPET_LENGTH = 180
SNIPPET_LEAD = 40
TITLE_WEIGHT = 4.0
TEXT_WEIGHT = 1.0
VECTOR_WEIGHT = 8.0

_TOKEN_PATTERN = re.compile(r'-?"[^"]*"|\S+')
_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)
_SAFE_TERM_PATTERN = re.compile(r"^\w+$", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedQuery:
    """A raw search string split into required terms, phrases and exclusions."""

    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()

    def __bool__(self):
        return bool(self.terms or self.phrases)

    @property
    def highlights(self):
        """Fragments worth marking up in a result snippet, longest first."""
        return sorted(set(self.phrases + self.terms), key=len, reverse=True)


def parse_search_query(raw):
    """Split a raw search string into ``ParsedQuery`` parts.

    Supported syntax: bare words are required (AND), ``"quoted words"`` must
    appear as a contiguous phrase, and a leading ``-`` excludes a word or phrase.
    """
    terms = []
    phrases = []
    exclusions = []

    def collect(bucket, fragment):
        fragment = fragment[:MAX_TERM_LENGTH].strip()
        if fragment and fragment not in bucket and len(bucket) < MAX_TERMS:
            bucket.append(fragment)

    for token in _TOKEN_PATTERN.findall(str(raw or "")):
        negated = token.startswith("-")
        if negated:
            token = token[1:]
        quoted = token.startswith('"')
        fragment = token.strip('"').strip()
        if quoted:
            fragment = _WHITESPACE_PATTERN.sub(" ", fragment)
            if not fragment:
                continue
            collect(exclusions if negated else (phrases if " " in fragment else terms), fragment)
            continue
        # Punctuation splits a bare token, so every term stays a single safe word.
        for word in _WORD_PATTERN.findall(fragment):
            collect(exclusions if negated else terms, word)

    return ParsedQuery(tuple(terms), tuple(phrases), tuple(exclusions))


def search_notes(queryset, raw_query):
    """Filter and rank ``queryset`` by relevance, annotating ``search_rank``.

    Always annotates ``search_rank`` — including for an empty query — so callers
    can order by it unconditionally.
    """
    parsed = parse_search_query(raw_query)
    if not parsed and not parsed.exclusions:
        return queryset.annotate(search_rank=Value(0.0, output_field=FloatField()))

    for fragment in parsed.exclusions:
        queryset = queryset.exclude(Q(title__icontains=fragment) | Q(plain_text__icontains=fragment))
    if not parsed:
        return queryset.annotate(search_rank=Value(0.0, output_field=FloatField()))

    if connection.vendor == "postgresql":
        queryset = _apply_postgres_search(queryset, parsed)
    else:
        queryset = queryset.annotate(search_rank=_substring_rank(parsed)).filter(_substring_filter(parsed))
    return queryset.distinct()


def highlight_text(text, parsed):
    """Mark every match in a short string, without windowing it like a snippet.

    Used for titles, where the whole value is shown anyway.
    """
    body = _WHITESPACE_PATTERN.sub(" ", str(text or "")).strip()
    if not body:
        return []
    spans = _match_spans(body, parsed.highlights) if parsed else []
    if not spans:
        return [{"text": body, "match": False}]

    segments = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            segments.append({"text": body[cursor:start], "match": False})
        segments.append({"text": body[start:end], "match": True})
        cursor = end
    if cursor < len(body):
        segments.append({"text": body[cursor:], "match": False})
    return segments


def build_snippet(text, parsed, *, length=SNIPPET_LENGTH):
    """Return ``[{"text": ..., "match": bool}]`` segments around the first hit.

    Segments are returned instead of marked-up HTML so the template escapes note
    content the normal way.
    """
    body = _WHITESPACE_PATTERN.sub(" ", str(text or "")).strip()
    if not body:
        return []
    spans = _match_spans(body, parsed.highlights) if parsed else []
    if not spans:
        return [{"text": _truncate(body, length), "match": False}]

    start = max(0, spans[0][0] - SNIPPET_LEAD)
    if start:
        space = body.rfind(" ", 0, start + 1)
        start = space + 1 if space > 0 else start
    window = body[start : start + length]
    truncated_end = start + length < len(body)
    if truncated_end:
        space = window.rfind(" ")
        if space > length // 2:
            window = window[:space]

    segments = []
    if start:
        segments.append({"text": "… ", "match": False})
    cursor = 0
    window_end = start + len(window)
    for span_start, span_end in spans:
        if span_end <= start:
            continue
        if span_start >= window_end:
            break
        local_start = max(span_start - start, cursor)
        local_end = min(span_end - start, len(window))
        if local_end <= local_start:
            continue
        if local_start > cursor:
            segments.append({"text": window[cursor:local_start], "match": False})
        segments.append({"text": window[local_start:local_end], "match": True})
        cursor = local_end
    if cursor < len(window):
        segments.append({"text": window[cursor:], "match": False})
    if truncated_end:
        segments.append({"text": " …", "match": False})
    return segments


def _apply_postgres_search(queryset, parsed):
    from django.contrib.postgres.search import SearchRank, SearchVector

    vector = SearchVector("title", weight="A", config=SEARCH_CONFIG) + SearchVector(
        "plain_text", weight="B", config=SEARCH_CONFIG
    )
    query = _tsquery(parsed)
    if query is None:
        return queryset.annotate(search_rank=_substring_rank(parsed)).filter(_substring_filter(parsed))

    rank = SearchRank(vector, query) * Value(VECTOR_WEIGHT, output_field=FloatField()) + _substring_rank(
        parsed
    )
    return queryset.annotate(search_vector=vector, search_rank=rank).filter(
        Q(search_vector=query) | _substring_filter(parsed)
    )


def _tsquery(parsed):
    """Build the combined ``tsquery``, or ``None`` if nothing survived sanitising."""
    from django.contrib.postgres.search import SearchQuery

    nodes = [SearchQuery(phrase, search_type="phrase", config=SEARCH_CONFIG) for phrase in parsed.phrases]
    for term in parsed.terms:
        # search_type="raw" is passed to to_tsquery() as a bound parameter, so this
        # is not an injection risk, but a stray operator would raise a database
        # error at query time. The parser only emits bare words; re-check anyway.
        if not _SAFE_TERM_PATTERN.match(term):
            continue
        nodes.append(SearchQuery(f"{term}:*", search_type="raw", config=SEARCH_CONFIG))
    if not nodes:
        return None
    combined = nodes[0]
    for node in nodes[1:]:
        combined = combined & node
    return combined


def _substring_filter(parsed):
    """Require every term and phrase to appear somewhere in the title or body."""
    condition = Q()
    for fragment in parsed.phrases + parsed.terms:
        condition &= Q(title__icontains=fragment) | Q(plain_text__icontains=fragment)
    return condition


def _substring_rank(parsed):
    expression = Value(0.0, output_field=FloatField())
    for fragment in parsed.phrases + parsed.terms:
        expression = (
            expression
            + Case(
                When(title__icontains=fragment, then=Value(TITLE_WEIGHT)),
                default=Value(0.0),
                output_field=FloatField(),
            )
            + Case(
                When(plain_text__icontains=fragment, then=Value(TEXT_WEIGHT)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
    return expression


def _match_spans(body, fragments):
    """Locate every fragment in ``body`` as merged, non-overlapping spans."""
    lowered = body.casefold()
    spans = []
    for fragment in fragments:
        needle = fragment.casefold()
        if not needle:
            continue
        start = lowered.find(needle)
        while start != -1:
            spans.append((start, start + len(needle)))
            start = lowered.find(needle, start + len(needle))
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _truncate(body, length):
    if len(body) <= length:
        return body
    window = body[:length]
    space = window.rfind(" ")
    if space > length // 2:
        window = window[:space]
    return f"{window} …"
