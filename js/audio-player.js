/* Article audio player — English TTS narration.
 *
 * Self-contained, no dependencies. On DOMContentLoaded it derives the
 * audio URL from the current page path, checks whether that MP3 exists,
 * and if so injects a play/pause + seek + speed control right under the
 * article title. Pages without a matching audio file (index/listing
 * pages, Chinese /zh/ pages) show nothing — so this script is safe to
 * include site-wide.
 */
(function () {
  "use strict";

  // Narration MP3s are hosted as GitHub Release assets (NOT in this repo, to
  // stay under the GitHub Pages size limit). Release assets live in a flat
  // namespace, so the page path "/foo/bar/" maps to the asset "foo__bar.mp3".
  // We can't probe their existence with fetch() (release-assets.github
  // usercontent.com sends no CORS header, so a cross-origin fetch throws);
  // <audio> playback itself needs no CORS. So existence is checked against a
  // same-origin manifest (/audio-manifest.json) instead.
  var RELEASE_BASE =
    "https://github.com/yifanswe/yifanswe.github.io/releases/download/audio-v1/";
  var MANIFEST_URL = "/audio-manifest.json";

  // iOS WebKit (every iOS browser) refuses to play the GitHub Release assets:
  // they're served cross-origin as application/octet-stream with
  // Content-Disposition: attachment, which WebKit's media loader rejects.
  // Desktop browsers sniff the bytes and play anyway, but mobile does not.
  // As a fix, selected series are committed into the repo and served
  // SAME-ORIGIN from GitHub Pages (correct audio/mpeg, no attachment header),
  // which plays everywhere. A slug is same-origin iff it starts with one of
  // these prefixes; everything else still streams from the release.
  //   same-origin slug "foo__bar"  ->  "/audio/foo/bar/index.mp3"
  var SAMEORIGIN_PREFIXES = ["backend-fundamentals__"];

  function isSameOrigin(slug) {
    for (var i = 0; i < SAMEORIGIN_PREFIXES.length; i++) {
      if (slug.indexOf(SAMEORIGIN_PREFIXES[i]) === 0) return true;
    }
    return false;
  }

  // Build the playable URL for a slug. Same-origin slugs map back to their
  // directory path under /audio/ (the "__" separators become "/"); the rest
  // resolve to their flat release asset.
  function audioUrlForSlug(slug) {
    if (isSameOrigin(slug)) {
      return "/audio/" + slug.replace(/__/g, "/") + "/index.mp3";
    }
    return RELEASE_BASE + encodeURIComponent(slug) + ".mp3";
  }

  // Derive the flat slug for the current page. MUST match the slug the upload
  // tooling computed from the audio file path, or audio silently won't show.
  //   "/foo/bar/"            -> "foo__bar"
  //   "/foo/bar/index.html"  -> "foo__bar"
  //   "/foo.html"            -> "foo"
  function slugForPath() {
    var p = location.pathname;
    // Chinese pages are not narrated.
    if (p === "/zh/" || p.indexOf("/zh/") === 0) return null;
    if (p.charAt(p.length - 1) === "/") p += "index.html";
    if (p.charAt(0) === "/") p = p.slice(1);
    p = p.replace(/\/?index\.html$/, ""); // drop trailing /index.html (dir pages)
    p = p.replace(/\.html$/, ""); // drop .html (flat pages)
    if (p === "") return null; // site root / listing — no narration
    return p.replace(/\//g, "__");
  }

  function fmt(t) {
    if (!isFinite(t) || t < 0) t = 0;
    var m = Math.floor(t / 60);
    var s = Math.floor(t % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  var ICON_PLAY =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
  var ICON_PAUSE =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';

  function build(audioUrl) {
    var titleEl = document.querySelector(".article-title");
    var metaEl = document.querySelector(".article-meta");
    var anchor = metaEl || titleEl;
    if (!anchor) return;

    var wrap = document.createElement("div");
    wrap.className = "audio-player";

    var btn = document.createElement("button");
    btn.className = "audio-player__btn";
    btn.type = "button";
    btn.setAttribute("aria-label", "Play narration");
    btn.innerHTML = ICON_PLAY;

    var main = document.createElement("div");
    main.className = "audio-player__main";

    var label = document.createElement("span");
    label.className = "audio-player__label";
    label.textContent = "Listen to this article";

    var barRow = document.createElement("div");
    barRow.className = "audio-player__bar-row";

    var seek = document.createElement("input");
    seek.type = "range";
    seek.className = "audio-player__seek";
    seek.min = "0";
    seek.max = "100";
    seek.value = "0";
    seek.setAttribute("aria-label", "Seek");

    var time = document.createElement("span");
    time.className = "audio-player__time";
    time.textContent = "0:00 / 0:00";

    var rate = document.createElement("button");
    rate.className = "audio-player__rate";
    rate.type = "button";
    rate.textContent = "1x";

    barRow.appendChild(seek);
    barRow.appendChild(time);
    barRow.appendChild(rate);
    main.appendChild(label);
    main.appendChild(barRow);
    wrap.appendChild(btn);
    wrap.appendChild(main);

    var audio = new Audio();
    audio.preload = "metadata";
    audio.src = audioUrl;

    // Insert after the meta line (or title).
    anchor.parentNode.insertBefore(wrap, anchor.nextSibling);

    var seeking = false;

    btn.addEventListener("click", function () {
      if (audio.paused) audio.play();
      else audio.pause();
    });
    audio.addEventListener("play", function () {
      btn.innerHTML = ICON_PAUSE;
      btn.setAttribute("aria-label", "Pause narration");
    });
    audio.addEventListener("pause", function () {
      btn.innerHTML = ICON_PLAY;
      btn.setAttribute("aria-label", "Play narration");
    });
    audio.addEventListener("loadedmetadata", function () {
      time.textContent = "0:00 / " + fmt(audio.duration);
    });
    audio.addEventListener("timeupdate", function () {
      if (seeking) return;
      var pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      seek.value = String(pct);
      time.textContent = fmt(audio.currentTime) + " / " + fmt(audio.duration);
    });
    audio.addEventListener("ended", function () {
      seek.value = "0";
      audio.currentTime = 0;
    });
    seek.addEventListener("input", function () {
      seeking = true;
      if (audio.duration) {
        time.textContent =
          fmt((seek.value / 100) * audio.duration) + " / " + fmt(audio.duration);
      }
    });
    seek.addEventListener("change", function () {
      if (audio.duration) audio.currentTime = (seek.value / 100) * audio.duration;
      seeking = false;
    });
    var rates = [1, 1.25, 1.5, 1.75, 2, 0.75];
    var ri = 0;
    rate.addEventListener("click", function () {
      ri = (ri + 1) % rates.length;
      audio.playbackRate = rates[ri];
      rate.textContent = rates[ri] + "x";
    });
  }

  function init() {
    var slug = slugForPath();
    if (!slug) return;
    // Existence check against the same-origin manifest (no CORS issue).
    // The manifest is a JSON array of slugs that have a narration asset.
    fetch(MANIFEST_URL)
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (slugs) {
        if (slugs && slugs.indexOf(slug) !== -1) {
          build(audioUrlForSlug(slug));
        }
      })
      .catch(function () {
        /* no manifest or network issue — stay silent */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
