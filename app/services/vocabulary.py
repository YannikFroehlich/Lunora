import csv
import io
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from app.models import VocabularyCard

# Leitner system: a correct answer moves a card one box up, a wrong one back to box 1.
# The interval says how long a card rests in a box before it is due again.
LEITNER_INTERVAL_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 21}
MAX_BOX = 5
MAX_CARDS_PER_LIST = 2000
MAX_BULK_LINES = 500
CSV_HEADER = ["Begriff", "Übersetzung", "Notiz"]

TERM_MAX_LENGTH = VocabularyCard._meta.get_field("term").max_length
TRANSLATION_MAX_LENGTH = VocabularyCard._meta.get_field("translation").max_length
NOTE_MAX_LENGTH = VocabularyCard._meta.get_field("note").max_length


def due_filter(now, prefix=""):
    return Q(**{f"{prefix}due_at__isnull": True}) | Q(**{f"{prefix}due_at__lte": now})


def is_due(card, now):
    return card.due_at is None or card.due_at <= now


def with_card_counts(queryset, now=None):
    now = now or timezone.now()
    return queryset.annotate(
        card_count=Count("cards", distinct=True),
        due_count=Count("cards", filter=due_filter(now, "cards__"), distinct=True),
        learned_count=Count("cards", filter=Q(cards__box=MAX_BOX), distinct=True),
    )


def review_card(card, *, correct, now=None):
    now = now or timezone.now()
    if correct:
        card.box = min(MAX_BOX, card.box + 1)
        card.correct_count += 1
    else:
        card.box = 1
        card.wrong_count += 1
    card.due_at = now + timedelta(days=LEITNER_INTERVAL_DAYS[card.box])
    card.last_reviewed_at = now
    card.save(update_fields=["box", "due_at", "correct_count", "wrong_count", "last_reviewed_at"])
    return card


def reset_progress(vocabulary_list):
    return vocabulary_list.cards.update(
        box=1, due_at=None, correct_count=0, wrong_count=0, last_reviewed_at=None
    )


def box_distribution(vocabulary_list):
    counts = dict(vocabulary_list.cards.values_list("box").annotate(total=Count("id")))
    return [{"box": box, "count": counts.get(box, 0)} for box in range(1, MAX_BOX + 1)]


def _delimiter(line):
    for delimiter in ("\t", ";", "="):
        if delimiter in line:
            return delimiter
    return None


def parse_bulk_cards(text):
    """Parse "Begriff;Übersetzung;Notiz" lines (tab, ";" or "=" separated) into card tuples.

    Returns (cards, errors); errors name the 1-based line number so the user can fix the paste.
    """
    lines = (text or "").lstrip("﻿").splitlines()
    cards = []
    errors = []
    if len([line for line in lines if line.strip()]) > MAX_BULK_LINES:
        return [], [f"Es können höchstens {MAX_BULK_LINES} Zeilen auf einmal importiert werden."]

    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        delimiter = _delimiter(line)
        if delimiter is None:
            errors.append(f"Zeile {number}: Kein Trennzeichen (Tab, ; oder =) gefunden.")
            continue
        parts = [part.strip() for part in next(csv.reader([line], delimiter=delimiter))]
        if number == 1 and parts == CSV_HEADER[: len(parts)]:
            continue
        term = parts[0] if parts else ""
        translation = parts[1] if len(parts) > 1 else ""
        note = delimiter.join(parts[2:]).strip() if len(parts) > 2 else ""
        if not term or not translation:
            errors.append(f"Zeile {number}: Begriff und Übersetzung dürfen nicht leer sein.")
        elif len(term) > TERM_MAX_LENGTH or len(translation) > TRANSLATION_MAX_LENGTH:
            errors.append(f"Zeile {number}: Begriff oder Übersetzung ist zu lang.")
        elif len(note) > NOTE_MAX_LENGTH:
            errors.append(f"Zeile {number}: Die Notiz ist zu lang.")
        else:
            cards.append((term, translation, note))
    return cards, errors


def export_csv(vocabulary_list):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for card in vocabulary_list.cards.all():
        writer.writerow([card.term, card.translation, card.note])
    # The BOM makes Excel open the semicolon file as UTF-8 instead of mangling umlauts.
    return "﻿" + buffer.getvalue()
