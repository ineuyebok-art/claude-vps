"""Markdown → HTML Telegram и Markdown → самостоятельная .html-страница."""
import html
import re
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
TG_LIMIT = 4096
CHUNK = 3800

TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _cells(line):
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") else line
    return [c.strip() for c in line.split("|")]


def _plain(text):
    """Убирает разметку из ячейки таблицы."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def table_mono(rows):
    """Таблица в моноширинном виде."""
    rows = [[_plain(c) for c in row] for row in rows]
    columns = max(len(r) for r in rows)
    rows = [r + [""] * (columns - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(columns)]
    lines = [" │ ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    if len(lines) > 1:
        lines.insert(1, "─┼─".join("─" * w for w in widths))
    return "\n".join(lines)


def _inline(text):
    """Строчная разметка. Код защищаем первым: внутри него разметки нет."""
    stash = []

    def keep(fragment):
        stash.append(fragment)
        return f"\0{len(stash) - 1}\0"

    text = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
                  lambda m: keep(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1), quote=False)}</a>'),
                  text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    return re.sub(r"\0(\d+)\0", lambda m: stash[int(m.group(1))], text)


def to_telegram(markdown):
    """Переводит Markdown в подмножество HTML, которое понимает Telegram."""
    lines = markdown.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^\s*```(\S*)", line)
        if fence:
            body = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*```\s*$", lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(body), quote=False)
            lang = re.sub(r"[^\w+-]", "", fence.group(1))
            attr = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{attr}>{code}</code></pre>")
            continue
        if "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            out.append(f"<pre>{html.escape(table_mono(rows), quote=False)}</pre>")
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            out.append(f"<b>{_inline(heading.group(1))}</b>")
        elif re.match(r"^\s{0,3}>\s?", line):
            quote = []
            while i < len(lines) and re.match(r"^\s{0,3}>\s?", lines[i]):
                quote.append(_inline(re.sub(r"^\s{0,3}>\s?", "", lines[i])))
                i += 1
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            continue
        elif re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            out.append("———")
        else:
            bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
            if bullet:
                out.append(f"{bullet.group(1)}• {_inline(bullet.group(2))}")
            else:
                out.append(_inline(line))
        i += 1
    return "\n".join(out)


def split_markdown(text, limit=CHUNK):
    """Режет длинный ответ на куски, не разрывая блоки кода."""
    if len(text) <= limit:
        return [text]
    chunks, current, size, fenced = [], [], 0, False
    for line in text.split("\n"):
        pieces = [line[i:i + limit] for i in range(0, len(line), limit)] or [""]
        for piece in pieces:
            if size + len(piece) + 1 > limit and current:
                if fenced:
                    current.append("```")
                chunks.append("\n".join(current))
                current, size = (["```"], 4) if fenced else ([], 0)
            current.append(piece)
            size += len(piece) + 1
        if line.lstrip().startswith("```"):
            fenced = not fenced
    if current:
        chunks.append("\n".join(current))
    return chunks


# ------------------------------------------------------------- .html-файл ---
PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{katex_css}</style>
<style>
:root {{ color-scheme: light dark; --bg:#fff; --ink:#1f2328; --muted:#59636e; --line:#d1d9e0; --code:#f6f8fa; --accent:#0969da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --ink:#e6edf3; --muted:#9198a1; --line:#3d444d; --code:#161b22; --accent:#4493f8; }} }}
body {{ margin:0 auto; max-width:46rem; padding:1.5rem 1rem 4rem; background:var(--bg); color:var(--ink); font:17px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif; }}
h1,h2,h3,h4 {{ line-height:1.25; margin:1.6em 0 .6em; }} h1 {{ font-size:1.7em; }} h2 {{ font-size:1.35em; }} h3 {{ font-size:1.1em; }}
a {{ color:var(--accent); }} blockquote {{ margin:1em 0; padding:0 1em; color:var(--muted); border-left:.25em solid var(--line); }}
pre,code {{ font:.9em ui-monospace,Consolas,monospace; background:var(--code); border-radius:6px; }}
code {{ padding:.15em .35em; }} pre {{ padding:.9em 1em; overflow:auto; }} pre code {{ padding:0; background:none; }}
.scroll {{ overflow-x:auto; margin:1em 0; }} table {{ border-collapse:collapse; }} th,td {{ border:1px solid var(--line); padding:.4em .7em; text-align:left; }} th {{ background:var(--code); }}
hr {{ border:0; border-top:1px solid var(--line); margin:2em 0; }} .katex-display {{ overflow-x:auto; overflow-y:hidden; padding:.3em 0; }}
</style></head><body>
{body}
<script>{katex_js}</script>
<script>
renderMathInElement(document.body, {{ throwOnError: false, delimiters: [
  {{left: "$$", right: "$$", display: true}}, {{left: "\\\\[", right: "\\\\]", display: true}},
  {{left: "\\\\(", right: "\\\\)", display: false}}, {{left: "$", right: "$", display: false}} ] }});
</script></body></html>
"""


