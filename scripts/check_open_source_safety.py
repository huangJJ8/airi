"""Open-source safety scanner for the AIRI repository.

A lightweight guardrail (not a full secret scanner) that fails the build when
high-confidence sensitive patterns appear in files that would be published to
a public repository:

    forbidden company / customer identifiers
    private keys
    credential-shaped tokens (AWS keys, GitHub tokens, Slack tokens, OpenAI keys)
    Chinese national ID card numbers
    real Windows user profile paths
    non-example e-mail addresses
    internal-only DNS suffixes

Design notes:
- Only high-precision value shapes are checked. Field *names* like ``token`` or
  ``password`` are legitimate model/config fields and are NOT flagged.
- Opaque synthetic identifiers used consistently by the demo fixtures
  (``c_db`` / ``tmp_db`` / ``demo`` databases) contain no real data and are
  deliberately NOT flagged. See SECURITY.md.
- Unit-test placeholder secrets are allowlisted explicitly.
- ``users.noreply.github.com`` is treated like ``example.`` — it is GitHub's own
  public no-reply domain, published by design, and carries no private address.
  Documentation that tells contributors to *use* it must not fail this scan.
  Nothing else about the e-mail rule is relaxed: real personal or corporate
  addresses are still findings.

Usage:
    uv run --frozen python scripts/check_open_source_safety.py

Exit code 0 = clean, 1 = findings (prints each finding with file and line).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git",
    ".venv",
    ".uv-cache",
    ".demo",
    ".ruff_cache",
    ".pytest_cache",
    ".workbuddy",
    ".tools",
    ".airi_tmp",
    "node_modules",
    "__pycache__",
    "dist",
    "htmlcov",
}
SKIP_FILE_SUFFIXES = {".db", ".png", ".jpg", ".gif", ".mp4", ".whl", ".pyc", ".lock"}
SKIP_DIR_NAMES = {"pytest-cache-files-7q17qjai", "coverage"}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".sql",
    ".toml",
    ".txt",
    ".ts",
    ".vue",
    ".j2",
    ".yml",
    ".yaml",
    ".cfg",
    ".ini",
    ".mako",
    ".html",
    ".js",
    ".mjs",
    ".css",
    ".example",
    ".ps1",
    ".sh",
    ".gitignore",
    ".editorconfig",
}

# High-confidence patterns. (name, compiled regex)
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "forbidden_company_identifier",
        re.compile(r"51baiwang|baiwang\.com", re.IGNORECASE),
    ),
    (
        "forbidden_customer_name",
        re.compile(r"工行河北|重庆银行|富民银行|度小满"),
    ),
    ("private_key", re.compile(r"BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("chinese_id_card", re.compile(r"\b\d{17}[0-9Xx]\b")),
    (
        "windows_user_path",
        re.compile(r"[Cc]:\\Users\\(?!Public|Default|All Users)[A-Za-z][\w.-]*"),
    ),
    (
        "internal_dns_suffix",
        re.compile(r"\b[\w-]+\.(?:corp|intranet|internal)\b", re.IGNORECASE),
    ),
    (
        "non_example_email",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@"
            r"(?!example\.|test\.|localhost|synthetic\.|users\.noreply\.github\.com)"
            r"(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|io)\b"
        ),
    ),
]

# Exact strings that are known-safe placeholders (unit-test fixtures).
ALLOWED_VALUES = {
    "unit-test-secret",
    "another-secret",
    "attested-value",
    "private-key",
    "mock-model",
    "change-me",
    "configure-your-model",
}

# This scanner contains the forbidden patterns itself; never scan it.
SELF_PATH = Path(__file__).resolve()

# ISO 7064 checksum for Chinese resident ID cards (18 digits, last may be X).
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"


def _looks_like_real_id_card(value: str) -> bool:
    """High-precision filter: non-zero region prefix + valid ISO 7064 checksum."""
    if len(value) != 18 or not value[0].isdigit() or value[0] == "0":
        return False
    body, check = value[:17], value[17].upper()
    if not body.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(body, _ID_WEIGHTS))
    return _ID_CHECK_CHARS[total % 11] == check


def iter_candidate_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(ROOT).parts
        if any(part in SKIP_DIRS or part in SKIP_DIR_NAMES for part in rel_parts[:-1]):
            continue
        if path.suffix in SKIP_FILE_SUFFIXES:
            continue
        if path.name not in {".env.example", ".gitignore", "LICENSE"} and (
            path.suffix not in TEXT_SUFFIXES
        ):
            continue
        files.append(path)
    return files


def scan() -> list[tuple[str, Path, int, str]]:
    findings: list[tuple[str, Path, int, str]] = []
    for path in iter_candidate_files():
        if path.resolve() == SELF_PATH:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable -> out of scope for this scanner
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(0)
                    if value in ALLOWED_VALUES:
                        continue
                    if name == "chinese_id_card" and not _looks_like_real_id_card(value):
                        continue
                    findings.append((name, path, lineno, line.strip()[:160]))
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("open-source safety scan: clean (no high-confidence sensitive patterns)")
        return 0
    print(f"open-source safety scan: {len(findings)} finding(s)")
    for name, path, lineno, line in findings:
        print(f"  [{name}] {path.relative_to(ROOT)}:{lineno}")
        print(f"    {line}")
    print("Resolve these before publishing the repository.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
