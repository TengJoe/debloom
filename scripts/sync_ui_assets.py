#!/usr/bin/python3
"""Bundle the local translation catalog and the canonical SVG into offline HTML."""

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = r"""<script>
(() => {
  'use strict';
  const catalog = __CATALOG__;
  let language = 'zh';
  let textNodes = [], attributes = [];
  function translate(value) {
    if (language !== 'en') return String(value);
    return String(value).split('\n').map(line => {
      if (Object.prototype.hasOwnProperty.call(catalog.strings, line)) return catalog.strings[line];
      for (const [pattern, replacement] of catalog.patterns) {
        const expression = new RegExp('^(?:' + pattern + ')$');
        if (expression.test(line)) return line.replace(expression, replacement);
      }
      return line;
    }).join('\n');
  }
  window.I18n = {translate, get language() { return language; }};
  function apply(code) {
    if (!['zh', 'en'].includes(code)) return;
    language = code;
    document.documentElement.lang = code === 'zh' ? 'zh-CN' : 'en';
    for (const [node, original] of textNodes) node.textContent = original.replace(original.trim(), translate(original.trim()));
    for (const [el, attr, original] of attributes) el.setAttribute(attr, translate(original));
    const sel = document.getElementById('language-select');
    if (sel) sel.value = code;
    if (window.App) window.App.refresh();
    if (window.Updates) window.Updates.refresh();
  }
  window.Locale = {receive(event) {
    if (!event || event.type !== 'locale') return;
    apply(event.language);
    const feedback = document.getElementById('language-feedback');
    if (feedback) feedback.textContent = event.error ? translate(event.error) : '';
  }};
  document.addEventListener('DOMContentLoaded', () => {
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.parentElement.closest('script,style,pre')) continue;
      if (Object.prototype.hasOwnProperty.call(catalog.strings, node.textContent.trim())) textNodes.push([node, node.textContent]);
    }
    for (const el of document.querySelectorAll('[aria-label],[title]')) {
      for (const attr of ['aria-label','title']) if (el.hasAttribute(attr)) attributes.push([el, attr, el.getAttribute(attr)]);
    }
    document.getElementById('language-select').addEventListener('change', event => {
      const code = event.target.value;
      if (window.webkit?.messageHandlers?.app) window.webkit.messageHandlers.app.postMessage(JSON.stringify({action:'locale',language:code}));
      else apply(code);
    });
  });
})();
</script>"""


def main():
    path = ROOT / "index.html"
    html = path.read_text()
    catalog = json.loads((ROOT / "translations.json").read_text())
    bundle = (
        "<!-- BEGIN GENERATED LOCALIZATION -->\n"
        + TEMPLATE.replace("__CATALOG__", json.dumps(catalog, ensure_ascii=True))
        + "\n<!-- END GENERATED LOCALIZATION -->"
    )
    if "<!-- BEGIN GENERATED LOCALIZATION -->" in html:
        html = re.sub(
            r"<!-- BEGIN GENERATED LOCALIZATION -->.*?<!-- END GENERATED LOCALIZATION -->",
            lambda _: bundle,
            html,
            flags=re.DOTALL,
        )
    else:
        html = html.replace(
            "  <script>\n    (function()", bundle + "\n  <script>\n    (function()", 1
        )
    uri = (
        "data:image/svg+xml;base64,"
        + base64.b64encode((ROOT / "local-deb-installer.svg").read_bytes()).decode()
    )
    for css, size in [("app-icon-wrap", 26), ("empty-icon-wrap", 44)]:
        pattern = (
            r'(<div class="'
            + css
            + r'" aria-hidden="true">)\s*(?:<svg.*?</svg>|<img[^>]*>)'
        )
        html, count = re.subn(
            pattern,
            lambda m, size=size: (
                m[1]
                + f'\n          <img width="{size}" height="{size}" src="{uri}" alt="">'
            ),
            html,
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("Logo container missing: " + css)
    path.write_text(html)


if __name__ == "__main__":
    main()
