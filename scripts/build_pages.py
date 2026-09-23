"""Build only allowlisted public files; never reads application settings or .env."""
import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def build(api_base, output):
    parsed = urlsplit(api_base)
    if (parsed.scheme != "https" or not parsed.hostname or (parsed.username is not None or parsed.password is not None or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname))
            or parsed.path or parsed.query or parsed.fragment or any(c in api_base for c in "<>\"'")):
        raise ValueError("API base must be an HTTPS origin without a path or credentials")
    root = Path(__file__).resolve().parents[1]
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new empty output directory; existing files are never deleted")
    (output / "static").mkdir(parents=True, exist_ok=True)
    html = (root / "app/templates/index.html").read_text(encoding="utf-8")
    policy = f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src {api_base}; object-src 'none'; base-uri 'none'; form-action 'none'"
    html = html.replace('<meta name="referrer"', f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n  <meta name="referrer"')
    (output / "index.html").write_text(html, encoding="utf-8")
    for name in ["app.js", "style.css"]:
        shutil.copyfile(root / "app/static" / name, output / "static" / name)
    (output / "static/config.js").write_text("window.EXPLORER_CONFIG = " + json.dumps({"apiBase": api_base}) + ";\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", default="dist/pages")
    args = parser.parse_args()
    build(args.api_base, args.output)
