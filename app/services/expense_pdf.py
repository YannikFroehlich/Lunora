"""A shared expense group as a printable PDF statement (reportlab): where everyone stands, who pays
whom to settle up, and every expense."""

from html import escape
from io import BytesIO

from django.contrib.auth import get_user_model
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.expenses import balances, euro, settle_up_plan
from app.services.notes import display_name
from app.services.user_preferences import format_user_date

TEXT = colors.HexColor("#332d27")
MUTED = colors.HexColor("#786f67")
BORDER = colors.HexColor("#e4ddd4")
SOFT = colors.HexColor("#f7f3ed")

TITLE = ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=TEXT)
HEADING = ParagraphStyle(
    "Heading",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=16,
    textColor=TEXT,
    spaceBefore=14,
    spaceAfter=6,
)
BODY = ParagraphStyle("Body", fontName="Helvetica", fontSize=10, leading=13, textColor=TEXT)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=9, leading=12, textColor=MUTED)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=9, leading=11)


def _table(rows, widths, *, right_columns=()):
    table = Table(rows, colWidths=widths, repeatRows=1)
    commands = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    commands += [("ALIGN", (column, 0), (column, -1), "RIGHT") for column in right_columns]
    table.setStyle(TableStyle(commands))
    return table


def render_expenses_pdf(group, viewer):
    expenses = list(group.expenses.prefetch_related("shares").order_by("spent_on", "created_at", "id"))
    balance_map = balances(group)
    member_ids = list(group.members.values_list("user_id", flat=True))
    people = {pk for pk in [*member_ids, *balance_map] if pk}
    people |= {expense.paid_by_id for expense in expenses if expense.paid_by_id}
    users = get_user_model().objects.filter(pk__in=people).select_related("profile")
    names = {user.pk: display_name(user) for user in users}

    def name(user_id):
        return names.get(user_id, "Gelöschtes Konto")

    story = [
        Paragraph(escape(group.name), TITLE),
        Spacer(1, 4),
        Paragraph(
            escape(
                f"Abrechnung vom {format_user_date(timezone.now(), viewer)} · "
                f"{', '.join(name(pk) for pk in member_ids)}"
            ),
            SMALL,
        ),
        Paragraph("Stand", HEADING),
    ]
    rows = [["Person", "Bezahlt minus Anteil"]]
    rows += [[name(pk), euro(balance_map.get(pk, 0))] for pk in member_ids]
    rows += [[name(pk), euro(total)] for pk, total in balance_map.items() if pk not in member_ids]
    story.append(_table(rows, [110 * mm, 60 * mm], right_columns=(1,)))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Positiv heißt: bekommt Geld zurück. Negativ heißt: schuldet noch etwas.", SMALL))

    story.append(Paragraph("So gleicht ihr aus", HEADING))
    plan = settle_up_plan(balance_map)
    for debtor, creditor, amount in plan:
        story.append(Paragraph(escape(f"{name(debtor)} zahlt {name(creditor)} {euro(amount)}"), BODY))
    if not plan:
        story.append(Paragraph("Alles ausgeglichen.", BODY))

    story.append(Paragraph("Ausgaben", HEADING))
    rows = [["Datum", "Wofür", "Kategorie", "Bezahlt von", "Betrag"]]
    for expense in expenses:
        rows.append(
            [
                format_user_date(expense.spent_on, viewer),
                Paragraph(escape(expense.title), CELL),
                "Ausgleich"
                if expense.is_settlement
                else Paragraph(escape(expense.get_category_display()), CELL),
                Paragraph(escape(name(expense.paid_by_id)), CELL),
                euro(expense.amount),
            ]
        )
    spent = sum((expense.amount for expense in expenses if not expense.is_settlement), start=0)
    rows.append(["", "Summe der Ausgaben", "", "", euro(spent)])
    table = _table(rows, [24 * mm, 62 * mm, 34 * mm, 30 * mm, 24 * mm], right_columns=(4,))
    table.setStyle(TableStyle([("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9)]))
    story.append(table)

    stream = BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Abrechnung {group.name}",
        creator="Lunora",
    )
    document.build(story)
    return stream.getvalue()
