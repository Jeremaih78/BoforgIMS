"""Owner-assisted, fixed-path OpenAI key cleanup; dry-run unless --write.

This does not revoke keys or rewrite Git history. It never prints file content,
matches, paths or exception text. Do not run without the owner's authorization
to inspect tasker/README_AI_Extensions.md.
"""

import argparse
import os
from pathlib import Path
import re
import stat
import tempfile


TARGET = Path("tasker/README_AI_Extensions.md")
MAX_BYTES = 2 * 1024 * 1024
TOKEN = re.compile(rb"(?<![A-Za-z0-9_-])sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")
REPLACEMENT = b"<REDACTED_OPENAI_API_KEY>"


class CleanupRefused(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise CleanupRefused()


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _target(repo):
    root = Path(os.path.abspath(repo))
    if any(_linked(part) for part in (root, *root.parents)) or not root.is_dir():
        raise CleanupRefused()
    target = root / TARGET
    if TARGET != Path("tasker/README_AI_Extensions.md"):
        raise CleanupRefused()
    if any(_linked(part) for part in (target.parent, target)):
        raise CleanupRefused()
    resolved = target.resolve(strict=True)
    if resolved != target or not resolved.is_relative_to(root) or not target.is_file():
        raise CleanupRefused()
    return target


def _read(path):
    if path.stat().st_size > MAX_BYTES:
        raise CleanupRefused()
    with path.open("rb") as stream:
        value = stream.read(MAX_BYTES + 1)
    if len(value) > MAX_BYTES or b"\0" in value:
        raise CleanupRefused()
    try:
        value.decode("utf-8-sig")
    except UnicodeError:
        raise CleanupRefused() from None
    return value


def redact_bytes(original):
    """Only replaces complete recognizable OpenAI tokens; preserves other bytes."""
    cleaned, count = TOKEN.subn(REPLACEMENT, original)
    if TOKEN.search(cleaned):
        raise CleanupRefused()
    return cleaned, count


def cleanup(repo, *, write=False):
    target = _target(repo)
    original = _read(target)
    cleaned, count = redact_bytes(original)
    if not write or not count:
        return count
    descriptor, temporary = tempfile.mkstemp(prefix=".tasker-key-cleanup-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(cleaned)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        # Recheck location and bytes immediately before atomic replacement. A
        # concurrent edit aborts instead of replacing a different saved version.
        if _target(repo) != target or _read(target) != original:
            raise CleanupRefused()
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Checkout root; only the fixed README is eligible")
    parser.add_argument("--write", action="store_true", help="Replace matching tokens after an authorized dry-run")
    try:
        options = parser.parse_args(argv)
        count = cleanup(options.repo, write=options.write)
    except Exception:
        print("REFUSED: cleanup could not safely complete; no secret values shown.")
        return 2
    print(f"{'REDACTED' if options.write else 'DRY RUN'}: {count} matching OpenAI key token(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
