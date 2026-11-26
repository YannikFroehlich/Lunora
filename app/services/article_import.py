"""A web article as a note: "Aus Webseite" in the new-note dialog and on the share page.

The page is fetched through the SSRF-safe fetcher and read with the stdlib HTML parser: headings,
paragraphs, lists, quotes and code from the page's main <article> (the one with the most text), else
its <main>, else the whole page, without navigation, scripts, forms, pictures or formatting. The
note ends with a link to the source. Nothing else of the page is kept.
"""

from html.parser import HTMLParser

from django.core.exceptions import ValidationError

from app.services import safe_fetch
from app.services.note_content import document_plain_text, validate_note_document
from app.services.notes import create_note
from app.services.recipe_import import _read_page
from app.services.throttle import count_hit

USER_AGENT = "Lunora-Notizen/1.0"
IMPORTS_PER_WINDOW = 10
IMPORT_WINDOW_SECONDS = 600
MAX_ARTICLE_CHARACTERS = 200_000
MAX_BLOCKS = 1500
MAX_TITLE = 200

SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "select",
    "dialog",
    "svg",
    "iframe",
    "figure",
}
TYPED_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"}
BREAK_TAGS = TYPED_TAGS | {"p", "div", "section", "article", "main", "br", "td", "th", "dd", "dt", "tr"}
HEADING_LEVELS = {"h1": 2, "h2": 2, "h3": 3, "h4": 3, "h5": 3, "h6": 3}


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.title = ""
        self.og_title = ""
        self._buffer = []
        self._skip = 0
        self._in_title = False
        self._typed = []
        self._lists = []
        self._containers = []
        self._next_id = 0

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            values = dict(attrs)
            if (values.get("property") or "").lower() == "og:title" and not self.og_title:
                self.og_title = " ".join((values.get("content") or "").split())
            return
        if self._skip or tag in SKIP_TAGS:
            if tag in SKIP_TAGS:
                self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in BREAK_TAGS:
            self._flush()
        if tag in TYPED_TAGS:
            self._typed.append(tag)
        if tag in {"ul", "ol"}:
            self._flush()
            self._next_id += 1
            self._lists.append((self._next_id, tag == "ol"))
        if tag in {"article", "main"}:
            self._next_id += 1
            self._containers.append((tag, self._next_id))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        if tag in BREAK_TAGS:
            self._flush()
        if tag in TYPED_TAGS and tag in self._typed:
            del self._typed[len(self._typed) - 1 - self._typed[::-1].index(tag)]
        if tag in {"ul", "ol"} and self._lists:
            self._lists.pop()
        if tag in {"article", "main"} and self._containers:
            self._containers.pop()

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = " ".join(data.split())
        elif not self._skip and not self._in_title:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()

    def _flush(self):
        raw = "".join(self._buffer)
        self._buffer = []
        kind = self._typed[-1] if self._typed else "p"
        text = raw.strip("\n") if kind == "pre" else " ".join(raw.split())
        if not text.strip() or len(self.blocks) >= MAX_BLOCKS:
            return
        self.blocks.append(
            {
                "kind": kind,
                "text": text,
                "list": self._lists[-1] if kind == "li" and self._lists else None,
                "containers": list(self._containers),
            }
        )


def _main_blocks(blocks):
    """The blocks of the <article> with the most text, else of <main>, else all of them."""
    for tag in ("article", "main"):
        sizes = {}
        for block in blocks:
            for container in block["containers"]:
                if container[0] == tag:
                    sizes[container] = sizes.get(container, 0) + len(block["text"])
        if sizes:
            best = max(sizes, key=sizes.get)
            return [block for block in blocks if best in block["containers"]]
    return blocks


def _text(text):
    return [{"type": "text", "text": text}]


def article_document(blocks, url):
    content = []
    characters = 0
    for block in blocks:
        characters += len(block["text"])
        if characters > MAX_ARTICLE_CHARACTERS:
            break
        kind, text = block["kind"], block["text"]
        if kind in HEADING_LEVELS:
            content.append(
                {"type": "heading", "attrs": {"level": HEADING_LEVELS[kind]}, "content": _text(text)}
            )
        elif kind == "li" and block["list"]:
            list_id, ordered = block["list"]
            item = {"type": "listItem", "content": [{"type": "paragraph", "content": _text(text)}]}
            previous = content[-1] if content else None
            if previous and previous.get("_list") == list_id:
                previous["content"].append(item)
            else:
                list_type = "orderedList" if ordered else "bulletList"
                content.append({"type": list_type, "_list": list_id, "content": [item]})
        elif kind == "blockquote":
            content.append({"type": "blockquote", "content": [{"type": "paragraph", "content": _text(text)}]})
        elif kind == "pre":
            content.append({"type": "codeBlock", "content": _text(text)})
        else:
            content.append({"type": "paragraph", "content": _text(text)})
    for node in content:
        node.pop("_list", None)
    source = {"type": "text", "text": url, "marks": [{"type": "link", "attrs": {"href": url}}]}
    content += [{"type": "horizontalRule"}, {"type": "paragraph", "content": [*_text("Quelle: "), source]}]
    return {"type": "doc", "content": content}


def parse_article(page, url):
    """(title, document) of the article on `page`; raises FetchError when it has no text."""
    parser = ArticleParser()
    parser.feed(page)
    parser.close()
    blocks = _main_blocks(parser.blocks)
    if not any(block["kind"] != "li" for block in blocks):
        raise safe_fetch.FetchError("Auf der Seite wurde kein Artikeltext gefunden.")
    headings = [block["text"] for block in blocks if block["kind"] == "h1"]
    title = (parser.og_title or parser.title or (headings[0] if headings else "") or url)[:MAX_TITLE]
    # The title is the note's own; a first heading repeating it would show it twice.
    if blocks and blocks[0]["kind"] in HEADING_LEVELS and blocks[0]["text"] == title:
        blocks = blocks[1:]
    return title, article_document(blocks, url)


def import_article(user, raw_url, *, folder_id=None):
    """A new note of `user` holding the article at `raw_url`; raises FetchError or ValidationError.

    Throttled per user, because every import makes the server fetch a page of the user's choosing."""
    url = safe_fetch.normalize_url(raw_url)
    if count_hit(f"notes:article-imports:{user.pk}", IMPORT_WINDOW_SECONDS) > IMPORTS_PER_WINDOW:
        raise safe_fetch.FetchError(
            "Zu viele Importe auf einmal. Versuch es in ein paar Minuten noch einmal."
        )
    page = safe_fetch.fetch(
        url,
        _read_page,
        user_agent=USER_AGENT,
        accept="text/html, application/xhtml+xml",
        server="Die Webseite",
    )
    title, document = parse_article(page, url)
    try:
        validate_note_document(document)
    except ValidationError as error:
        raise safe_fetch.FetchError("Der Artikel lässt sich nicht als Notiz speichern.") from error
    note = create_note(user, title=title, folder_id=folder_id)
    note.document = document
    note.plain_text = document_plain_text(document)
    note.save(update_fields=["document", "plain_text"])
    return note
