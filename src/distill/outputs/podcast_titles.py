"""Read generated title metadata without treating spoken dialogue as a title."""

import re
from pathlib import Path

from distill.processing.text import plain_text_excerpt


def extract_title(script: str) -> str | None:
    preamble = re.split(r"\[(?:Alex|Sarah)\]|<Person[12]>", script, maxsplit=1, flags=re.I)[0]
    match = re.search(r"^TITLE:[ \t]*([^\n]+)$", preamble, flags=re.M | re.I)
    if not match:
        return None
    title = " ".join(match[1].split()).strip('"\u201c\u201d')
    if not 5 <= len(title) <= 100 or any(char in title for char in "<>[]"):
        return None
    return title


def episode_title(script_path: Path, article_titles: list[str]) -> str:
    if script_path.is_file():
        title = extract_title(script_path.read_text())
        if title:
            return title
    if article_titles:
        return plain_text_excerpt(article_titles[0], 90) or "AI engineering briefing"
    return "AI engineering briefing"
