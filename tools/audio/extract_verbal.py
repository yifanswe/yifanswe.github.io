#!/usr/bin/env python3
"""Generate a spoken-first narration script from a blog article HTML file.

Unlike extract_text.py, this does NOT try to preserve the reading layout. It
rewrites HTML structure into a verbal script for TTS:
- headings become spoken section transitions;
- lists become "First... Second..." narration;
- tables become summarized row descriptions;
- code blocks are acknowledged but not read as syntax noise.

The output is meant for hidden/local narration cache and TTS input, not for the
public blog UI.
"""
import html as _html
import re
import sys
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

_SYMBOL_MAP = {
    "→": " to ", "←": " back to ", "↔": " and ", "⇒": " implies ",
    "⟶": " to ", "·": ". ", "•": " ", "≈": " approximately ",
    "≤": " at most ", "≥": " at least ", "≠": " not equal to ",
    "×": " times ", "—": ", ", "–": ", ", "…": ". ",
    "∞": " infinity ", "%": " percent ", "&": " and ",
    "≫": " much greater than ", "≪": " much less than ",
    "<": " less than ", ">": " greater than ", "±": " plus or minus ",
    "≡": " equivalent to ",
}

_ORDINALS = [
    "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh",
    "Eighth", "Ninth", "Tenth", "Next", "Next", "Next", "Next", "Next",
]


def _speakify(text: str) -> str:
    text = re.sub(r"~\s*(\d)", r"about \1", text)
    unit_word = {
        "ms": "millisecond", "sec": "second", "s": "second",
        "min": "minute", "hr": "hour", "hour": "hour",
        "day": "day", "yr": "year", "year": "year", "mo": "month",
    }

    def _slash_unit(m):
        unit = m.group(1) or ""
        return " per " + unit_word.get(unit.lower(), unit)

    text = re.sub(r"/(ms|sec|min|hr|hour|day|yr|year|mo|s)\b", _slash_unit, text)
    text = re.sub(r"\breqs?\b", "requests", text)
    text = re.sub(r"\b([A-Za-z]{2,})/([A-Za-z]{2,})\b", r"\1 or \2", text)
    text = re.sub(r"\s+/\s+", " or ", text)
    return text


def _clean(text: str) -> str:
    text = _html.unescape(text or "")
    for sym, word in _SYMBOL_MAP.items():
        text = text.replace(sym, word)
    text = text.replace("*", " ").replace("`", " ").replace("#", " ")
    text = text.replace("_", " ").replace("|", " ")
    text = re.sub(r"(^|[.\s])[-–—]\s+", r"\1", text)
    text = re.sub(r"\s+[-–—]\s+", ", ", text)
    text = _speakify(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"(^|[\s])[,;:]\s*", r"\1", text)
    return text.strip()


