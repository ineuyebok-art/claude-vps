"""The «Красиво» page in a real browser with the network off. Skipped without Playwright."""
import tempfile
import unittest
from pathlib import Path

from tests import harness  # noqa: F401
from ccbot import render

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = """# Отчёт

Формула в тексте $E = mc^2$ и отдельно:

$$
\\int_0^1 x^2 \\, dx = \\frac{1}{3}
$$

| Показатель | Значение |
|---|---|
| **Точность** | 0,97 |
"""


@unittest.skipUnless(sync_playwright, "playwright not installed")
class OfflineRendering(unittest.TestCase):
    def open(self, html):
        requests = []
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as p:
            path = Path(folder) / "answer.html"
            path.write_text(html, encoding="utf-8")
            browser = p.chromium.launch()
            context = browser.new_context(offline=True)
            page = context.new_page()
            page.on("request", lambda r: requests.append(r.url))
            page.goto(path.as_uri())
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
                await document.fonts.ready;
                return {math: document.querySelectorAll('.katex').length,
                        display: document.querySelectorAll('.katex-display').length,
                        cells: document.querySelectorAll('td').length,
                        fonts: [...document.fonts].filter(f => f.status === 'loaded').length,
                        failed: [...document.fonts].filter(f => f.status === 'error').length,
                        text: document.body.innerText};
            }""")
            browser.close()
        return result, requests

    def test_formulas_render_without_network(self):
        result, requests = self.open(render.to_html_page(SAMPLE))
        self.assertGreaterEqual(result["math"], 2)
        self.assertEqual(result["display"], 1)
        self.assertEqual(result["cells"], 2)
        self.assertGreater(result["fonts"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertNotIn("$E", result["text"])
        self.assertEqual([u for u in requests if not u.startswith(("file:", "data:"))], [])

    def test_cheatsheet_has_everything(self):
        text = (ROOT / "docs/cheatsheet.html").read_text(encoding="utf-8")
        for part in ("@файл", "Ctrl+Alt+V", "Esc", "Shift+Tab", "/resume", "/clear", "/model", "!команда",
                     "cc [имя]", "<kbd>1</kbd>"):
            self.assertIn(part, text)
        result, requests = self.open(text)
        self.assertGreater(len(result["text"]), 500)
        self.assertEqual([u for u in requests if not u.startswith(("file:", "data:"))], [])


if __name__ == "__main__":
    unittest.main()
