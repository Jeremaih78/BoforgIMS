"""Count OpenAI-key patterns in the affected document's local Git history.

Owner authorization to inspect this document's historical contents is required.
No blob content, matches, key fragments, commit messages or hashes are printed.
This does not rewrite history or inspect secret environment files.
"""

import codecs
import re
import subprocess
from pathlib import Path

from tools.redact_tasker_api_key import MAX_BYTES, TARGET, TOKEN


class AuditRefused(Exception):
    pass


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode:
        raise AuditRefused()
    return result.stdout


def _text(blob):
    if blob.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return blob.decode("utf-32")
    if blob.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return blob.decode("utf-16")
    if b"\0" in blob:
        raise AuditRefused()
    return blob.decode("utf-8-sig")


def audit_history(repo):
    """Inspect only the fixed path under refs already present in this checkout."""
    if TARGET != Path("tasker/README_AI_Extensions.md"):
        raise AuditRefused()
    commits = _git(repo, "rev-list", "--all", "--full-history", "--", TARGET.as_posix()).splitlines()
    objects = set()
    matched = 0
    pattern = re.compile(TOKEN.pattern.decode("ascii"))
    for commit in commits:
        if not re.fullmatch(rb"[0-9a-f]{40,64}", commit):
            raise AuditRefused()
        entry = _git(repo, "ls-tree", "-z", commit.decode("ascii"), "--", TARGET.as_posix())
        if not entry:
            continue  # A deletion has no blob at this path.
        metadata, name = entry.rstrip(b"\0").split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != b"blob" or name != TARGET.as_posix().encode() or mode not in (b"100644", b"100755"):
            raise AuditRefused()
        if not re.fullmatch(rb"[0-9a-f]{40,64}", object_id):
            raise AuditRefused()
        if object_id in objects:
            continue
        size = int(_git(repo, "cat-file", "-s", object_id.decode("ascii")))
        if size < 0 or size > MAX_BYTES:
            raise AuditRefused()
        blob = _git(repo, "cat-file", "blob", object_id.decode("ascii"))
        if len(blob) != size:
            raise AuditRefused()
        matched += bool(pattern.search(_text(blob)))
        objects.add(object_id)
    return len(commits), len(objects), matched


def main():
    try:
        commits, objects, matched = audit_history(Path(__file__).resolve().parents[1])
    except Exception:
        print("REFUSED: historical assessment incomplete; no secret values shown.")
        return 2
    print(f"Historical revisions: {commits}; unique document blobs: {objects}; key-pattern blobs: {matched}.")
    print("Scope: fixed document path in local Git refs only; no history was changed.")
    print("Other paths, earlier rename paths, reflogs, remote copies and chat/artifact copies are not certified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