def _sentence(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    if text[-1] not in ".!?:;,":
        text += "."
    return text


def _inline_text(node: Tag) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    for noisy in clone.find_all(["pre", "table", "script", "style"]):
        noisy.decompose()
    body = clone.find("body") or clone
    return _clean(body.get_text(" ", strip=True))


def _direct_text(tag: Tag) -> str:
    parts = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name.lower() not in {
            "ul", "ol", "table", "pre", "script", "style"
        }:
            parts.append(child.get_text(" ", strip=True))
    return _clean(" ".join(parts))


def _looks_like_example_fragment(sentence: str) -> bool:
    """Heuristic for vendor/example fragments that sound bad in audio.

    Reading "Redis, Memcached, Aerospike, Hazelcast..." aloud is useful on the
    page but choppy in narration. Prefer the conceptual sentence that follows.
    """
    words = sentence.split()
    comma_count = sentence.count(",")
    return comma_count >= 3 and len(words) <= 18


def _first_strong_text(li: Tag) -> str:
    first = li.find("strong", recursive=False)
    if not first:
        return ""
    return _clean(first.get_text(" ", strip=True)).strip(" .,:;-")


def _compact_list_item(li: Tag) -> str:
    label = _first_strong_text(li)
    full = _direct_text(li) or _inline_text(li)
    if not full:
        return ""

    # If the item starts with a bold label, treat that as the spoken concept and
    # summarize the body instead of reading every inline example/vendor name.
    if label and full.startswith(label):
        rest = full[len(label):].strip(" ,:;.-")
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", rest) if s.strip()]
        sentences = [s for s in sentences if not _looks_like_example_fragment(s)]
        if sentences:
            return f"{label}: {sentences[0]}"
        return label

    return full


def _render_list(tag: Tag) -> list[str]:
    items = []
    for li in tag.find_all("li", recursive=False):
        txt = _compact_list_item(li)
        if txt:
            items.append(txt)
    if not items:
        return []

    # Long dense lists are easier to listen to if framed as a short tour rather
    # than as a literal page structure.
    lines = [f"The important points are these."] if len(items) > 1 else []
    for i, item in enumerate(items):
        prefix = _ORDINALS[i] if i < len(_ORDINALS) else "Next"
        lines.append(_sentence(f"{prefix}, {item}"))
    return lines


def _render_table(table: Tag) -> list[str]:
    rows = table.find_all("tr")
    if not rows:
        return []

    headers = []
    thead = table.find("thead")
    if thead:
        headers = [_clean(c.get_text(" ", strip=True)) for c in thead.find_all(["th", "td"])]
    else:
        ths = rows[0].find_all("th")
        if ths:
            headers = [_clean(th.get_text(" ", strip=True)) for th in ths]

    tbody = table.find("tbody")
    body_rows = tbody.find_all("tr") if tbody else rows
    lines = ["The article includes a table here. I will summarize it in spoken form instead of reading the grid literally."]

    for tr in body_rows:
        cells = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if not cells:
            continue
        # Skip pure header row if it exactly matches the headers.
        if headers and cells == headers:
            continue
        if headers and len(headers) == len(cells):
            subject = cells[0]
            rest = []
            for h, c in zip(headers[1:], cells[1:]):
                if h and c:
                    rest.append(f"{h}: {c}")
            if rest:
                lines.append(_sentence(f"For {subject}, " + "; ".join(rest)))
            else:
                lines.append(_sentence("; ".join(cells)))
        else:
            lines.append(_sentence("; ".join(cells)))
    return lines


def _render_children(parent: Tag) -> list[str]:
    lines = []
    for child in parent.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in {"script", "style"}:
            continue
        if name == "pre":
            lines.append("The original article includes a code or configuration block here. I am skipping the raw syntax in the audio version; please refer to the page for the exact code.")
        elif name == "table":
            lines.extend(_render_table(child))
        elif name in {"ul", "ol"}:
            lines.extend(_render_list(child))
        elif name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            txt = _inline_text(child)
            if txt:
                lines.append(_sentence(f"Section: {txt}"))
        elif name in {"p", "blockquote"}:
            txt = _inline_text(child)
            if txt:
                lines.append(_sentence(txt))
        elif name == "li":
            txt = _direct_text(child) or _inline_text(child)
            if txt:
                lines.append(_sentence(txt))
            lines.extend(_render_children(child))
        else:
            lines.extend(_render_children(child))
    return lines


def extract(path: str) -> tuple[str, str]:
    raw = open(path, encoding="utf-8").read()
    soup = BeautifulSoup(raw, "lxml")
    title_el = soup.find(class_="article-title")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    content = soup.find(class_="article-content")
    if not content:
        return title, ""

    lines = []
    if title:
        lines.append(_sentence(f"This is an audio version of the article: {title}"))
    lines.extend(_render_children(content))

    out = []
    for line in lines:
        line = _sentence(line)
        if not line:
            continue
        if out and out[-1] == line:
            continue
        out.append(line)

    text = "\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return title, text.strip()


if __name__ == "__main__":
    p = sys.argv[1]
    title, text = extract(p)
    print(f"### TITLE: {title}")
    print(f"### CHARS: {len(text)}")
    print("### TEXT:")
    print(text)
