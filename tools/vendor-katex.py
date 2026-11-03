#!/usr/bin/env python3
"""Builds bot/ccbot/assets/katex.{js,css} from the npm tarball: fonts inlined, nothing external.

Usage: tools/vendor-katex.py <path to unpacked katex package>
"""
import base64
import pathlib
import re
import sys

package = pathlib.Path(sys.argv[1])
assets = pathlib.Path(__file__).resolve().parent.parent / "bot" / "ccbot" / "assets"
css = (package / "dist" / "katex.min.css").read_text(encoding="utf-8")


def inline(match):
    data = (package / "dist" / match.group(1)).read_bytes()
    return "url(data:font/woff2;base64," + base64.b64encode(data).decode() + ") format(\"woff2\")"


css = re.sub(r"url\((fonts/[^)]+\.woff2)\)\s*format\(\"woff2\"\)", inline, css)
css = re.sub(r",\s*url\(fonts/[^)]+\)\s*format\(\"[a-z]+\"\)", "", css)
if "url(fonts" in css:
    sys.exit("external font reference left in CSS")
js = (package / "dist" / "katex.min.js").read_text(encoding="utf-8")
js += "\n" + (package / "dist" / "contrib" / "auto-render.min.js").read_text(encoding="utf-8")
(assets / "katex.css").write_text(css, encoding="utf-8")
(assets / "katex.js").write_text(js.replace("</script", "<\\/script"), encoding="utf-8")
(assets / "KATEX-LICENSE").write_text((package / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
print(f"css {len(css)//1024} KB, js {len(js)//1024} KB")
