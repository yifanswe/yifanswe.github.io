#!/usr/bin/env python3
"""
Bilingual (English / Simplified-Chinese) build pipeline for the static blog.

The site stays English-first. A parallel Chinese mirror is generated under /zh/.
A per-tab language toggle (sessionStorage, default English) lets a reader switch;
each page redirects itself to its zh/en counterpart on load if a choice is stored.

Pipeline (run from repo root):

  1. python3 tools/i18n.py extract
       Reads every English content page (those with a <nav class="topbar-nav">),
       collects translatable strings, DEDUPES them globally (nav labels, "Contents",
       "← Back…", etc. repeat across all 137 pages), and writes the unique set in
       fixed-size chunks for translation:
         /tmp/i18n/manifest.json        -> [{ "id", "file", "count" }, ...]
         /tmp/i18n/uniques.json         -> [ unique English string, ... ]
         /tmp/i18n/src/<k>.json         -> { "id": k, "strings": [ up to CHUNK ] }

  2. (translation step, external)
       Each src/<k>.json is translated into /tmp/i18n/out/<k>.json: a JSON array of
       the same length / order, each entry the Simplified-Chinese translation.

  3. python3 tools/i18n.py stitch
       Concatenates out/<k>.json in id order, checks the length equals uniques,
       and writes the en->zh dictionary to /tmp/i18n/map.json.

  4. python3 tools/i18n.py build
       - Injects the toggle + redirect head-script into every English page in place.
       - Builds /zh/<path> for every page from the English DOM + the map.json lookup.
       - Appends the .lang-toggle CSS rule to css/style.css (once).

Determinism: extract and build call the SAME collect_items() over the SAME English
files, so the zh strings array aligns index-for-index with the DOM nodes. All HTML
surgery happens here in Python; the translator only ever sees/returns plain strings,
so code blocks, structure, links and anchors can never be corrupted by translation.
"""

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from bs4.element import Comment, Doctype, CData, ProcessingInstruction

REPO = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/i18n")
SRC_DIR = WORK / "src"   # English chunks to translate
OUT_DIR = WORK / "out"   # translated chunks (filled by the workflow)

CHUNK = 200  # unique strings per translation chunk

# Elements whose text is code / markup, never translated.
SKIP_PARENTS = {"script", "style", "code", "pre", "textarea"}

# Absolute href prefixes that are static assets, not navigable pages.
ASSET_PREFIXES = ("/css/", "/js/", "/images/", "/fonts/")

# Injected once into <head> of every page: per-tab redirect + the switchLang() helper.
HEAD_SCRIPT = (
    "\n(function(){"
    "var K='siteLang',p=location.pathname,"
    "z=(p==='/zh/'||p.lastIndexOf('/zh/',0)===0),"
    "s=null;try{s=sessionStorage.getItem(K);}catch(e){}"
    "if(s==='zh'&&!z){location.replace('/zh'+(p==='/'?'/':p)+location.hash);return;}"
    "if(s==='en'&&z){location.replace((p.replace(/^\\/zh/,'')||'/')+location.hash);return;}"
    "if(s===null&&z){try{sessionStorage.setItem(K,'zh');}catch(e){}}"
    "})();"
    "function switchLang(t){"
    "try{sessionStorage.setItem('siteLang',t);}catch(e){}"
    "var p=location.pathname,z=(p==='/zh/'||p.lastIndexOf('/zh/',0)===0);"
    "if(t==='zh'&&!z)location.href='/zh'+(p==='/'?'/':p);"
    "else if(t==='en'&&z)location.href=(p.replace(/^\\/zh/,'')||'/');"
    "else location.reload();"
    "}\n"
)

# Toggle anchors (string-injected into EN, bs4-injected into ZH).
EN_TOGGLE = '<a href="javascript:void(0)" class="lang-toggle" onclick="switchLang(\'zh\')">中文</a>'
ZH_TOGGLE_TEXT = "EN"

CSS_RULE = """
/* Language toggle (EN / 中文) in the top nav */
.topbar-nav .lang-toggle {
  text-transform: none;
  letter-spacing: 0;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 11px;
  color: var(--text-muted);
  cursor: pointer;
}
.topbar-nav .lang-toggle:hover {
  border-color: var(--text);
  color: var(--text);
}
"""

SKIP_TYPES = (Comment, Doctype, CData, ProcessingInstruction)


