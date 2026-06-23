#!/usr/bin/env python3
"""Generate an LLM-written conversational narration script for one article.

Flow:
  HTML article -> structured source notes -> Hermes LLM rewrite -> hidden script

This is intentionally separate from extract_verbal.py. The rule-based extractor
is deterministic but stiff; this script asks an LLM to rewrite the article into a
spoken script while preserving technical facts.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

REPO = Path(__file__).resolve().parents[2]
CACHE = Path(__file__).resolve().parent / "llm_verbal_cache"
PROMPT_LIMIT_CHARS = 200000
CHUNK_TARGET_CHARS = 18000


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = s.replace("\u00a0", " ")
    return s


def slug_for_html(path: Path) -> str:
    rel = path.relative_to(REPO).as_posix()
    rel = re.sub(r"/?index\.html$", "", rel)
    rel = re.sub(r"\.html$", "", rel)
    return rel.replace("/", "__")


def block_to_source(tag: Tag) -> list[str]:
    name = tag.name.lower()
    out: list[str] = []
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        txt = clean(tag.get_text(" ", strip=True))
        if txt:
            out.append(f"\n## {txt}\n")
    elif name in {"p", "blockquote"}:
        txt = clean(tag.get_text(" ", strip=True))
        if txt:
            out.append(txt)
    elif name in {"ul", "ol"}:
        items = []
        for li in tag.find_all("li", recursive=False):
            txt = clean(li.get_text(" ", strip=True))
            if txt:
                items.append(f"- {txt}")
        if items:
            out.append("\n".join(items))
    elif name == "table":
        rows = []
        for tr in tag.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            out.append("[TABLE]\n" + "\n".join(rows[:40]) + ("\n[/TABLE]"))
    elif name == "pre":
        code = clean(tag.get_text(" ", strip=True))
        if code:
            out.append("[CODE BLOCK]\n" + code[:1600] + ("..." if len(code) > 1600 else "") + "\n[/CODE BLOCK]")
    return out


def extract_source(path: Path) -> tuple[str, str]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    title_el = soup.find(class_="article-title")
    title = clean(title_el.get_text(" ", strip=True)) if title_el else path.parent.name
    content = soup.find(class_="article-content")
    if not content:
        raise RuntimeError(f"No .article-content in {path}")

    blocks: list[str] = []
    for child in content.children:
        if isinstance(child, NavigableString):
            continue
        if isinstance(child, Tag):
            blocks.extend(block_to_source(child))
    source = "\n\n".join(b for b in blocks if b.strip())
    return title, source[:PROMPT_LIMIT_CHARS]


def split_source(source: str, target_chars: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Split source notes on heading boundaries so LLM output doesn't truncate."""
    parts = re.split(r"(?=\n## )", "\n" + source)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if cur and len(cur) + len(part) + 2 > target_chars:
            chunks.append(cur.strip())
            cur = part
        else:
            cur = (cur + "\n\n" + part).strip() if cur else part
    if cur:
        chunks.append(cur.strip())
    return chunks


def build_prompt(title: str, source: str, max_words: int | None, part: int = 1, total: int = 1) -> str:
    length = (
        f"Aim for about {max_words} words for this part."
        if max_words
        else "Make this part concise enough for audio, but cover the main technical ideas."
    )
    part_line = f"This is part {part} of {total} of the article. Write only this part; do not summarize the missing parts." if total > 1 else ""
    return f"""
You are rewriting a technical blog article into a spoken narration script for TTS.

Goal: make it sound like a senior backend engineer casually explaining the topic to another engineer.

Style requirements:
- This is NOT a summary. Preserve the technical depth and the important details from the source.
- Conversational, clear, and natural, but information-dense. Sound like a senior backend engineer explaining the material carefully.
- Do not add filler, hype, or rhetorical questions. Avoid phrases like "here's the thing", "where it gets interesting", "fun part", "wild", "okay", "let's dig in", or "how far does this go" unless they carry real information.
- Do not sound like you are reading a document. Sound like you are teaching it out loud.
- Keep the technical accuracy. Do not invent facts not supported by the source.
- Keep important terms, numbers, and mechanisms: Redis, Memcached, TTL, LRU, consistency, sharding, replication, p99, QPS, memory costs, failure windows, and so on.
- For code blocks: do NOT read syntax line by line. Explain what the code/config/protocol block demonstrates, including the important fields, parameters, and failure implications.
- For tables: do NOT skip them and do NOT read cells mechanically. Convert every substantive row/comparison into prose, grouping related rows where it sounds natural.
- For dense bullet lists: preserve the content, but group related items and explain the distinction naturally. Avoid rattling off long vendor lists unless the vendors are the point.
- Prefer short sentences, but keep details. Use contractions where natural.
- Plain paragraphs only. No markdown tables. No bullet lists unless the source is explicitly a checklist and bullets are clearer.
- Output only the narration script. No preface, no analysis.

Length: {length}
{part_line}

Title: {title}

SOURCE ARTICLE NOTES:
{source}
""".strip()


def run_hermes(prompt: str) -> str:
    p = subprocess.run(
        ["hermes", "chat", "-q", prompt, "-Q", "--source", "tool"],
        text=True,
        capture_output=True,
        timeout=900,
        cwd=str(REPO),
    )
    if p.returncode != 0:
        raise RuntimeError(f"hermes failed: {p.stderr or p.stdout}")
    out = p.stdout.strip()
    # Some CLIs may echo if '-' is not stdin; fail clearly.
    if out == "-" or len(out) < 200:
        raise RuntimeError(f"unexpected short Hermes output: {out[:200]!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html", help="article HTML path, e.g. backend-fundamentals/cache/index.html")
    ap.add_argument("--max-words", type=int, default=None, help="target narration length")
    ap.add_argument("--out", default=None, help="output .txt path")
    args = ap.parse_args()

    path = Path(args.html)
    if not path.is_absolute():
        path = REPO / path
    title, source = extract_source(path)
    chunks = split_source(source)
    per_part_words = None
    if args.max_words:
        per_part_words = max(250, args.max_words // max(1, len(chunks)))

    scripts = []
    for i, chunk in enumerate(chunks, start=1):
        prompt = build_prompt(title, chunk, per_part_words, i, len(chunks))
        print(f"LLM part {i}/{len(chunks)}: source_chars={len(chunk)}", file=sys.stderr)
        scripts.append(run_hermes(prompt))
    script = "\n\n".join(s.strip() for s in scripts if s.strip())

    out = Path(args.out) if args.out else CACHE / f"{slug_for_html(path)}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(script.rstrip() + "\n", encoding="utf-8")
    print(out)
    print(f"words={len(script.split())} chars={len(script)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
