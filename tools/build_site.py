#!/usr/bin/env python3
"""
Static-site generator for yifanswe.github.io.

This repository is a hand-maintained static site: there is no Jekyll/Hugo build
step on GitHub Pages. Every content page is a committed `index.html` that was
produced by converting a Markdown source doc into the site's article template.
This script makes that conversion reproducible.

What it does
------------
For each configured SECTION it:
  1. Reads each Markdown source doc.
  2. Strips the leading `# H1` (the title is rendered by the template instead).
  3. Converts Markdown -> HTML with python-markdown using the
     `extra` + `toc` + `sane_lists` extensions. The `toc` extension assigns the
     heading `id` slugs (e.g. "§1. What X Is" -> "1-what-x-is") that the
     client-side js/toc.js relies on to build the in-page table of contents.
  4. Rewrites sibling `*.md` links so cross-references work on the live site
     (`foo.md` -> `../foo/`, `index.md` -> `../`).
  5. Wraps the body in ARTICLE_TEMPLATE and writes `<section>/<slug>/index.html`.
  6. Writes the section landing page `<section>/index.html` listing every doc.

The output HTML matches the structure of the existing backend-fundamentals/ and
common-backend-systems/ pages, so the whole site can be regenerated the same way.

Usage
-----
    python3 tools/build_site.py

Requires: python-markdown (`pip install markdown`). Verified with markdown 3.9.
"""

import html
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_AUTHOR = "Yifan Li"
DEFAULT_DATE = "2026/05/31"

import markdown

MD_EXTENSIONS = ["extra", "toc", "sane_lists"]


# --- Section definitions ------------------------------------------------------
# Each section maps a directory of Markdown sources to an output folder on the
# site. `docs` is the ordered list of (source filename, page title, listing
# description) tuples. Titles/descriptions come from the section's own index.md.

SECTIONS = [
    {
        "slug": "system-design-patterns",
        "title": "System Design Patterns",
        "nav_label": "System Design Patterns",
        "src_dir": Path(
            "/Users/liali/Documents/personal/next_play/interview/"
            "general system design/system_design_pattern"
        ),
        "intro": (
            "A taxonomy of the <strong>fundamental hardnesses</strong> that recur "
            "across system design interviews. Where the other sections group by "
            "component, this one groups by <em>what is actually hard</em> — the "
            "underlying constraint that breaks naive designs. Most “design X” "
            "prompts are really 2–3 of these patterns stacked together."
        ),
        "docs": [
            ("fan-out-write-amplification.md", "Fan-out / Write Amplification",
             "One write must reach N readers; celebrity creates write storm."),
            ("hot-key-skew.md", "Hot Key / Skew",
             "0.01% of keys take 50% of traffic; one shard melts."),
            ("strong-consistency-under-contention.md", "Strong Consistency Under Contention",
             "N actors race for 1 resource; the loser must see a clear “no”."),
            ("idempotency-exactly-once.md", "Idempotency / Exactly-Once",
             "Network retries cause duplicates; “exactly once” is a lie."),
            ("search-and-ranking.md", "Search and Ranking",
             "Find top K from billions in 100ms; recall + ranking split."),
            ("geo-spatial-queries.md", "Geo / Spatial Queries",
             "“What’s near me” over moving entities; lat/long does not shard."),
            ("real-time-aggregation.md", "Real-Time Aggregation",
             "Continuous compute over a firehose, with late events."),
            ("coordination-exactly-one-execution.md", "Coordination / Exactly-One Execution",
             "Only one node should act, even under partitions."),
            ("ordering-causal-consistency.md", "Ordering / Causal Consistency",
             "Events must respect “happens-before”; total order is expensive."),
            ("durability-at-scale.md", "Durability at Scale",
             "Petabytes that must never disappear; correlated failures."),
            ("backpressure-flow-control.md", "Backpressure / Flow Control",
             "Producer faster than consumer; unbounded queues kill."),
            ("cache-stampede-read-amplification.md", "Cache Stampede / Read Amplification",
             "A cache miss on a hot key wakes a thundering herd."),
        ],
    },
]


# --- Templates ----------------------------------------------------------------

ARTICLE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — {author}</title>
    <link rel="stylesheet" href="/css/style.css">
    <link rel="shortcut icon" type="image/x-icon" href="/images/favicon.ico" />
    <link rel="stylesheet" href="/css/style/github.min.css">
    <link rel="stylesheet" href="https://use.fontawesome.com/releases/v5.6.3/css/all.css" crossorigin="anonymous">
    <script src="/js/jquery.min.js"></script>
    <script src="/js/highlight.min.js"></script>
    <script>hljs.initHighlightingOnLoad();</script>
