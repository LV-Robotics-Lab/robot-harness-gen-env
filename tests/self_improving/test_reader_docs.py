"""Public reader-document gate; real Markdown parser and filesystem fixtures."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from script.check_reader_docs import check_reader_docs


def reader_root(tmp_path, text=""):
    for path in (
        "repo-docs",
        "docs/contracts",
        "self_improving/golden_e2e_progress",
        "docs/history/golden-e2e",
    ):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text(text)
    (tmp_path / "AGENTS.md").write_text("# Instructions\n")
    (tmp_path / "docs/history/golden-e2e/README.md").write_text("# History\n")
    (tmp_path / "docs/history/golden-e2e/manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "historical_document_archive.v1",
                "status": "historical_non_active",
                "documents": [],
            }
        )
    )
    return tmp_path


def test_reader_reports_existing_and_missing_links_through_public_gate(tmp_path):
    root = reader_root(tmp_path, "[good](AGENTS.md)\n[bad](absent.md)\n")
    report = check_reader_docs(root)
    assert report["local_links"] == "failed"
    assert [(link["target"], link["status"]) for link in report["links"]] == [
        ("AGENTS.md", "passed"),
        ("absent.md", "failed"),
    ]
    assert report["links"][1]["reason"] == "missing_file"
    assert report["links"][1]["line"] == 2


def test_parser_handles_reference_escape_code_html_and_fragments(tmp_path):
    root = reader_root(
        tmp_path,
        """
# 重复
# 重复
<a id="explicit"></a>

[inline](#重复)
[second][ref]
[missing](#absent)
[space](<a b.md>)
[escape](a\\(b\\).md)
<a href="#explicit">HTML</a>

![image](image.png)
`[not-a-link](missing-inline.md)`
```md
[not-a-link](missing-fence.md)
```
[ref]: #重复-1
""",
    )
    for name in ("a b.md", "a(b).md", "image.png"):
        (root / name).write_bytes(b"fixture")
    report = check_reader_docs(root)
    failed = [link for link in report["links"] if link["status"] == "failed"]
    assert [(link["target"], link["reason"]) for link in failed] == [
        ("#absent", "missing_fragment"),
    ]
    assert len(report["links"]) == 7
    assert any(
        link["target"] == "#explicit" and link["status"] == "passed" for link in report["links"]
    )


def test_missing_mandatory_and_symlink_are_not_omitted(tmp_path):
    root = reader_root(tmp_path)
    (root / "AGENTS.md").unlink()
    (root / "repo-docs/escape.md").symlink_to(tmp_path.parent / "outside")
    report = check_reader_docs(root)
    assert report["local_links"] == "failed"
    assert {error["reason"] for error in report["errors"]} == {
        "missing_mandatory_document",
        "symlink",
    }


def add_history(root):
    data = b"# Old instructions\n[original relative link](unavailable.md)\n"
    name = "docs/history/golden-e2e/" + "a" * 40 + "/old.md"
    target = root / name
    target.parent.mkdir()
    target.write_bytes(data)
    record = {
        "original_commit": "a" * 40,
        "original_path": "old.md",
        "archive_path": name,
        "git_blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    manifest = root / "docs/history/golden-e2e/manifest.json"
    body = json.loads(manifest.read_text())
    body["documents"] = [record]
    manifest.write_text(json.dumps(body))
    return target, manifest


def test_archive_checks_exact_bytes_without_reinterpreting_old_instructions(tmp_path):
    root = reader_root(tmp_path)
    add_history(root)
    report = check_reader_docs(root)
    assert report["archive_bytes"] == "passed"
    assert report["local_links"] == "passed"
    assert not any("unavailable" in link["target"] for link in report["links"])


@pytest.mark.parametrize("attack", ["bytes", "size", "blob", "escape", "unlisted", "symlink"])
def test_archive_rejects_corruption_and_scope_escape(tmp_path, attack):
    root = reader_root(tmp_path)
    target, manifest = add_history(root)
    body = json.loads(manifest.read_text())
    if attack == "bytes":
        target.write_bytes(b"changed")
    elif attack == "size":
        body["documents"][0]["size_bytes"] += 1
    elif attack == "blob":
        body["documents"][0]["git_blob"] = "0" * 40
    elif attack == "escape":
        body["documents"][0]["archive_path"] = "../outside.md"
    elif attack == "unlisted":
        (target.parent / "extra.md").write_text("not inventoried")
    else:
        target.unlink()
        target.symlink_to(root / "README.md")
    manifest.write_text(json.dumps(body))
    assert check_reader_docs(root)["archive_bytes"] == "failed"


def test_remote_default_is_explicit_not_run_and_never_echoes_credentials(tmp_path):
    root = reader_root(tmp_path, '<a href="https://user:SECRET@example.com/a?token=PRIVATE">x</a>')
    report = check_reader_docs(root)
    assert report["remote_http"] == "not_run"
    assert report["overall"] == "not_fully_verified"
    assert "SECRET" not in json.dumps(report) and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://user:password@example.com/",
    ],
)
def test_explicit_remote_rejects_unsafe_destinations(url, tmp_path):
    root = reader_root(tmp_path, f'<a href="{url}">x</a>')
    report = check_reader_docs(root, check_remote=True)
    assert report["links"][0]["status"] == "failed"
    assert report["overall"] != "passed"


def test_cli_local_failure_has_nonzero_exit_and_machine_report(tmp_path):
    root = reader_root(tmp_path, "[missing](absent.md)")
    script = Path(__file__).resolve().parents[2] / "script/check_reader_docs.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["local_links"] == "failed"


def test_remote_redirect_to_private_address_never_connects(monkeypatch):
    from script import check_reader_docs as checker

    connections = []
    monkeypatch.setattr(
        checker.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1" if host == "private.invalid" else "8.8.8.8", 80))
        ],
    )

    class ExternalHTTPDouble:
        def __init__(self, host, port, timeout):
            connections.append(host)

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return self

        status = 302

        def getheader(self, name):
            return "http://private.invalid/"

        def close(self):
            pass

    monkeypatch.setattr(checker.http.client, "HTTPConnection", ExternalHTTPDouble)
    report = checker.remote_probe("http://public.invalid/")
    assert report == {"status": "failed", "reason": "nonpublic_remote_address"}
    assert connections == ["public.invalid"]


def test_remote_timeout_is_bounded_at_external_process_seam(monkeypatch):
    from script import check_reader_docs as checker

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 15
        assert "--remote-probe" in command
        raise subprocess.TimeoutExpired(command, 15)

    monkeypatch.setattr(checker.subprocess, "run", timeout)
    assert checker.check_remote_url("https://example.com/")["status"] == "failed"


def test_html_id_does_not_invent_a_second_heading_slug(tmp_path):
    root = reader_root(tmp_path, '<a id="same"></a>\n\n# Same\n\n[x](#same-1)')
    assert check_reader_docs(root)["links"][0]["reason"] == "missing_fragment"
