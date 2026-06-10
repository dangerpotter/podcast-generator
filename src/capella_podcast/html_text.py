"""HTML -> plain text conversion for LLM input, preserving hyperlinks.

Every ``.text`` field in the Capella export is HTML. The LLM gets plain text;
the Recommended Resources section needs the link targets, so links are
extracted alongside the text.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_WS = re.compile(r"[ \t\xa0]+")
_NL = re.compile(r"\n{3,}")

_BLOCK_TAGS = (
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "br", "table",
)


def html_to_text(html: str | None) -> str:
    """Strip HTML to readable plain text (block tags become newlines)."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    text = _WS.sub(" ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = _NL.sub("\n\n", text)
    return text.strip()


def extract_links(html: str | None) -> list[dict[str, str]]:
    """Return [{"text": anchor text, "url": href}, ...] for real hyperlinks."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a in soup.find_all("a", href=True):
        url = a["href"].strip()
        if not url or url.startswith(("#", "mailto:")):
            continue
        text = _WS.sub(" ", a.get_text()).strip() or url
        key = (text, url)
        if key in seen:
            continue
        seen.add(key)
        links.append({"text": text, "url": url})
    return links