</head>
<body>
<div class="container">
    <div class="topbar">
        <div class="topbar-brand"><a href="/">{author}</a></div>
        <nav class="topbar-nav">
            <a href="/">Home</a>
            <a href="/backend-fundamentals/">Backend Fundamentals</a>
            <a href="/common-backend-systems/">Common Backend Systems</a>
            <a href="/{section_slug}/">{nav_label}</a>
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

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — {author}</title>
    <link rel="stylesheet" href="/css/style.css">
    <link rel="shortcut icon" type="image/x-icon" href="/images/favicon.ico" />
    <link rel="stylesheet" href="https://use.fontawesome.com/releases/v5.6.3/css/all.css" crossorigin="anonymous">
</head>
<body>
<div class="container">
    <div class="topbar">
        <div class="topbar-brand"><a href="/">{author}</a></div>
        <nav class="topbar-nav">
            <a href="/">Home</a>
            <a href="/backend-fundamentals/">Backend Fundamentals</a>
            <a href="/common-backend-systems/">Common Backend Systems</a>
            <a href="/{section_slug}/">{nav_label}</a>
            <a target="_blank" rel="noopener" href="https://github.com/yifanswe">GitHub</a>
        </nav>
    </div>
    <div class="article">
        <a class="article-back" href="/">← Back to Home</a>
        <h1 class="article-title">{title}</h1>
        <div class="article-meta"><i class="fas fa-edit"></i> {date}</div>
        <div class="article-content">
            <p>{intro}</p>
        </div>
        <div class="posts">
{cards}
        </div>
    </div>
    <div class="footer">
        <a href="#">© 2018–2026 {author}</a>
    </div>
</div>
</body>
</html>
"""

CARD_TEMPLATE = (
    '            <a href="/{section_slug}/{slug}/" class="post-item">'
    '<span class="post-title">{title}<br>'
    '<span style="color:var(--text-muted);font-weight:400;font-size:13px;'
    'font-family:var(--font-sans)">{desc}</span></span>'
    '<span class="post-date">{date}</span></a>'
)


# --- Conversion ---------------------------------------------------------------

H1_RE = re.compile(r"^#\s+.*?$", re.MULTILINE)


def strip_first_h1(text: str) -> str:
    """Remove the leading `# Title` line; the template renders the title."""
    m = H1_RE.search(text)
    if m and text[: m.start()].strip() == "":
        return text[: m.start()] + text[m.end():].lstrip("\n")
    return text


def rewrite_sibling_links(html_text: str, slugs: set[str]) -> str:
    """Make in-section cross-references resolve on the live site.

    `foo.md` -> `../foo/`, `foo.md#anchor` -> `../foo/#anchor`, `index.md` -> `../`.
    Links pointing outside the section (e.g. ../questions/...) are left untouched.
    """
    def repl(match: re.Match) -> str:
        target, anchor = match.group("file"), match.group("anchor") or ""
        if target == "index":
            return f'href="../{anchor}"'
        if target in slugs:
            return f'href="../{target}/{anchor}"'
        return match.group(0)

    pattern = re.compile(r'href="(?P<file>[A-Za-z0-9_-]+)\.md(?P<anchor>#[^"]*)?"')
    return pattern.sub(repl, html_text)


def convert_doc(md_text: str, slugs: set[str]) -> str:
    md = markdown.Markdown(extensions=MD_EXTENSIONS)
    body = md.convert(strip_first_h1(md_text))
    return rewrite_sibling_links(body, slugs)


def build_section(section: dict) -> None:
    slug = section["slug"]
    out_root = REPO / slug
    src_dir = section["src_dir"]
    known_slugs = {Path(f).stem for f, _, _ in section["docs"]}

    cards = []
    for filename, title, desc in section["docs"]:
        doc_slug = Path(filename).stem
        md_text = (src_dir / filename).read_text(encoding="utf-8")
        body = convert_doc(md_text, known_slugs)

        page = ARTICLE_TEMPLATE.format(
            title=html.escape(title),
            author=SITE_AUTHOR,
            section_slug=slug,
            section_title=html.escape(section["title"]),
            nav_label=html.escape(section["nav_label"]),
            date=DEFAULT_DATE,
            body=body,
        )
        out_dir = out_root / doc_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(page, encoding="utf-8")
        print(f"  wrote {slug}/{doc_slug}/index.html")

        cards.append(
            CARD_TEMPLATE.format(
                section_slug=slug,
                slug=doc_slug,
                title=html.escape(title),
                desc=html.escape(desc),
                date=DEFAULT_DATE,
            )
        )

    index_page = INDEX_TEMPLATE.format(
        title=html.escape(section["title"]),
        author=SITE_AUTHOR,
        section_slug=slug,
        nav_label=html.escape(section["nav_label"]),
        date=DEFAULT_DATE,
        intro=section["intro"],
        cards="\n".join(cards),
    )
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "index.html").write_text(index_page, encoding="utf-8")
    print(f"  wrote {slug}/index.html")


def main() -> None:
    for section in SECTIONS:
        print(f"Building section: {section['slug']}")
        build_section(section)


if __name__ == "__main__":
    main()
