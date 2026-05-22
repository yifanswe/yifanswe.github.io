(function () {
  var content = document.querySelector('.article-content');
  var navTargets = [
    document.getElementById('toc-nav'),
    document.getElementById('toc-nav-inline'),
  ].filter(Boolean);
  if (!content || navTargets.length === 0) return;

  var headings = content.querySelectorAll('h1, h2, h3');
  if (headings.length < 2) {
    document.querySelectorAll('.toc').forEach(function (el) {
      el.style.display = 'none';
    });
    return;
  }

  function slugify(text) {
    return text
      .toLowerCase()
      .trim()
      .replace(/[^\w\s-]/g, '')
      .replace(/[\s_-]+/g, '-')
      .replace(/^-+|-+$/g, '');
  }

  var used = {};
  var items = [];
  headings.forEach(function (h) {
    var base = h.id || slugify(h.textContent) || 'section';
    var id = base;
    var i = 2;
    while (used[id]) {
      id = base + '-' + i;
      i++;
    }
    used[id] = true;
    h.id = id;
    items.push({ id: id, text: h.textContent, level: h.tagName.toLowerCase() });
  });

  function buildList() {
    var ul = document.createElement('ul');
    ul.className = 'toc-list';
    items.forEach(function (item) {
      var li = document.createElement('li');
      li.className = 'toc-' + item.level;
      var a = document.createElement('a');
      a.href = '#' + item.id;
      a.textContent = item.text;
      a.dataset.target = item.id;
      li.appendChild(a);
      ul.appendChild(li);
    });
    return ul;
  }

  navTargets.forEach(function (nav) {
    nav.appendChild(buildList());
  });

  var allLinks = document.querySelectorAll('.toc-list a');
  var linksById = {};
  allLinks.forEach(function (a) {
    var id = a.dataset.target;
    if (!linksById[id]) linksById[id] = [];
    linksById[id].push(a);
  });

  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var matches = linksById[entry.target.id];
        if (!matches) return;
        if (entry.isIntersecting) {
          allLinks.forEach(function (l) { l.classList.remove('active'); });
          matches.forEach(function (l) { l.classList.add('active'); });
        }
      });
    }, { rootMargin: '-15% 0px -75% 0px', threshold: 0 });
    headings.forEach(function (h) { observer.observe(h); });
  }
})();
