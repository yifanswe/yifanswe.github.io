#!/usr/bin/env python3
"""
Bulk-import system-design interview docs into the Common Backend Systems section.

Unlike tools/build_site.py (which takes an explicit, curated list of docs per
section), this builder handles a *large, auto-discovered* corpus: it scans two
source directories, derives each page's title from its first Markdown `# H1`,
and merges everything into one flat list under /common-backend-systems/.

What it does
------------
1. Scans the two source dirs and builds a (slug, title, source, md_path) record
   for every .md file (skipping dotfiles / working files).
       - questions/            -> detailed write-ups, titles kept as-is
       - hello_interview_prep/ -> condensed walkthroughs, " (walkthrough)" tag
   Slugs are the filename stem with underscores normalized to hyphens.
2. Converts each doc to HTML (extra + toc + sane_lists, same as build_site.py),
   stripping the leading H1, and rewrites in-corpus `*.md` cross-links so they
   resolve to the published sibling pages.
3. Writes /common-backend-systems/<slug>/index.html using the site's article
   template (full nav, dark code theme, selective highlighter).
4. Injects title-only cards into the existing /common-backend-systems/index.html
   and into the homepage's "Common Backend Systems" collapsible group, between
   <!-- QUESTIONS:START --> / <!-- QUESTIONS:END --> markers so re-runs are
   idempotent. It also updates that group's post count and description.

The 5 hand-written component pages already in the section are preserved.

Usage:  python3 tools/build_questions.py
Requires: python-markdown (pip install markdown).
"""

import html
import re
from pathlib import Path

import markdown

REPO = Path(__file__).resolve().parent.parent
SD = Path(
    "/Users/liali/Documents/personal/next_play/interview/general system design"
)
SECTION_SLUG = "common-backend-systems"
SECTION_TITLE = "Common Backend Systems"
AUTHOR = "Yifan Li"
DATE = "2026/06/01"
MD_EXTENSIONS = ["extra", "toc", "sane_lists"]

SOURCES = [
    {"dir": SD / "questions", "walkthrough": False},
    {"dir": SD / "hello_interview_prep", "walkthrough": True},
]

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — {author}</title>
    <link rel="stylesheet" href="/css/style.css">
    <link rel="shortcut icon" type="image/x-icon" href="/images/favicon.ico" />
    <link rel="stylesheet" href="/css/style/monokai-sublime.min.css">
    <link rel="stylesheet" href="https://use.fontawesome.com/releases/v5.6.3/css/all.css" crossorigin="anonymous">
    <script src="/js/jquery.min.js"></script>
    <script src="/js/highlight.min.js"></script>
    <script src="/js/highlight-init.js"></script>
</head>
<body>
<div class="container">
    <div class="topbar">
        <div class="topbar-brand"><a href="/">{author}</a></div>
        <nav class="topbar-nav">
            <a href="/">Home</a>
            <a href="/backend-fundamentals/">Backend Fundamentals</a>
            <a href="/common-backend-systems/">Common Backend Systems</a>
            <a href="/system-design-patterns/">System Design Patterns</a>
            <a target="_blank" rel="noopener" href="https://github.com/yifanswe">GitHub</a>
        </nav>
    </div>
    <div class="article">
        <a class="article-back" href="/{section_slug}/">← Back to {section_title}</a>
        <h1 class="article-title">{title}</h1>
        <div class="article-meta">
            <i class="fas fa-edit"></i> {date} &nbsp;·&nbsp; {section_title}
        </div>
        <details class="toc-inline toc" open>
            <summary>Contents</summary>
            <nav id="toc-nav-inline"></nav>
        </details>
        <aside class="toc-sticky toc">
            <div class="toc-title">Contents</div>
            <nav id="toc-nav"></nav>
        </aside>
        <div class="article-content">
{body}
        </div>
    </div>
    <div class="footer">
        <a href="#">© 2018–2026 {author}</a>
    </div>
