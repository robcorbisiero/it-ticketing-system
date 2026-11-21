"""Build the static GitHub Pages demo from the same UI used by the local app."""

from pathlib import Path
from shutil import copyfile


ROOT = Path(__file__).resolve().parent


def build():
    web = ROOT / "web"
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    for name in ("app.js", "style.css", "pages-demo.js"):
        copyfile(web / name, output / name)

    html = (web / "index.html").read_text(encoding="utf-8")
    substitutions = {
        'href="/style.css"': 'href="./style.css"',
        '<script src="/app.js" defer></script>':
            '<script src="./pages-demo.js" defer></script>\n  <script src="./app.js" defer></script>',
        '<title>Support Desk</title>': '<title>Support Desk · Interactive Demo</title>',
        '<span id="viewer"></span>':
            '<span id="viewer"></span><button id="reset-demo" class="secondary">Reset demo</button>',
        '<main class="shell">':
            '<main class="shell">\n    <p class="demo-notice"><strong>Interactive demo.</strong> '
            'All tickets are fictional. Changes reset when you refresh.</p>',
    }
    for original, replacement in substitutions.items():
        if html.count(original) != 1:
            raise ValueError(f"Expected one occurrence of {original!r} in the web template.")
        html = html.replace(original, replacement)
    (output / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build()
