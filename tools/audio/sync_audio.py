#!/usr/bin/env python3
"""Sync article narration audio to the GitHub Release after edits.

INPUT  : one or more article names (flexible: slug, HTML path, URL path, or
         a directory like "backend-fundamentals/cache"). Or --all to scan
         every narratable article.
OUTPUT : for each requested article, whether its Release audio was UPDATED,
         was already UP-TO-DATE, was newly ADDED, or FAILED — plus an exit
         code (0 = nothing needed or all succeeded, 1 = a failure occurred).

How "changed" is decided
------------------------
The audio is a function of the *verbal narration script* produced by
extract_verbal.py (NOT the raw HTML — nav/CSS/script tweaks must not trigger
regeneration). The verbal script rewrites reading-first structures such as code
blocks, tables, and lists into spoken-friendly narration. We sha256 that script
and compare against tools/audio/audio_state.json, the baseline written after the
last successful sync. Hash differs (or article is new) => regenerate + re-upload.
Hash matches => skip.

On success the baseline + audio-manifest.json are updated so the next run
sees the new state. Re-run is idempotent.

Examples
--------
  # check/update a single article you just edited
  python tools/audio/sync_audio.py backend-fundamentals/cache

  # several at once (any mix of forms)
  python tools/audio/sync_audio.py \
      backend-fundamentals/cache \
      /common-backend-systems/uber/ \
      system-design-patterns__hot-key-skew

  # scan everything, regenerate whatever drifted
  python tools/audio/sync_audio.py --all

  # see what WOULD change without touching the release
  python tools/audio/sync_audio.py --all --dry-run
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
from extract_verbal import extract  # noqa: E402

AUDIO_ROOT = os.path.join(REPO, "audio")
VERBAL_ROOT = os.path.join(HERE, "verbal_cache")
STATE_PATH = os.path.join(HERE, "audio_state.json")
MANIFEST_PATH = os.path.join(REPO, "audio-manifest.json")

VOICE = "en-US-AriaNeural"
EXTRACTOR = "extract_verbal_v1"
RELEASE_TAG = "audio-v1"
MAX_CHUNK = 2200
MIN_WORDS = 80
RETRIES = 3


# ── slug / path helpers ────────────────────────────────────────────────────
def norm_to_slug(token: str) -> str:
    """Turn any of {slug, html path, url path, dir} into the canonical slug.

    "backend-fundamentals/cache"            -> backend-fundamentals__cache
    "/backend-fundamentals/cache/"          -> backend-fundamentals__cache
    "backend-fundamentals/cache/index.html" -> backend-fundamentals__cache
    "backend-fundamentals__cache"           -> backend-fundamentals__cache
    "backend-fundamentals__cache.mp3"       -> backend-fundamentals__cache
    """
    t = token.strip()
    t = re.sub(r"\.mp3$", "", t)
    if "__" in t and "/" not in t:
        return t  # already a slug
    # strip a leading repo-absolute or site-absolute slash
    t = t.lstrip("/")
    # drop trailing index.html or just .html
    t = re.sub(r"/?index\.html$", "", t)
    t = re.sub(r"\.html$", "", t)
    t = t.strip("/")
    return t.replace("/", "__")


def html_path_for_slug(slug: str) -> str:
    """Map a slug back to its source HTML file (dir-style index.html)."""
    rel = slug.replace("__", "/")
    return os.path.join(REPO, rel, "index.html")


def audio_path_for_slug(slug: str) -> str:
    rel = slug.replace("__", "/")
    return os.path.join(AUDIO_ROOT, rel, "index.mp3")


def asset_name_for_slug(slug: str) -> str:
    return slug + ".mp3"


def verbal_path_for_slug(slug: str) -> str:
    return os.path.join(VERBAL_ROOT, slug + ".txt")


def write_verbal_cache(slug: str, text: str) -> None:
    path = verbal_path_for_slug(slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


# ── state ──────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        state = json.load(open(STATE_PATH))
        state.setdefault("release_tag", RELEASE_TAG)
        state.setdefault("voice", VOICE)
        state.setdefault("extractor", EXTRACTOR)
        state.setdefault("articles", {})
        return state
    return {"release_tag": RELEASE_TAG, "voice": VOICE, "extractor": EXTRACTOR, "articles": {}}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def write_manifest(state: dict) -> None:
    slugs = sorted(state["articles"].keys())
    with open(MANIFEST_PATH, "w") as f:
        json.dump(slugs, f, separators=(",", ":"))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── TTS (mirrors generate_audio_concurrent.py) ─────────────────────────────
def split_chunks(text, max_len=MAX_CHUNK):
    sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    chunks, cur = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_len:
            for p in re.split(r"(?<=[,;])\s+", s):
                if len(cur) + len(p) + 1 > max_len and cur:
                    chunks.append(cur.strip()); cur = ""
                cur += " " + p
            continue
        if len(cur) + len(s) + 1 > max_len and cur:
            chunks.append(cur.strip()); cur = ""
        cur += " " + s
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def concat_mp3(parts, out_path):
    if len(parts) == 1:
        os.replace(parts[0], out_path)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        listfile = f.name
        for p in parts:
            f.write(f"file '{p}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", listfile, "-c", "copy", out_path],
            check=True,
        )
    finally:
        os.unlink(listfile)
        for p in parts:
            if os.path.exists(p):
                os.unlink(p)


async def tts_chunk(sem, text, out_path):
    import edge_tts
    async with sem:
        last = None
        for attempt in range(RETRIES):
            try:
                comm = edge_tts.Communicate(text, VOICE)
                await comm.save(out_path)
                if os.path.getsize(out_path) > 0:
                    return
            except Exception as e:  # noqa
                last = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"chunk failed after {RETRIES} tries: {last}")


async def synth_article(slug, text, concurrency=8):
    out = audio_path_for_slug(slug)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    chunks = split_chunks(text)
    parts = [out + f".part{i}.mp3" for i in range(len(chunks))]
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*[tts_chunk(sem, c, p) for c, p in zip(chunks, parts)])
    concat_mp3(parts, out)
    return out, len(chunks)


# ── gh release ─────────────────────────────────────────────────────────────
def release_exists() -> bool:
    r = subprocess.run(["gh", "release", "view", RELEASE_TAG],
                       cwd=REPO, capture_output=True, text=True)
    return r.returncode == 0


def upload_asset(local_path, asset_name) -> None:
    """Upload (clobber) a single asset under a deterministic name."""
    # gh names the asset after the file's basename; stage a correctly-named copy.
    with tempfile.TemporaryDirectory() as td:
        staged = os.path.join(td, asset_name)
        # hardlink if possible, else copy
        try:
            os.link(local_path, staged)
        except OSError:
            import shutil
            shutil.copy2(local_path, staged)
        subprocess.run(
            ["gh", "release", "upload", RELEASE_TAG, staged, "--clobber"],
            cwd=REPO, check=True, capture_output=True, text=True,
        )


def verify_remote(asset_name, expected_size) -> bool:
    """Confirm the asset exists on the release with the right byte size."""
    r = subprocess.run(
        ["gh", "release", "view", RELEASE_TAG, "--json", "assets",
         "--jq", f'.assets[] | select(.name=="{asset_name}") | .size'],
        cwd=REPO, capture_output=True, text=True,
    )
    out = r.stdout.strip()
    return out.isdigit() and int(out) == expected_size


# ── core ───────────────────────────────────────────────────────────────────
def resolve_targets(args, state) -> list:
    if args.all:
        # every narratable article in the repo (dir/index.html with >=MIN_WORDS)
        slugs = set(state["articles"].keys())
        for root, _d, files in os.walk(REPO):
            if "/.git" in root or "/zh" in root or "/audio" in root:
                continue
            if "index.html" in files:
                hp = os.path.join(root, "index.html")
                try:
                    _t, text = extract(hp)
                except Exception:
                    continue
                if len(text.split()) >= MIN_WORDS:
                    slugs.add(norm_to_slug(os.path.relpath(hp, REPO)))
        return sorted(slugs)
    return [norm_to_slug(t) for t in args.articles]


async def process_one(slug, state, dry_run) -> dict:
    res = {"slug": slug, "status": "", "detail": ""}
    hp = html_path_for_slug(slug)
    if not os.path.exists(hp):
        res["status"] = "NOT_FOUND"
        res["detail"] = f"no source HTML at {os.path.relpath(hp, REPO)}"
        return res
    try:
        _title, text = extract(hp)
    except Exception as e:
        res["status"] = "FAILED"
        res["detail"] = f"extract error: {e}"
        return res

    wc = len(text.split())
    if wc < MIN_WORDS:
        res["status"] = "SKIPPED_STUB"
        res["detail"] = f"only {wc} words (<{MIN_WORDS})"
        return res

    new_hash = text_hash(EXTRACTOR + "\n" + text)
    prev = state["articles"].get(slug)
    is_new = prev is None
    changed = is_new or prev.get("sha256") != new_hash or prev.get("extractor") != EXTRACTOR

    if not changed:
        write_verbal_cache(slug, text)
        res["status"] = "UP_TO_DATE"
        res["detail"] = f"{wc}w, {EXTRACTOR}, hash {new_hash[:12]}"
        return res

    if dry_run:
        res["status"] = "WOULD_ADD" if is_new else "WOULD_UPDATE"
        res["detail"] = (f"{wc}w; {EXTRACTOR}; "
                         + ("new article" if is_new
                            else f"{prev.get('sha256', '')[:12]} -> {new_hash[:12]}"))
        return res

    # regenerate
    try:
        write_verbal_cache(slug, text)
        out_path, nchunks = await synth_article(slug, text)
        size = os.path.getsize(out_path)
    except Exception as e:
        res["status"] = "FAILED"
        res["detail"] = f"tts error: {e}"
        return res

    # upload + verify
    asset = asset_name_for_slug(slug)
    try:
        upload_asset(out_path, asset)
    except subprocess.CalledProcessError as e:
        res["status"] = "FAILED"
        res["detail"] = f"upload error: {e.stderr or e}"
        return res
    if not verify_remote(asset, size):
        res["status"] = "FAILED"
        res["detail"] = "remote size mismatch after upload"
        return res

    # commit to state
    state["articles"][slug] = {
        "html": os.path.relpath(hp, REPO),
        "sha256": new_hash,
        "words": wc,
        "extractor": EXTRACTOR,
    }
    res["status"] = "ADDED" if is_new else "UPDATED"
    res["detail"] = f"{wc}w, {nchunks} chunks, {size//1024} KB -> {asset}"
    return res


async def main_async(args):
    if not args.dry_run and not release_exists():
        print(f"ERROR: release '{RELEASE_TAG}' not found. Create it first.",
              file=sys.stderr)
        return 2

    state = load_state()
    state["release_tag"] = RELEASE_TAG
    state["voice"] = VOICE
    state["extractor"] = EXTRACTOR
    targets = resolve_targets(args, state)
    if not targets:
        print("No target articles given. Pass article names or --all.",
              file=sys.stderr)
        return 2

    results = []
    # Process sequentially per-article (each article already parallelises its
    # own chunks); keeps gh uploads orderly and output readable.
    for slug in targets:
        results.append(await process_one(slug, state, args.dry_run))

    changed_any = any(r["status"] in ("UPDATED", "ADDED") for r in results)
    if changed_any and not args.dry_run:
        write_manifest(state)
        save_state(state)

    # ── report ──
    icon = {
        "UPDATED": "🔄", "ADDED": "➕", "UP_TO_DATE": "✓",
        "WOULD_UPDATE": "🔄?", "WOULD_ADD": "➕?",
        "SKIPPED_STUB": "·", "NOT_FOUND": "✗", "FAILED": "✗",
    }
    print(f"\n{'ARTICLE':52s} RESULT        DETAIL")
    print("─" * 100)
    for r in results:
        print(f"{r['slug'][:52]:52s} "
              f"{icon.get(r['status'],'?')} {r['status']:11s} {r['detail']}")

    n_upd = sum(r["status"] == "UPDATED" for r in results)
    n_add = sum(r["status"] == "ADDED" for r in results)
    n_ok = sum(r["status"] == "UP_TO_DATE" for r in results)
    n_fail = sum(r["status"] in ("FAILED", "NOT_FOUND") for r in results)
    n_would = sum(r["status"] in ("WOULD_UPDATE", "WOULD_ADD") for r in results)

    print("─" * 100)
    if args.dry_run:
        print(f"DRY RUN: {n_would} would change, {n_ok} up-to-date, "
              f"{n_fail} not-found/failed. Nothing uploaded.")
    else:
        print(f"updated: {n_upd}  added: {n_add}  up-to-date: {n_ok}  "
              f"failed: {n_fail}")
        if changed_any:
            print("→ Release audio WAS updated. "
                  "Commit audio-manifest.json + tools/audio/audio_state.json.")
        else:
            print("→ Release audio NOT changed (nothing needed regeneration).")

    return 1 if n_fail else 0


def main():
    ap = argparse.ArgumentParser(
        description="Sync article narration audio to the GitHub Release.")
    ap.add_argument("articles", nargs="*",
                    help="article name(s): slug, html path, url path, or dir")
    ap.add_argument("--all", action="store_true",
                    help="scan every narratable article for drift")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without uploading")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
