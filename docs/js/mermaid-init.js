// Render Mermaid diagrams from `pre.mermaid-src` blocks.
//
// Why this exists: mkdocs-material 9.7 hijacks the `mermaid` class — it moves the
// fence source out and leaves an empty `div.mermaid` — but ships no mermaid asset
// to render with, so diagrams silently vanish. So the fences are emitted as
// `mermaid-src` (see mkdocs.yml) and rendered here against a vendored mermaid.
//
// Mermaid bakes the theme in at render time, so a palette switch needs a re-render,
// not a CSS swap.
(function () {
  var isDark = function () {
    return document.body.getAttribute("data-md-color-scheme") === "slate";
  };

  var render = function () {
    if (!window.mermaid) return;

    var blocks = document.querySelectorAll("pre.mermaid-src");
    if (!blocks.length) return;

    window.mermaid.initialize({
      startOnLoad: false,
      theme: isDark() ? "dark" : "default",
      securityLevel: "strict",
      flowchart: { useMaxWidth: true },
      sequence: { useMaxWidth: true },
      er: { useMaxWidth: true },
    });

    blocks.forEach(function (pre, i) {
      // keep the raw source: after the first render the element holds an <svg>
      if (!pre.dataset.src) pre.dataset.src = pre.textContent;

      var id = "mmd-" + i + "-" + (isDark() ? "d" : "l");
      window.mermaid
        .render(id, pre.dataset.src)
        .then(function (out) {
          pre.innerHTML = out.svg;
        })
        .catch(function (err) {
          // leave the source visible rather than showing an empty gap
          pre.textContent = pre.dataset.src;
          console.error("mermaid failed to render a diagram:", err);
        });
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }

  // Material's instant navigation swaps the article without a page load
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(render);
  }

  // palette toggle -> re-render with the other theme
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      if (m.attributeName === "data-md-color-scheme") render();
    });
  }).observe(document.body, { attributes: true });
})();
