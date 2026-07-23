TABLE_LEAD_IN_MAX_PARAGRAPHS = 5
TABLE_LEAD_IN_MAX_CHARS = 300

TABLE_LEAD_IN_STOPWORDS = frozenset({
    "table of contents",
    "list of tables",
    "list of figures",
})


def is_lead_in(text: str) -> bool:
    if not text:
        return False

    normalized = " ".join(text.split())
    if len(normalized) < 4:
        return False

    lowered = normalized.lower()

    if lowered.replace("f", "").replace("-", "").replace(" ", "").isdigit():
        return False

    if lowered.rstrip("0123456789 .-") in TABLE_LEAD_IN_STOPWORDS:
        return False

    return True


def bounded_lead_in(paragraphs) -> str:
    selected = []
    total = 0

    for text in reversed(paragraphs):
        if total >= TABLE_LEAD_IN_MAX_CHARS:
            break
        snippet = text[: TABLE_LEAD_IN_MAX_CHARS - total]
        selected.append(snippet)
        total += len(snippet)

    selected.reverse()
    return " ".join(selected)