def content_pages():
    """All English index.html files that are real content (have the top nav)."""
    out = []
    for path in sorted(REPO.rglob("index.html")):
        rel = path.relative_to(REPO)
        if rel.parts and rel.parts[0] in (".git", "zh", "tools"):
            continue
        html = path.read_text(encoding="utf-8")
        if 'class="topbar-nav"' not in html:
            continue  # redirect stubs (2020/, archives/) have no nav -> skip
        out.append(rel)
    return out


def has_alpha(s):
    return any(c.isalpha() for c in s)


def collect_items(soup):
    """
    Ordered list of translatable units in a page. Deterministic across runs.
    Each item is ('text', NavigableString) or ('attr', tag, attr_name).
    Text nodes first (document order), then input placeholders (document order).
    """
    items = []
    for node in soup.find_all(string=True):
        if isinstance(node, SKIP_TYPES):
            continue
        if any(p.name in SKIP_PARENTS for p in node.parents):
            continue
        if not has_alpha(node.strip()):
            continue
        items.append(("text", node))
    for inp in soup.find_all("input"):
        ph = inp.get("placeholder")
        if ph and has_alpha(ph):
            items.append(("attr", inp, "placeholder"))
    return items


def item_text(item):
    if item[0] == "text":
        return item[1].strip()
    return item[1].get(item[2])


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def cmd_extract():
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    uniques, seen = [], set()
    total = 0
    for idx, rel in enumerate(content_pages()):
        soup = BeautifulSoup((REPO / rel).read_text(encoding="utf-8"), "html.parser")
        strings = [item_text(it) for it in collect_items(soup)]
        manifest.append({"id": idx, "file": str(rel), "count": len(strings)})
        total += len(strings)
        for s in strings:
            if s not in seen:
                seen.add(s)
                uniques.append(s)
    (WORK / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (WORK / "uniques.json").write_text(
        json.dumps(uniques, ensure_ascii=False), encoding="utf-8"
    )
    for old in SRC_DIR.glob("*.json"):
        old.unlink()
    nchunks = 0
    for k, start in enumerate(range(0, len(uniques), CHUNK)):
        (SRC_DIR / f"{k}.json").write_text(
            json.dumps({"id": k, "strings": uniques[start:start + CHUNK]},
                       ensure_ascii=False), encoding="utf-8")
        nchunks = k + 1
    print(f"extracted {len(manifest)} pages, {total} strings, "
          f"{len(uniques)} unique -> {nchunks} chunks in {SRC_DIR}")


# --------------------------------------------------------------------------- #
# stitch
# --------------------------------------------------------------------------- #
def _read_out_lines(k):
    """Return the translated lines for chunk k, or None if absent/unreadable."""
    out = OUT_DIR / f"{k}.txt"
    if not out.exists():
        return None
    text = out.read_text(encoding="utf-8")
    # Split on newlines; drop a single trailing newline's empty tail only.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def chunk_status():
    """Per-chunk validity. Returns (valid_ids, bad_ids, missing_ids, src_map)."""
    src_map = {}
    for src in SRC_DIR.glob("*.json"):
        k = int(src.stem)
        src_map[k] = json.loads(src.read_text(encoding="utf-8"))["strings"]
    valid, bad, missing = [], [], []
    for k, strings in sorted(src_map.items()):
        lines = _read_out_lines(k)
        if lines is None:
            missing.append(k)
        elif len(lines) != len(strings):
            bad.append(k)
        else:
            valid.append(k)
    return valid, bad, missing, src_map


def cmd_status():
    valid, bad, missing, src_map = chunk_status()
    total_u = sum(len(v) for v in src_map.values())
    covered = sum(len(src_map[k]) for k in valid)
    print(f"chunks: {len(src_map)} | valid {len(valid)} | "
          f"bad {len(bad)} | missing {len(missing)}")
    print(f"unique strings covered: {covered}/{total_u}")
    todo = sorted(bad + missing)
    if todo:
        print("REDO:", ",".join(map(str, todo)))


def cmd_stitch():
    valid, bad, missing, src_map = chunk_status()
    mapping = {}
    for k in valid:
        for en, zh in zip(src_map[k], _read_out_lines(k)):
            if zh.strip():
                mapping[en] = zh
    (WORK / "map.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    total_u = sum(len(v) for v in src_map.values())
    print(f"stitched map: {len(mapping)} strings from {len(valid)} valid chunks; "
          f"{len(bad)} bad, {len(missing)} missing (those fall back to English)")
    if bad or missing:
        print("  redo chunks:", ",".join(map(str, sorted(bad + missing))))


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def inject_en_inplace(html):
    """Add head script + nav toggle to an English page via minimal string edits."""
    if "siteLang" not in html:
        html = html.replace("<head>", "<head>\n<script>" + HEAD_SCRIPT + "</script>", 1)
    if "lang-toggle" not in html:
        html = html.replace("</nav>", "    " + EN_TOGGLE + "\n            </nav>", 1)
    return html


def rewrite_zh_links(soup):
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href == "/":
            a["href"] = "/zh/"
        elif href.startswith("/") and not href.startswith("/zh/") \
                and not href.startswith(ASSET_PREFIXES):
            a["href"] = "/zh" + href


def inject_zh_dom(soup):
    """Add head script + nav toggle (showing 'EN') to a generated zh page.

    Strips any toggle / head-script first, so the result is correct even when the
    source English file already had the EN toggle injected (idempotent re-runs).
    """
    # Remove any previously-injected redirect script and language toggle.
    for sc in soup.find_all("script"):
        if sc.string and "siteLang" in sc.string:
            sc.decompose()
    for tg in soup.find_all("a", class_="lang-toggle"):
        tg.decompose()
    head = soup.head
    if head:
        tag = soup.new_tag("script")
        tag.string = HEAD_SCRIPT
        head.insert(0, tag)
    nav = soup.find("nav", class_="topbar-nav")
    if nav:
        a = soup.new_tag("a", href="javascript:void(0)",
                         attrs={"class": "lang-toggle", "onclick": "switchLang('en')"})
        a.string = ZH_TOGGLE_TEXT
        nav.append(a)


def patch_zh_homepage_js(html):
    """The homepage filter JS rebuilds the post-count label; localize its literals."""
    html = html.replace("' posts'", "' 篇'").replace("' post'", "' 篇'")
    html = html.replace("'No posts match.'", "'没有匹配的文章。'")
    return html


def build_zh_page(rel, mapping):
    soup = BeautifulSoup((REPO / rel).read_text(encoding="utf-8"), "html.parser")
    # Capture the English page heading + title before mutating, so we can keep the
    # <title> consistent with the (often better-translated) <h1>.
    h1el = soup.find(class_="article-title")
    title_el = soup.find("title")
    en_core = h1el.get_text() if h1el else None
    en_title = title_el.string if (title_el and title_el.string) else None
    items = collect_items(soup)
    for item in items:
        zh = mapping.get(item_text(item))
        if not zh:
            continue  # untranslated -> leave English (safe fallback)
        if item[0] == "text":
            node = item[1]
            orig = str(node)
            lead = orig[: len(orig) - len(orig.lstrip())]
            trail = orig[len(orig.rstrip()):]
            node.replace_with(NavigableString(lead + zh + trail))
        else:
            item[1][item[2]] = zh
    # Keep <title> consistent with the translated <h1>: titles follow the pattern
    # "<core> — Yifan Li"; rebuild the core from the heading's translation so a
    # title can't stay English while its heading is Chinese.
    if title_el and en_core and en_title and en_title.strip() == f"{en_core} — Yifan Li":
        zh_core = mapping.get(en_core)
        if zh_core:
            title_el.string = f"{zh_core} — Yifan Li"
    if soup.html:
        soup.html["lang"] = "zh-CN"
    rewrite_zh_links(soup)
    inject_zh_dom(soup)
    out = str(soup)
    if rel == Path("index.html"):
        out = patch_zh_homepage_js(out)
    dest = REPO / "zh" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")


def append_css_rule():
    css = REPO / "css" / "style.css"
    text = css.read_text(encoding="utf-8")
    if ".lang-toggle" not in text:
        css.write_text(text.rstrip() + "\n" + CSS_RULE, encoding="utf-8")
        print("appended .lang-toggle rule to css/style.css")


def cmd_build():
    mapping = json.loads((WORK / "map.json").read_text(encoding="utf-8"))
    built = 0
    for rel in content_pages():
        # 1. Chinese mirror (built first, from the pristine English source).
        build_zh_page(rel, mapping)
        # 2. English page: inject toggle in place.
        p = REPO / rel
        p.write_text(inject_en_inplace(p.read_text(encoding="utf-8")), encoding="utf-8")
        built += 1
    append_css_rule()
    print(f"built {built} zh pages using {len(mapping)} translated strings")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "extract":
        cmd_extract()
    elif cmd == "status":
        cmd_status()
    elif cmd == "stitch":
        cmd_stitch()
    elif cmd == "build":
        cmd_build()
    else:
        print(__doc__)
        sys.exit(1)
