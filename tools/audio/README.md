# Audio narration sync

Article narration MP3s are **not** stored in this repo — they live as assets on
the GitHub Release tagged **`audio-v1`** (≈1.9 GB, 134 files). Keeping them out
of git keeps the Pages site small and ToS-clean. The site references them via:

- **`js/audio-player.js`** — builds each page's release-asset URL and gates the
  player UI on a same-origin manifest.
- **`audio-manifest.json`** (repo root) — JSON array of slugs that have audio.

## When you edit an article

Run the sync tool with the article name(s). It detects whether the hidden
**verbal narration script** changed (so CSS/nav/script edits don't trigger
pointless regeneration), regenerates only what changed, re-uploads to the
release, and tells you whether the release audio was updated.

```bash
PY=~/.hermes/hermes-agent/venv/bin/python   # venv with edge_tts, bs4, lxml

# one article (any of these forms work)
$PY tools/audio/sync_audio.py backend-fundamentals/cache
$PY tools/audio/sync_audio.py /backend-fundamentals/cache/
$PY tools/audio/sync_audio.py backend-fundamentals__cache

# several at once
$PY tools/audio/sync_audio.py backend-fundamentals/cache common-backend-systems/uber

# scan everything for drift
$PY tools/audio/sync_audio.py --all

# preview without uploading
$PY tools/audio/sync_audio.py --all --dry-run
```

### Result codes
| Symbol | Status | Meaning |
|---|---|---|
| 🔄 | `UPDATED` | text changed → audio regenerated + re-uploaded |
| ➕ | `ADDED` | new article → audio created + uploaded |
| ✓ | `UP_TO_DATE` | speakable text unchanged → nothing done |
| · | `SKIPPED_STUB` | under 80 words → not narrated |
| ✗ | `NOT_FOUND` / `FAILED` | source HTML missing, or TTS/upload failed |

Exit code is non-zero if any article failed, so it's CI/cron-friendly.

## After a sync that changed something

The tool updates two tracked files — **commit them**:

```bash
git add audio-manifest.json tools/audio/audio_state.json
git commit -m "Update narration for <article>"
git push
```

(The release assets are already updated by then; the commit just records the
new manifest + content-hash baseline so the next run knows the current state.)

## How change-detection works

`audio_state.json` stores a `sha256` of each article's verbal narration script
(the exact string `extract_verbal.py` feeds to TTS, salted with the extractor
version). On each run the tool recomputes that hash from the current HTML and
compares. Hash differs → regenerate. The script is also written to the ignored
`tools/audio/verbal_cache/` directory for inspection, but it is not linked from
or displayed on the public blog. If you change extraction rules, run `--all`
once to rebaseline.

## Requirements
- `gh` authenticated to the repo (`gh auth status`)
- Python venv with `edge_tts`, `beautifulsoup4`, `lxml`
- `ffmpeg` on PATH (concatenates multi-chunk articles)