</div>
<button class="totop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑ Top</button>
<script src="/js/totop.js"></script>
<script src="/js/toc.js"></script>
</body>
</html>
"""

H1_RE = re.compile(r"^#\s+(.*?)\s*$", re.MULTILINE)


def discover():
    """Return list of records: {slug, title, walkthrough, path, stem}."""
    records = []
    used_slugs = {}
    for src in SOURCES:
        for path in sorted(src["dir"].glob("*.md")):
            if path.name.startswith("."):
                continue
            text = path.read_text(encoding="utf-8")
            m = H1_RE.search(text)
            title = m.group(1).strip() if m else path.stem.replace("_", " ").title()
            if src["walkthrough"]:
                title = f"{title} (walkthrough)"
            slug = path.stem.replace("_", "-").lower()
            if slug in used_slugs:  # collision guard (none expected)
                slug = f"{slug}-{2 if used_slugs[slug] == 1 else used_slugs[slug] + 1}"
            used_slugs[slug] = used_slugs.get(slug, 0) + 1
            records.append({
                "slug": slug,
                "title": title,
                "walkthrough": src["walkthrough"],
                "path": path,
                "stem": path.stem,
            })
    return records


def topic_sort_key(rec):
    t = rec["title"].lower().replace("(walkthrough)", "")
    for p in ("design the ", "design a ", "design an ", "design "):
        if t.startswith(p):
            t = t[len(p):]
            break
    t = re.split(r"[—\-–:]| like | as ", t)[0]
    t = re.sub(r"[^a-z0-9]", "", t)
    return (t, rec["walkthrough"], rec["slug"])


def strip_first_h1(text):
    m = H1_RE.search(text)
    if m and text[: m.start()].strip() == "":
        return text[: m.start()] + text[m.end():].lstrip("\n")
    return text


def make_link_rewriter(stem_to_slug):
    """Rewrite in-corpus links (bare or ../questions/.. or ../hello_..) to /slug/."""
    pattern = re.compile(
        r'href="(?:\.\./(?:questions|hello_interview_prep)/)?'
        r'(?P<stem>[A-Za-z0-9_-]+)\.md(?P<anchor>#[^"]*)?"'
    )

    def repl(match):
        slug = stem_to_slug.get(match.group("stem"))
        if not slug:
            return match.group(0)
        return f'href="../{slug}/{match.group("anchor") or ""}"'

    return lambda h: pattern.sub(repl, h)


def build_pages(records):
    stem_to_slug = {r["stem"]: r["slug"] for r in records}
    rewrite = make_link_rewriter(stem_to_slug)
    for r in records:
        md_text = strip_first_h1(r["path"].read_text(encoding="utf-8"))
        body = markdown.Markdown(extensions=MD_EXTENSIONS).convert(md_text)
        body = rewrite(body)
        page = PAGE_TEMPLATE.format(
            title=html.escape(r["title"]),
            author=AUTHOR,
            section_slug=SECTION_SLUG,
            section_title=SECTION_TITLE,
            date=DATE,
            body=body,
        )
        out = REPO / SECTION_SLUG / r["slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    print(f"  wrote {len(records)} pages under {SECTION_SLUG}/")


def update_section_index(records):
    path = REPO / SECTION_SLUG / "index.html"
    text = path.read_text(encoding="utf-8")
    cards = "\n".join(
        f'            <a href="/{SECTION_SLUG}/{r["slug"]}/" class="post-item">'
        f'<span class="post-title">{html.escape(r["title"])}</span>'
        f'<span class="post-date">{DATE}</span></a>'
        for r in records
    )
    block = f"            <!-- QUESTIONS:START -->\n{cards}\n            <!-- QUESTIONS:END -->"
    # strip any previous injection, then insert before the closing </div> of .posts
    text = re.sub(r"\n?\s*<!-- QUESTIONS:START -->.*?<!-- QUESTIONS:END -->", "", text, flags=re.S)
    text = re.sub(
        r'(<div class="posts">.*?)(\n\s*</div>)',
        lambda m: m.group(1) + "\n" + block + m.group(2),
        text,
        count=1,
        flags=re.S,
    )
    # refresh the section intro to mention the questions
    text = re.sub(
        r"(<div class=\"article-content\">\s*<p>).*?(</p>)",
        r"\g<1>Application-layer and operational systems built on the fundamentals, "
        r"plus a large set of worked system-design interview questions — full "
        r"“design X” write-ups and condensed walkthroughs.\g<2>",
        text,
        count=1,
        flags=re.S,
    )
    path.write_text(text, encoding="utf-8")
    print(f"  updated {SECTION_SLUG}/index.html (+{len(records)} cards)")


def update_homepage(records):
    path = REPO / "index.html"
    text = path.read_text(encoding="utf-8")
    items = "\n".join(
        f'                    <a href="/{SECTION_SLUG}/{r["slug"]}/" class="post-item" '
        f'data-year="2026" data-title="{html.escape(keywords(r))}">\n'
        f'                        <span class="post-title">{html.escape(r["title"])}</span>\n'
        f'                        <span class="post-date">{DATE}</span>\n'
        f"                    </a>"
        for r in records
    )
    block = f"                    <!-- QUESTIONS:START -->\n{items}\n                    <!-- QUESTIONS:END -->"

    # isolate the Common Backend Systems collapsible group. The tempered
    # `(?:(?!</details>)[\s\S])*?` token never crosses a </details>, so the
    # match is exactly the one <details> block whose title is this section.
    grp_re = re.compile(
        r'<details class="post-group"[^>]*>'
        r'(?:(?!</details>)[\s\S])*?Common Backend Systems'
        r'(?:(?!</details>)[\s\S])*?</details>'
    )
    m = grp_re.search(text)
    if not m:
        raise SystemExit("Could not locate Common Backend Systems group on homepage")
    group = m.group(0)

    total = 5 + len(records)
    group = re.sub(
        r'data-count="\d+">\d+ posts',
        f'data-count="{total}">{total} posts',
        group,
        count=1,
    )
    group = re.sub(
        r'(<span class="post-group-desc">).*?(</span>)',
        r"\g<1>Operational systems built on the fundamentals, plus worked "
        r"system-design interview questions.\g<2>",
        group,
        count=1,
        flags=re.S,
    )
    group = re.sub(r"\n?\s*<!-- QUESTIONS:START -->.*?<!-- QUESTIONS:END -->", "", group, flags=re.S)
    group = re.sub(
        r'(<div class="posts">.*?)(\n\s*</div>)',
        lambda mm: mm.group(1) + "\n" + block + mm.group(2),
        group,
        count=1,
        flags=re.S,
    )
    text = text[: m.start()] + group + text[m.end():]
    path.write_text(text, encoding="utf-8")
    print(f"  updated homepage Common Backend Systems group (count -> {total})")


def keywords(rec):
    base = re.sub(r"[^a-zA-Z0-9 ]", " ", rec["title"])
    base = re.sub(r"\s+", " ", base).strip()
    return base + (" walkthrough" if rec["walkthrough"] else "")


def main():
    records = discover()
    records.sort(key=topic_sort_key)
    print(f"Discovered {len(records)} interview-question docs")
    build_pages(records)
    update_section_index(records)
    update_homepage(records)


if __name__ == "__main__":
    main()
