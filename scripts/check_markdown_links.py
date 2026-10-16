"""Check repository-relative Markdown links without network access."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    missing: list[str] = []
    for document in sorted(ROOT.rglob("*.md")):
        if any(part in {".git", ".venv", ".gstack"} for part in document.parts):
            continue
        content = document.read_text(encoding="utf-8")
        for target in LINK.findall(content):
            target = target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            resolved = (document.parent / path).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    if missing:
        print("MARKDOWN_LINKS_FAIL")
        print("\n".join(missing))
        return 1
    print("MARKDOWN_LINKS_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
