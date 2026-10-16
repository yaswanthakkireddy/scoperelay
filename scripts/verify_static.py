"""Verify the exact reviewed static payload without requiring PowerShell scripts."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json"
PAYLOAD = ROOT / "showcase/public"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["files_sha256"]
    if len(expected) != 8 or not PAYLOAD.is_dir():
        raise SystemExit("STATIC_HASH_FAIL: manifest or payload missing")
    actual = {item.name for item in PAYLOAD.iterdir() if item.is_file()}
    directories = [item.name for item in PAYLOAD.iterdir() if item.is_dir()]
    if actual != set(expected) or directories:
        raise SystemExit(
            f"STATIC_HASH_FAIL: allowlist mismatch files={sorted(actual)} dirs={directories}"
        )
    mismatches = [
        name for name, digest in expected.items() if sha256(PAYLOAD / name) != digest
    ]
    if mismatches:
        raise SystemExit(f"STATIC_HASH_FAIL: digest mismatch {mismatches}")
    print(f"STATIC_HASH_PASS count=8 manifest={sha256(MANIFEST)}")


if __name__ == "__main__":
    main()
