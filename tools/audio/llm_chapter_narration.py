#!/usr/bin/env python3
"""LLM chapter-based narration for one article.

Flow:
  HTML -> top-level chapter source notes -> LLM verbal txt per chapter
       -> TTS mp3 per chapter -> ffprobe duration -> concat full mp3
       -> JSON chapter manifest with exact start times.

This is the flow needed for precise previous/next chapter audio controls.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

# Reuse the source-note and LLM prompting helpers from the non-chapter prototype.
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from llm_verbalize import block_to_source, build_prompt, clean, run_hermes, slug_for_html  # noqa: E402

VOICE = "en-US-AriaNeural"
MAX_TTS_CHUNK = 2200


def heading_label(text: str) -> str:
    text = clean(text)
    text = re.sub(r"^§\s*\d+\.?\s*", "", text)
    return text.strip(" .") or "Untitled"


def extract_chapters(path: Path) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    title_el = soup.find(class_="article-title")
    title = clean(title_el.get_text(" ", strip=True)) if title_el else path.parent.name
    content = soup.find(class_="article-content")
    if not content:
        raise RuntimeError(f"No .article-content in {path}")

    chapters = []
    current = {"title": "Intro", "source": []}

    def flush():
        source = "\n\n".join(x for x in current["source"] if x.strip()).strip()
        if source:
            chapters.append({"title": current["title"], "source": source})

    for child in content.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        # Treat h2 as the audio chapter boundary. h3/h4 stay inside the chapter.
        if name == "h2":
            flush()
            current = {"title": heading_label(child.get_text(" ", strip=True)), "source": []}
            current["source"].append("\n## " + clean(child.get_text(" ", strip=True)) + "\n")
        else:
            current["source"].extend(block_to_source(child))
    flush()

    if not chapters:
        raise RuntimeError("No chapters extracted")
    return title, chapters


def split_tts_chunks(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    chunks, cur = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(cur) + len(s) + 1 > MAX_TTS_CHUNK and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += " " + s
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


async def tts_text(text: str, out: Path) -> None:
    chunks = split_tts_chunks(text)
    parts = [out.with_suffix(f".part{i}.mp3") for i in range(len(chunks))]
    for p in parts:
        p.unlink(missing_ok=True)
    for i, (chunk, part) in enumerate(zip(chunks, parts), start=1):
        print(f"  tts chunk {i}/{len(chunks)} chars={len(chunk)}", file=sys.stderr)
        await edge_tts.Communicate(chunk, VOICE).save(str(part))
    if len(parts) == 1:
        parts[0].replace(out)
        return
    concat_mp3(parts, out)


def concat_mp3(parts: list[Path], out: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        listfile = Path(f.name)
        for p in parts:
            f.write(f"file '{p}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out)],
            check=True,
        )
    finally:
        listfile.unlink(missing_ok=True)
        for p in parts:
            p.unlink(missing_ok=True)


def duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
        capture_output=True,
        check=True,
    )
    return float(p.stdout.strip())


def safe_name(i: int, title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"{i:02d}-{slug or 'chapter'}"


async def main_async(args) -> int:
    html = Path(args.html)
    if not html.is_absolute():
        html = REPO / html
    title, chapters = extract_chapters(html)
    slug = slug_for_html(html)

    out_dir = Path(args.out_dir).resolve()
    chapter_dir = out_dir / "chapters"
    script_dir = out_dir / "scripts"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    chapter_audio_paths = []
    manifest_chapters = []
    start = 0.0

    for i, chapter in enumerate(chapters, start=1):
        name = safe_name(i, chapter["title"])
        script_path = script_dir / f"{name}.txt"
        mp3_path = chapter_dir / f"{name}.mp3"

        if args.reuse_scripts and script_path.exists():
            script = script_path.read_text(encoding="utf-8").strip()
        else:
            source_words = len(chapter["source"].split())
            target_words = args.words_per_chapter
            if target_words is None:
                target_words = max(180, int(source_words * args.compression))
            prompt = build_prompt(
                title=f"{title} — {chapter['title']}",
                source=chapter["source"],
                max_words=target_words,
                part=i,
                total=len(chapters),
            )
            print(f"LLM chapter {i}/{len(chapters)}: {chapter['title']} source_words={source_words} target_words={target_words} source_chars={len(chapter['source'])}", file=sys.stderr)
            script = run_hermes(prompt).strip()
            script_path.write_text(script + "\n", encoding="utf-8")

        print(f"TTS chapter {i}/{len(chapters)}: {chapter['title']} words={len(script.split())}", file=sys.stderr)
        await tts_text(script, mp3_path)
        d = duration(mp3_path)
        chapter_audio_paths.append(mp3_path)
        manifest_chapters.append({
            "title": chapter["title"],
            "start": round(start, 3),
            "duration": round(d, 3),
            "script": str(script_path),
            "audio": str(mp3_path),
        })
        start += d

    full_mp3 = out_dir / f"{slug}.mp3"
    concat_mp3(chapter_audio_paths, full_mp3)
    total = duration(full_mp3)

    manifest = {
        "slug": slug,
        "title": title,
        "audio": str(full_mp3),
        "duration": round(total, 3),
        "chapter_level": "h2",
        "chapters": manifest_chapters,
    }
    manifest_path = out_dir / f"{slug}.chapters.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(full_mp3)
    print(manifest_path)
    print(f"chapters={len(manifest_chapters)} duration={total:.3f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out-dir", default="/Users/f/Downloads/llm-chapter-narration")
    ap.add_argument("--words-per-chapter", type=int, default=None,
                    help="fixed target words per chapter; default scales with source length")
    ap.add_argument("--compression", type=float, default=0.55,
                    help="target output words as a fraction of source-note words when --words-per-chapter is omitted")
    ap.add_argument("--reuse-scripts", action="store_true")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
