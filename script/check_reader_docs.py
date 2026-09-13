"""Reader-local links and immutable historical bytes; never a site crawler."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from markdown_it import MarkdownIt

READERS = ("repo-docs", "self_improving/golden_e2e_progress", "docs/contracts")
HISTORY = "docs/history/golden-e2e"
MANDATORY = ("README.md", "AGENTS.md", f"{HISTORY}/README.md")


def safe_path(root: Path, path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ValueError("path_escape")
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError("symlink")
    return path


class HTMLLinks(HTMLParser):
    def __init__(self, line):
        super().__init__(convert_charrefs=True)
        self.line = line
        self.links = []
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("href", "src"):
            if attrs.get(name):
                self.links.append((attrs[name], self.line + self.getpos()[0] - 1))
        for name in ("id", "name" if tag == "a" else "id"):
            if attrs.get(name):
                self.anchors.add(attrs[name])


def document_links(path: Path):
    links = []
    anchors = set()
    heading_slugs = set()
    tokens = MarkdownIt("commonmark").parse(path.read_text())
    for index, block in enumerate(tokens):
        line = block.map[0] + 1 if block.map else 1
        if block.type == "heading_open":
            text = "".join(
                t.content
                for t in tokens[index + 1].children or ()
                if t.type in ("text", "code_inline", "image")
            )
            base = re.sub(r"[^\w -]", "", text.lower()).replace(" ", "-")
            slug, suffix = base, 0
            while slug in heading_slugs:
                suffix += 1
                slug = f"{base}-{suffix}"
            anchors.add(slug)
            heading_slugs.add(slug)
        for token in block.children or (block,):
            if token.type in ("link_open", "image"):
                target = token.attrGet("href" if token.type == "link_open" else "src")
                links.append((target, line))
            if token.type in ("html_inline", "html_block"):
                parser = HTMLLinks(line)
                parser.feed(token.content)
                links.extend(parser.links)
                anchors.update(parser.anchors)
            if token.type in ("softbreak", "hardbreak"):
                line += 1
    return links, anchors


def check_archive(root: Path) -> dict:
    try:
        archive = safe_path(root, root / HISTORY)
        manifest = json.loads(safe_path(root, archive / "manifest.json").read_text())
        if (
            manifest["schema_version"] != "historical_document_archive.v1"
            or manifest["status"] != "historical_non_active"
        ):
            raise ValueError("archive_manifest_identity")
        members, total = set(), 0
        for record in manifest["documents"]:
            name = record["archive_path"]
            original = Path(record["original_path"])
            if (
                original.is_absolute()
                or ".." in original.parts
                or name != f"{HISTORY}/{record['original_commit']}/{original.as_posix()}"
                or type(record["size_bytes"]) is not int
            ):
                raise ValueError("archive_provenance_path")
            path = safe_path(root, root / name)
            if (
                not path.is_relative_to(archive)
                or Path(name).is_absolute()
                or ".." in Path(name).parts
                or path in members
            ):
                raise ValueError("archive_member_scope")
            if not re.fullmatch("[0-9a-f]{40}", record["original_commit"]):
                raise ValueError("archive_original_commit")
            if not path.is_file() or path.stat().st_size > 32 * 1024**2:
                raise ValueError("archive_member_missing_or_oversize")
            data = path.read_bytes()
            total += len(data)
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if (
                total > 256 * 1024**2
                or len(data) != record["size_bytes"]
                or hashlib.sha256(data).hexdigest() != record["sha256"]
                or blob != record["git_blob"]
            ):
                raise ValueError("archive_member_integrity")
            members.add(path)
        for entry in archive.rglob("*"):
            safe_path(root, entry)
        if set(archive.rglob("*.md")) != members | {archive / "README.md"}:
            raise ValueError("archive_unlisted_markdown")
        return {
            "status": "passed",
            "files": len(members),
            "size_bytes": total,
            "internal_links": "original_context_not_run",
        }
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {"status": "failed", "reason": str(error)}


def display_target(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return urlunsplit(
                (parsed.scheme, parsed.hostname or "", parsed.path, "", parsed.fragment)
            )
        return url
    except ValueError:
        return "<invalid-url>"


def remote_probe(url: str) -> dict:
    """A short-lived child pins approved public IPs, including every redirect."""
    deadline = time.monotonic() + 14
    for _ in range(4):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or not parsed.hostname
            or parsed.port not in (None, 80, 443)
        ):
            return {"status": "failed", "reason": "unsafe_remote_url"}
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            return {"status": "failed", "reason": "nonpublic_remote_address"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "failed", "reason": "remote_timeout"}
        cls = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        connection = cls(parsed.hostname, port, timeout=remaining)
        address = addresses[0][4][0]
        # HTTPSConnection still verifies TLS/SNI against the original hostname.
        connection._create_connection = lambda *_args, **_kwargs: socket.create_connection(
            (address, port), timeout=remaining
        )
        try:
            connection.request(
                "HEAD",
                urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                headers={"User-Agent": "canonical-reader-links/1"},
            )
            response = connection.getresponse()
            code = response.status
            location = response.getheader("Location")
        finally:
            connection.close()
        if code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        return {
            "status": "passed" if 200 <= code < 300 else "failed",
            "reason": "http_status",
            "http_status": code,
        }
    return {"status": "failed", "reason": "redirect_limit"}


def check_remote_url(url: str) -> dict:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return {"status": "failed", "reason": "unsafe_remote_url"}
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--remote-probe"],
            input=url,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode:
            return {"status": "failed", "reason": "remote_probe_failure"}
        return json.loads(result.stdout)
    except (ValueError, subprocess.TimeoutExpired):
        return {"status": "failed", "reason": "remote_timeout_or_invalid_url"}


def check_reader_docs(root: Path, *, check_remote: bool = False) -> dict:
    root = root.resolve()
    scope = {root / name for name in MANDATORY}
    errors = []
    for directory in READERS:
        try:
            folder = safe_path(root, root / directory)
            if not folder.is_dir():
                raise ValueError("missing_reader_directory")
            scope.update(folder.rglob("*.md"))
        except ValueError as error:
            errors.append({"path": directory, "reason": str(error)})
    links = []
    for path in sorted(scope):
        try:
            safe_path(root, path)
            if not path.is_file():
                raise ValueError("missing_mandatory_document")
            targets, _ = document_links(path)
        except (ValueError, OSError) as error:
            errors.append({"path": str(path.relative_to(root)), "reason": str(error)})
            continue
        for target, line in targets:
            record = {
                "document": str(path.relative_to(root)),
                "line": line,
                "target": display_target(target),
                "kind": "local",
            }
            try:
                parsed = urlsplit(target)
            except ValueError:
                record.update(status="failed", reason="invalid_url")
                links.append(record)
                continue
            if parsed.scheme or parsed.netloc:
                record["kind"] = "remote"
                record.update(
                    check_remote_url("https:" + target if not parsed.scheme else target)
                    if check_remote
                    else {"status": "not_run", "reason": "remote_not_checked"}
                )
            else:
                try:
                    destination = safe_path(
                        root, path.parent / unquote(parsed.path) if parsed.path else path
                    )
                    if not destination.exists():
                        raise ValueError("missing_file")
                    if parsed.fragment:
                        if destination.suffix.lower() != ".md":
                            raise ValueError("unsupported_fragment_target")
                        if unquote(parsed.fragment) not in document_links(destination)[1]:
                            raise ValueError("missing_fragment")
                    record.update(status="passed", reason="file_exists")
                except ValueError as error:
                    record.update(status="failed", reason=str(error))
            links.append(record)
    failed = bool(errors) or any(
        link["status"] == "failed" and link["kind"] == "local" for link in links
    )
    archive = check_archive(root)
    remotes = [link for link in links if link["kind"] == "remote"]
    remote = (
        "failed"
        if any(link["status"] == "failed" for link in remotes)
        else "not_run"
        if any(link["status"] == "not_run" for link in remotes)
        else "passed"
    )
    return {
        "local_links": "failed" if failed else "passed",
        "links": links,
        "errors": errors,
        "scope": sorted(str(path.relative_to(root)) for path in scope),
        "archive_bytes": archive["status"],
        "archive": archive,
        "remote_http": remote,
        "overall": (
            "failed"
            if failed or archive["status"] == "failed" or remote == "failed"
            else "not_fully_verified"
            if remote == "not_run"
            else "passed"
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check-remote", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--remote-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.remote_probe:
        try:
            result = remote_probe(sys.stdin.read())
        except (ValueError, OSError, http.client.HTTPException):
            result = {"status": "failed", "reason": "remote_connection_failure"}
        print(json.dumps(result))
        return 0
    report = check_reader_docs(args.root, check_remote=args.check_remote)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(text)
    print(text, end="")
    return int(report["overall"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
