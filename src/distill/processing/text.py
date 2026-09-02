import html
import re


def plain_text(value: str | None) -> str:
    """Convert an HTML fragment into normalized plain text."""
    if not value:
        return ""
    decoded = html.unescape(value)
    without_tags = re.sub(r"<[/!A-Za-z][^>]*(?:>|$)", " ", decoded)
    return " ".join(html.unescape(without_tags).split())


def plain_text_excerpt(value: str | None, limit: int = 300) -> str:
    """Return a word-boundary plain-text excerpt."""
    text = plain_text(value)
    if len(text) <= limit:
        return text
    shortened = text[: limit + 1].rsplit(" ", 1)[0]
    return f"{shortened or text[:limit]}..."
