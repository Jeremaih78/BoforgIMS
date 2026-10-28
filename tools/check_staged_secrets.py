r"""Manual index-only secret gate. Never reads the working tree or Git history.

Run: venv\Scripts\python.exe tools/check_staged_secrets.py
Only added/copied/modified staged blobs are inspected. Sensitive staged paths
are blocked even on deletion/rename. A pass is not credential revocation.
"""

import argparse
import ast
import codecs
import io
import os
import posixpath
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass


MAX_TEXT_BYTES = 2 * 1024 * 1024
PROVIDER_TOKENS = re.compile(
    r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{30,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
ASSIGNMENT = re.compile(
    r"\b(?:[A-Z][A-Z0-9_]*_)?(?:API[_-]?KEY|ACCESS_TOKEN|AUTH_TOKEN|TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD)"
    r"\b[\"']?[ \t]*[:=][ \t]*(?:[\"']([^\"'\r\n]{12,})[\"']|([^\s#;,\"']{12,}))",
    re.IGNORECASE,
)
SECRET_NAME = re.compile(
    r"(?:[A-Z][A-Z0-9_]*_)?(?:API[_-]?KEY|ACCESS_TOKEN|AUTH_TOKEN|TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def excluded_path(path):
    normalized = posixpath.normpath(path.replace("\\", "/")).casefold()
    name = posixpath.basename(normalized)
    return name == "readme_ai_extensions.md" or (
        name != ".env.example" and (name == ".env" or name.startswith(".env."))
    )


def _placeholder(value):
    value = value.strip()
    if value.startswith(("${", "{{", "<", "os.environ", "os.getenv(", "getenv(", "process.env.", "settings.")):
        return True
    lowered = value.lower()
    for prefix in ("sk-proj-", "sk-svcacct-", "sk-", "github_pat_", "ghp_", "gho_"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix):]
            break
    normalized = lowered.replace("-", "_")
    return normalized in {
        "your_api_key", "your_api_key_here", "your_key_here", "your_token_here",
        "replace_me", "change_me", "changeme", "redacted", "placeholder",
        "example_api_key", "example_token", "insert_key_here", "insert_token_here",
    } or bool(re.fullmatch(r"[x*0]+", lowered))


def _plain_assignment_found(text):
    return any(not _placeholder(match.group(1) or match.group(2)) for match in ASSIGNMENT.finditer(text))


def _literal_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value.decode("utf-8", errors="replace") if isinstance(node.value, bytes) else node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _python_assignment_found(text):
    """Inspect literal values, not executable Python expressions or quote boundaries.

    Embedded configuration strings are still inspected after decoding. A parse
    failure falls back to the generic scanner rather than exempting the file.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return _plain_assignment_found(text)
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and _plain_assignment_found(token.string):
                return True
    except (tokenize.TokenError, IndentationError):
        return _plain_assignment_found(text)
    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.Name, ast.Attribute)):
                    bindings.append((target.id if isinstance(target, ast.Name) else target.attr, node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            target = node.target
            if isinstance(target, (ast.Name, ast.Attribute)):
                bindings.append((target.id if isinstance(target, ast.Name) else target.attr, node.value))
        elif isinstance(node, ast.keyword) and node.arg:
            bindings.append((node.arg, node.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    bindings.append((key.value, value))
        elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            if _plain_assignment_found(_literal_text(node)):
                return True
    for name, value in bindings:
        literal = _literal_text(value)
        if SECRET_NAME.fullmatch(name) and literal is not None and len(literal) >= 12 and not _placeholder(literal):
            return True
    return False


def scan_text(text, *, source_path=None):
    """Return fixed reason labels only; never return captured secret material."""
    reasons = []
    if PRIVATE_KEY.search(text):
        reasons.append("private-key marker")
    if any(not _placeholder(match.group()) for match in PROVIDER_TOKENS.finditer(text)):
        reasons.append("API-key/token pattern")
    python_source = source_path is not None and posixpath.splitext(source_path.replace("\\", "/"))[1].casefold() == ".py"
    if (_python_assignment_found(text) if python_source else _plain_assignment_found(text)):
        reasons.append("credential assignment")
    return tuple(reasons)


def _decode_text(blob):
    if blob.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return blob.decode("utf-32")
    if blob.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return blob.decode("utf-16")
    if b"\0" in blob:
        return None
    return blob.decode("utf-8-sig", errors="replace")


def inspect_staged(paths, read_staged):
    """The injected reader must return index bytes, never working-tree content."""
    paths = list(paths)
    findings = [Finding(path, "excluded sensitive path; content not read") for path in paths if excluded_path(path)]
    if findings:
        return findings
    for path in paths:
        try:
            blob = read_staged(path)
            if len(blob) > MAX_TEXT_BYTES:
                findings.append(Finding(path, "size exceeds gate limit; review separately"))
                continue
            text = _decode_text(blob)
            if text is not None:
                findings.extend(Finding(path, reason) for reason in scan_text(text, source_path=path))
        except Exception:
            # Git/decoder exceptions can contain input data. Never echo them.
            findings.append(Finding(path, "unable to inspect staged content"))
    return findings


def report_findings(findings, output):
    for finding in findings:
        # Escape control characters in filenames; no content snippets or matches.
        safe_path = "[redacted filename]" if scan_text(finding.path) else ascii(finding.path)
        print(f"BLOCKED {safe_path}: {finding.reason}", file=output)
    if findings:
        print("Staged secret gate failed. Review blocked paths; no secret values are shown.", file=output)
    else:
        print("Staged secret gate passed: no blocked paths or detected patterns.", file=output)


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", os.fspath(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode:
        raise RuntimeError("Git index inspection failed")
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository directory (default: current directory)")
    options = parser.parse_args(argv)
    try:
        args = ("diff", "--cached", "--name-only", "--no-ext-diff", "--no-renames", "-z")
        names = _git(options.repo, *args)
        paths = [os.fsdecode(name) for name in names.split(b"\0") if name]
        # Inspect every staged name first, including deleted rename sources.
        # If an excluded file is involved, read no blobs at all.
        if any(excluded_path(path) for path in paths):
            findings = inspect_staged(paths, None)
        else:
            names = _git(options.repo, *args, "--diff-filter=ACM")
            paths = [os.fsdecode(name) for name in names.split(b"\0") if name]
            findings = inspect_staged(paths, lambda path: _git(options.repo, "show", "--no-ext-diff", "--no-textconv", f":{path}"))
    except Exception:
        print("Staged secret gate could not inspect the Git index; no content is shown.", file=sys.stderr)
        return 2
    report_findings(findings, sys.stdout)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
