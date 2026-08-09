"""Check the shareable repository surface for common local-only material.

This intentionally checks tracked files plus non-ignored working-tree files,
without printing matching content. It is a lightweight release gate, not a
replacement for a security review.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ASSETS = {
    "codex.png",
    "gas-cloud.png",
    "home.png",
    "mic-muted.png",
    "mic.png",
    "next.png",
    "previous.png",
    "sleep.png",
    ".gitkeep",
}

# Build host-path fragments without embedding this checker's own examples in
# the scan results.
WINDOWS_USER_PATH = "C:" + "\\Users\\"
WINDOWS_DEV_PATH = "D:" + "\\Dev-"

CONTENT_RULES = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("OpenAI-style key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("Windows user path", re.compile(re.escape(WINDOWS_USER_PATH), re.IGNORECASE)),
    ("development machine path", re.compile(re.escape(WINDOWS_DEV_PATH), re.IGNORECASE)),
)


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = {Path(item) for item in result.stdout.decode().split("\0") if item}
    return sorted(ROOT / path for path in paths)


def private_path(relative: Path) -> bool:
    normalized = relative.as_posix().lower()
    if normalized == "creds.txt" or normalized == ".env" or normalized.endswith((".d6config", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx", ".secret", ".token")):
        return True
    if normalized.startswith("data/"):
        return True
    if normalized.startswith("profiles/") and normalized.endswith(".json"):
        return True
    if normalized.startswith("profiles/assets/") and Path(normalized).name not in PUBLIC_ASSETS:
        return True
    return False


def main() -> int:
    files = candidate_files()
    findings: list[tuple[str, Path, int | None]] = []
    for path in files:
        relative = path.relative_to(ROOT)
        if private_path(relative):
            findings.append(("local-only path", relative, None))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in CONTENT_RULES:
            match = pattern.search(content)
            if match:
                line = content.count("\n", 0, match.start()) + 1
                findings.append((label, relative, line))
    if findings:
        print("Public release check failed:")
        for label, path, line in findings:
            suffix = f":{line}" if line else ""
            print(f"- {label}: {path}{suffix}")
        return 1
    print(f"Public release check passed ({len(files)} shareable files inspected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