def _inline_page(text):
    stash = []

    def keep(fragment):
        stash.append(fragment)
        return f"\0{len(stash) - 1}\0"

    text = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = re.sub(r"\$[^$\n]+\$|\\\([^\n]+?\\\)", lambda m: keep(html.escape(m.group(0), quote=False)), text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
                  lambda m: keep(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1), quote=False)}</a>'),
                  text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    return re.sub(r"\0(\d+)\0", lambda m: stash[int(m.group(1))], text)


def _list_block(lines, i):
    """Разбирает список с вложенностью по отступу."""
    item = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
    html_out = []
    stack = []
    while i < len(lines):
        match = item.match(lines[i])
        if not match:
            if not lines[i].strip() and i + 1 < len(lines) and item.match(lines[i + 1]):
                i += 1
                continue
            break
        indent, marker = len(match.group(1)), match.group(2)
        tag = "ul" if marker in "-*+" else "ol"
        while stack and indent < stack[-1][0]:
            html_out.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            html_out.append(f"<{tag}>")
            stack.append((indent, tag))
        else:
            html_out.append("</li>")
        html_out.append(f"<li>{_inline_page(match.group(3))}")
        i += 1
    while stack:
        html_out.append(f"</li></{stack.pop()[1]}>")
    return "".join(html_out), i


def to_html_body(markdown):
    lines = markdown.split("\n")
    out = []
    para = []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_inline_page(p) for p in para) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*```", line):
            flush()
            body = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*```\s*$", lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(body), quote=False) + "</code></pre>")
        elif line.strip().startswith("$$"):
            flush()
            body = [line]
            if not (line.strip().endswith("$$") and len(line.strip()) > 2):
                i += 1
                while i < len(lines):
                    body.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        break
                    i += 1
            i += 1
            out.append("<p>" + html.escape("\n".join(body), quote=False) + "</p>")
        elif "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            parts = ['<div class="scroll"><table><thead><tr>']
            parts += [f"<th>{_inline_page(c)}</th>" for c in head]
            parts.append("</tr></thead><tbody>")
            for row in rows:
                parts.append("<tr>" + "".join(f"<td>{_inline_page(c)}</td>" for c in row) + "</tr>")
            parts.append("</tbody></table></div>")
            out.append("".join(parts))
        elif re.match(r"^\s{0,3}#{1,6}\s", line):
            flush()
            match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
            level = len(match.group(1))
            out.append(f"<h{level}>{_inline_page(match.group(2))}</h{level}>")
            i += 1
        elif re.match(r"^\s{0,3}>\s?", line):
            flush()
            quote = []
            while i < len(lines) and re.match(r"^\s{0,3}>\s?", lines[i]):
                quote.append(re.sub(r"^\s{0,3}>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + to_html_body("\n".join(quote)) + "</blockquote>")
        elif re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
        elif re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            flush()
            block, i = _list_block(lines, i)
            out.append(block)
        elif not line.strip():
            flush()
            i += 1
        else:
            para.append(line.strip())
            i += 1
    flush()
    return "\n".join(out)


def to_html_page(markdown, title="Ответ Claude"):
    return PAGE.format(title=html.escape(title), body=to_html_body(markdown),
                       katex_css=(ASSETS / "katex.css").read_text(encoding="utf-8"),
                       katex_js=(ASSETS / "katex.js").read_text(encoding="utf-8"))
