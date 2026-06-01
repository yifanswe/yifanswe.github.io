/* Selective syntax highlighting.
 *
 * Only highlight code blocks that explicitly declare a language
 * (```lang  ->  <code class="language-xxx">). The docs on this site are full
 * of ASCII architecture diagrams written in bare ``` blocks; highlight.js
 * auto-detection mis-parses those as code (coloring words like "table",
 * "insert", "into"), which looks broken. Skipping unlabeled blocks leaves
 * diagrams as clean monospace text while real code (python/sql/json/...)
 * still gets themed.
 *
 * Works with both highlight.js v11 (highlightElement) and older
 * v9/v10 builds (highlightBlock).
 */
(function () {
  function run() {
    if (typeof hljs === "undefined") return;
    var highlight = hljs.highlightElement || hljs.highlightBlock;
    if (!highlight) return;
    var blocks = document.querySelectorAll('pre code[class*="language-"]');
    Array.prototype.forEach.call(blocks, function (el) {
      highlight.call(hljs, el);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
