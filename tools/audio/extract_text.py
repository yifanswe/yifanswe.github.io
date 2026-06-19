#!/usr/bin/env python3
"""Extract clean, speakable English text from a blog article HTML file.

Rules (per user's instructions):
- Only the .article-content body is read (skip nav/footer/meta).
- <pre> code blocks are skipped entirely (no symbol noise).
- <table> is read row by row, turned into natural sentences.
- Inline <code>, <strong>, <em> keep their TEXT, drop the markup.
- No asterisks, backticks, list bullets, or stray punctuation get spoken.
- HTML entities decoded; arrows/symbols mapped to words or dropped.
"""
import re
import sys
import html as _html
from bs4 import BeautifulSoup, NavigableString, Tag


# Symbol -> spoken word (or space). Keeps TTS from reading raw glyphs.
_SYMBOL_MAP = {
    "→": " to ", "←": " ", "↔": " ", "⇒": " implies ", "⟶": " to ",
    "·": ". ", "•": " ", "≈": " approximately ", "≤": " at most ",
    "≥": " at least ", "≠": " not equal to ", "×": " times ",
    "—": ", ", "–": ", ", "…": ". ", "∞": " infinity ",
    "%": " percent ", "&": " and ", "≫": " much greater than ",
    "≪": " much less than ", "<": " less than ", ">": " greater than ",
    "±": " plus or minus ", "≡": " equivalent to ",
}


def _speakify(text: str) -> str:
    """Expand abbreviations/units that TTS otherwise mangles.

    Conservative, word-boundary based. Leaves acronyms TTS already says
    well (API, IP, QPS, HTTP, JSON, SQL, numbers like 429) untouched.
    """
    # "~45" / "~5 min" -> "about 45"
    text = re.sub(r"~\s*(\d)", r"about \1", text)
    # rate units: "/min", "/sec", "/hour", "/day", "/s", "/ms"
    unit_word = {
        "ms": "millisecond", "sec": "second", "s": "second",
        "min": "minute", "hr": "hour", "hour": "hour",
        "day": "day", "yr": "year", "year": "year", "mo": "month",
    }
    def _slash_unit(m):
        return " per " + unit_word.get(m.group(1).lower(), m.group(1))
    text = re.sub(r"/(ms|sec|min|hr|hour|day|yr|year|mo|s)\b", _slash_unit, text)
    # "req" as a standalone token -> "requests"
    text = re.sub(r"\breq\b", "requests", text)
    text = re.sub(r"\breqs\b", "requests", text)
    # generic "X/Y" slash between words -> "X or Y" (allow/deny, read/write)
    text = re.sub(r"\b([A-Za-z]{2,})/([A-Za-z]{2,})\b", r"\1 or \2", text)
    # leftover lone slash with spaces
    text = re.sub(r"\s+/\s+", " or ", text)
    return text


def _clean_inline(text: str) -> str:
    """Normalise a run of inline text into speakable form."""
    text = _html.unescape(text)
    for sym, word in _SYMBOL_MAP.items():
        text = text.replace(sym, word)
    # Drop characters that are pure markup noise when spoken.
    text = text.replace("*", " ").replace("`", " ").replace("#", " ")
    text = text.replace("_", " ").replace("|", " ")
    # Turn list-leading dashes ("- item") and inline " - " into pauses,
    # but keep hyphens inside words (e.g. "per-tenant", "read-modify-write").
    text = re.sub(r"(^|[.\s])[-–—]\s+", r"\1", text)   # leading bullet dash
    text = re.sub(r"\s+[-–—]\s+", ", ", text)          # " - " separator -> pause
    # Expand abbreviations/units for natural speech.
    text = _speakify(text)
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    # Tidy spaced/duplicated punctuation: " ," -> ",", ".." -> ".", ", ." -> "."
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"(^|[\s])[,;:]\s*", r"\1", text)    # orphan punctuation at start
    return text.strip()


def _table_to_sentences(table: Tag) -> str:
    """Turn a table into row-by-row natural sentences.

    Strategy: read header cells, then for each body row emit
    "Header1: cell1. Header2: cell2. ..." so it is followable by ear.
    """
    rows = table.find_all("tr")
    if not rows:
        return ""
    headers = []
    header_row = table.find("thead")
    if header_row:
        headers = [
            _clean_inline(th.get_text(" ", strip=True))
            for th in header_row.find_all(["th", "td"])
        ]
    else:
        # First row might be headers via <th>
        first = rows[0]
        ths = first.find_all("th")
        if ths:
            headers = [_clean_inline(th.get_text(" ", strip=True)) for th in ths]

    body_rows = table.find("tbody").find_all("tr") if table.find("tbody") else rows
    sentences = ["Here is a table."]
    for tr in body_rows:
        cells = [
            _clean_inline(td.get_text(" ", strip=True))
            for td in tr.find_all(["td", "th"])
        ]
        cells = [c for c in cells if c]
        if not cells:
            continue
        if headers and len(headers) == len(cells):
            parts = [f"{h}: {c}" for h, c in zip(headers, cells) if c]
            sentences.append(". ".join(parts) + ".")
        else:
            sentences.append(". ".join(cells) + ".")
    return " ".join(sentences)


def _block_text(node: Tag) -> str:
    """Recursively render a block-level element to speakable text."""
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            name = child.name.lower()
            if name == "pre":
                continue  # skip code blocks entirely
            if name == "table":
                parts.append(" " + _table_to_sentences(child) + " ")
            elif name in ("script", "style"):
                continue
            else:
                parts.append(_block_text(child))
    return "".join(parts)


def extract(path: str) -> tuple[str, str]:
    """Return (title, speakable_text) for an article HTML file."""
    raw = open(path, encoding="utf-8").read()
    soup = BeautifulSoup(raw, "lxml")

    title_el = soup.find(class_="article-title")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    content = soup.find(class_="article-content")
    if not content:
        return title, ""

    # Walk top-level block children, emit one cleaned line per block.
    lines = []
    if title:
        lines.append(_clean_inline(title) + ".")

    block_tags = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}
    handled = set()

    for el in content.find_all(recursive=True):
        if el.name == "pre":
            handled.update(id(d) for d in el.find_all(True))
            continue
        if el.name == "table":
            # Mark descendants handled so we don't double-read cells as <li>/<p>.
            handled.update(id(d) for d in el.find_all(True))
            lines.append(_clean_inline(_table_to_sentences(el)))
            continue
        if id(el) in handled:
            continue
        if el.name in block_tags:
            # Skip if inside a table/pre (already handled) or inside another li
            if el.find_parent("pre") or el.find_parent("table"):
                continue
            txt = _clean_inline(_block_text(el))
            if txt and len(txt) > 1:
                # Ensure sentence ends with terminal punctuation for TTS prosody.
                if txt[-1] not in ".!?:;,":
                    txt += "."
                lines.append(txt)

    # Deduplicate consecutive identical lines (nested elements can repeat).
    out = []
    for ln in lines:
        if out and out[-1] == ln:
            continue
        out.append(ln)

    text = "\n".join(out)
    # Final tidy: collapse repeated spaces/newlines, strip orphan punctuation.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"(^|\n)[\s.,:;]+", r"\1", text)
    return title, text.strip()


if __name__ == "__main__":
    p = sys.argv[1]
    title, text = extract(p)
    print(f"### TITLE: {title}")
    print(f"### CHARS: {len(text)}")
    print("### TEXT:")
    print(text)
