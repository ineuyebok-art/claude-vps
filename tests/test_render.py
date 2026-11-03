import unittest

from tests import harness  # noqa: F401 — sets sys.path
from ccbot import render


class TelegramMarkup(unittest.TestCase):
    def test_basic(self):
        html = render.to_telegram("# Заголовок\n**жирный** и *курсив* и `код`\n- пункт")
        self.assertIn("<b>Заголовок</b>", html)
        self.assertIn("<b>жирный</b>", html)
        self.assertIn("<i>курсив</i>", html)
        self.assertIn("<code>код</code>", html)
        self.assertIn("• пункт", html)

    def test_escapes_html(self):
        self.assertEqual(render.to_telegram("a < b && c > d"), "a &lt; b &amp;&amp; c &gt; d")

    def test_code_is_not_formatted(self):
        html = render.to_telegram("```python\nx = **2** < 3\n```")
        self.assertEqual(html, '<pre><code class="language-python">x = **2** &lt; 3</code></pre>')

    def test_table_is_monospace(self):
        html = render.to_telegram("| имя | число |\n|---|---|\n| а | 1 |\n| бб | 22 |")
        self.assertTrue(html.startswith("<pre>"))
        self.assertIn("имя │ число", html)
        self.assertIn("бб  │ 22", html)

    def test_unclosed_markup_stays_valid_while_streaming(self):
        self.assertEqual(render.to_telegram("**незакрыт"), "**незакрыт")
        self.assertTrue(render.to_telegram("```\nкод").endswith("</pre>"))

    def test_star_inside_expression_is_not_italic(self):
        self.assertEqual(render.to_telegram("2*3*4"), "2*3*4")

    def test_split_keeps_code_blocks_closed(self):
        text = "```\n" + "\n".join("строка " * 10 for _ in range(300)) + "\n```"
        chunks = render.split_markdown(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), render.CHUNK + 10)
            self.assertEqual(chunk.count("```") % 2, 0)


class HtmlPage(unittest.TestCase):
    def test_has_no_external_resources(self):
        page = render.to_html_page("# T\n\n$x^2$\n\n| a |\n|---|\n| 1 |")
        self.assertNotRegex(page, r'(?:src|href)=["\']https?:')
        self.assertNotIn("url(fonts", page)
        self.assertNotRegex(page, r"url\(https?:")
        self.assertIn("renderMathInElement", page)
        self.assertIn("<table>", page)

    def test_math_is_not_mangled(self):
        body = render.to_html_body("Формула $a_1 * b_2 * c$ и **жирный**\n\n$$\n\\frac{1}{2}\n$$")
        self.assertIn("$a_1 * b_2 * c$", body)
        self.assertIn("<strong>жирный</strong>", body)
        self.assertIn("\\frac{1}{2}", body)

    def test_script_in_answer_is_escaped(self):
        page = render.to_html_body("<script>alert(1)</script>")
        self.assertNotIn("<script>", page)

    def test_lists(self):
        body = render.to_html_body("- a\n- b\n  - c\n1. x")
        self.assertIn("<ul><li>a</li><li>b<ul><li>c</li></ul>", body)


if __name__ == "__main__":
    unittest.main()
