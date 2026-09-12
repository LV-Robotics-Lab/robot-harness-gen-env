"""Harness-managed advisory model, without workflow, provider or physical authority.

Transport adapted from codex_experiment_proposal.py@aaf90244fd6edea29dab1cfa35ef11f1968d50ab;
recorded Git author Bingsheng Xie <xieziyin_shangshu@outlook.com>. No identity inference.
"""

import hashlib
import json
import os
import signal
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image
from pydantic import ValidationError

from .contracts import ArtifactRef, BackendProposal, InputBundle, SceneIntentProposal
from .schema_export import structured_output_schema


class ArtifactStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


class CodexBackend:
    def __init__(
        self,
        executable: Path,
        approved_executable_sha256: str,
        model: str,
        artifact_store: ArtifactStore,
    ):
        self.executable = Path(executable)
        self.executable_sha = approved_executable_sha256
        self.model = model
        self.store = artifact_store

    def interpret(self, bundle: InputBundle, *, output_root: Path, timeout: int = 600):
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("deadline must be an integer within 1..600 seconds")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("attempt root must be an absolute non-symbolic new directory")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        evidence = []
        status, failure, proposal, executed = "failed", None, None, False

        def record(name, data, media_type="application/json"):
            (root / name).write_bytes(data)
            ref = self.store.write_artifact(data, media_type)
            evidence.append(ref)
            return ref

        try:
            if (
                not self.executable.is_absolute()
                or hashlib.sha256(self.executable.read_bytes()).hexdigest() != self.executable_sha
            ):
                raise ValueError("executable_identity_mismatch")
            record("bundle.json", bundle.model_dump_json().encode())
            text = self.store.read_artifact(bundle.text).decode() if bundle.text else ""
            images = []
            for index, image in enumerate(bundle.images):
                record(f"image-{index}.png", self.store.read_artifact(image.canonical), "image/png")
                images.append(
                    {
                        "path": str(root / f"image-{index}.png"),
                        "input_sha256": image.source.sha256,
                        "media_index": index,
                        "canonical_sha256": image.canonical.sha256,
                        "source": "image",
                    }
                )
            if bundle.video:
                video = bundle.video
                sequence = json.loads(self.store.read_artifact(video.sequence))
                frames = sequence["frames"]
                if (
                    not sequence.get("full_decode")
                    or sequence["source"] != video.source.model_dump()
                    or len(frames) != video.frame_count
                ):
                    raise ValueError("video_evidence_mismatch")
                count = min(8, video.frame_count)
                indices = (
                    [round(i * (video.frame_count - 1) / (count - 1)) for i in range(count)]
                    if count > 1
                    else [0]
                )
                source_path = root / "source-video.bin"
                source_path.write_bytes(self.store.read_artifact(video.source))
                selector = "+".join(f"eq(n\\,{index})" for index in indices)
                decoded = subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-xerror",
                        "-nostdin",
                        "-i",
                        str(source_path),
                        "-map",
                        "0:v:0",
                        "-an",
                        "-vf",
                        f"select={selector}",
                        "-fps_mode",
                        "passthrough",
                        "-pix_fmt",
                        "rgb24",
                        "-frames:v",
                        str(count),
                        "-f",
                        "rawvideo",
                        "-",
                    ],
                    capture_output=True,
                    timeout=min(60, max(0.01, timeout - (time.monotonic() - started))),
                )
                record("video-decoder.stderr", decoded.stderr, "text/plain")
                frame_size = video.width * video.height * 3
                if decoded.returncode or len(decoded.stdout) != frame_size * count:
                    raise ValueError("video_evidence_mismatch")
                for ordinal, index in enumerate(indices):
                    frame = frames[index]
                    rgb = decoded.stdout[ordinal * frame_size : (ordinal + 1) * frame_size]
                    digest = hashlib.sha256(rgb).hexdigest()
                    if (
                        frame["index"] != index
                        or frame["sha256"] != digest
                        or frame["size_bytes"] != len(rgb)
                    ):
                        raise ValueError("video_evidence_mismatch")
                    output = BytesIO()
                    Image.frombytes("RGB", (video.width, video.height), rgb).save(
                        output, format="PNG"
                    )
                    ref = record(f"video-{index}.png", output.getvalue(), "image/png")
                    images.append(
                        {
                            "path": str(root / f"video-{index}.png"),
                            "source": "video",
                            "input_sha256": video.source.sha256,
                            "frame_index": index,
                            "pts": frame["pts"],
                            "rgb_sha256": digest,
                            "canonical_sha256": ref.sha256,
                        }
                    )
            prompt = (
                "You are the Harness internal advisory backend. Do not use tools. Return only "
                "SceneIntentProposal JSON matching the supplied schema. Never claim provider, "
                "Skill, simulation, asset acquisition or validation success. Preserve every "
                "requested entity, attribute, articulation state and spatial relation; "
                "do not collapse multiple objects into one or omit requested properties. Use "
                "field-level provenance bound to the supplied source hashes. SceneIR.input_sha256 "
                "must equal bundle.request_sha256 and revision must be 0. Preserve uncertainty "
                "as explicit unknowns; critical conflicts require clarification. Explicit text "
                "overrides media only with override provenance explaining the change. Do not "
                "invent dimensions or silently resolve conflicting media. Coordinates use metres.\n"
                + json.dumps(
                    {
                        "bundle": bundle.model_dump(mode="json"),
                        "text": text,
                        "attached_media": images,
                    },
                    ensure_ascii=False,
                )
            )
            record("prompt.txt", prompt.encode(), "text/plain")
            record(
                "proposal.schema.json",
                json.dumps(structured_output_schema(SceneIntentProposal)).encode(),
            )
            argv = [
                str(self.executable),
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                self.model,
                "--output-schema",
                str(root / "proposal.schema.json"),
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.multi_agent=false",
                "-c",
                "mcp_servers={}",
                "--output-last-message",
                str(root / "proposal.json"),
            ]
            for image in images:
                argv.extend(["-i", image["path"]])
            argv.append("-")
            record("invocation.json", json.dumps({"argv": argv, "media": images}).encode())
            with (
                (root / "codex.jsonl").open("wb") as stdout,
                (root / "codex.stderr").open("wb") as stderr,
            ):
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=root,
                    start_new_session=True,
                )
                executed = True
                process_stat = Path(f"/proc/{process.pid}/stat").read_text()
                record(
                    "process.json",
                    json.dumps(
                        {
                            "pid": process.pid,
                            "pgid": process.pid,
                            "start_ticks": int(process_stat.rsplit(")", 1)[1].split()[19]),
                            "attempt_root": str(root),
                            "executable_sha256": self.executable_sha,
                        }
                    ).encode(),
                )
                try:
                    process.communicate(
                        prompt.encode(), timeout=max(0.01, timeout - (time.monotonic() - started))
                    )
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    failure = (
                        "model_timeout"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "model_interrupted"
                    )
                    os.killpg(process.pid, signal.SIGINT)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=5)
            for name in ("codex.jsonl", "codex.stderr"):
                record(name, (root / name).read_bytes(), "text/plain")
            proposal_path = root / "proposal.json"
            if proposal_path.is_file():
                record("proposal.json", proposal_path.read_bytes())
            if failure:
                raise ValueError(failure)
            if process.returncode != 0:
                raise ValueError("model_exit_failure")
            events = [
                json.loads(line)
                for line in (root / "codex.jsonl").read_bytes().splitlines()
                if line.strip()
            ]
            if not any(event.get("type") == "turn.completed" for event in events):
                raise ValueError("model_incomplete_turn")
            if any(
                event.get("item", {}).get("type")
                in {
                    "command_execution",
                    "mcp_tool_call",
                    "web_search",
                    "collab_tool_call",
                    "file_change",
                }
                for event in events
            ):
                raise ValueError("advisory_tool_violation")
            raw = (root / "proposal.json").read_bytes()
            proposal = SceneIntentProposal.model_validate_json(raw)
            if proposal.scene and (
                proposal.scene.input_sha256 != bundle.request_sha256 or proposal.scene.revision != 0
            ):
                raise ValueError("proposal_input_mismatch")
            provenance = [p for unknown in proposal.unknowns for p in unknown.provenance]
            if proposal.scene:
                provenance += [
                    p for entity in proposal.scene.entities for p in entity.provenance.records()
                ]
                provenance += [
                    p for relation in proposal.scene.relations for p in relation.provenance
                ]
            for item in provenance:
                if item.source == "text":
                    valid = (
                        bundle.text
                        and item.input_sha256 == bundle.text.sha256
                        and item.frame_index is None
                        and item.media_index is None
                    )
                elif item.source == "image":
                    valid = (
                        item.media_index is not None
                        and item.media_index < len(bundle.images)
                        and item.input_sha256 == bundle.images[item.media_index].source.sha256
                        and item.frame_index is None
                    )
                else:
                    valid = (
                        bundle.video
                        and item.input_sha256 == bundle.video.source.sha256
                        and item.frame_index is not None
                        and item.frame_index
                        in {
                            image.get("frame_index")
                            for image in images
                            if image["source"] == "video"
                        }
                    )
                if not valid:
                    raise ValueError("proposal_input_mismatch")
            status = "completed"
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            record(
                "validation-errors.json",
                json.dumps(
                    {
                        "error_code": "invalid_model_evidence",
                        "errors": [
                            {"loc": item["loc"], "type": item["type"], "msg": item["msg"][:1024]}
                            for item in errors[:64]
                        ],
                        "total_errors": len(errors),
                        "truncated": len(errors) > 64,
                    },
                    sort_keys=True,
                ).encode(),
            )
            failure, proposal = "invalid_model_evidence", None
        except FileNotFoundError:
            status, failure = "blocked", "blocked_external_resource"
            proposal = None
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            allowed = {
                "executable_identity_mismatch",
                "model_timeout",
                "model_interrupted",
                "model_exit_failure",
                "model_incomplete_turn",
                "advisory_tool_violation",
                "proposal_input_mismatch",
                "video_evidence_mismatch",
            }
            failure = str(exc) if str(exc) in allowed else "invalid_model_evidence"
            proposal = None
        elapsed = time.monotonic() - started
        record(
            "result.json",
            json.dumps(
                {
                    "status": status,
                    "error_code": failure,
                    "model": self.model,
                    "executable_sha256": self.executable_sha,
                    "input_sha256": bundle.request_sha256,
                    "external_agent_executed": executed,
                    "authority": "advisory_only",
                    "elapsed_seconds": elapsed,
                    "evidence": [r.model_dump() for r in evidence],
                },
                sort_keys=True,
            ).encode(),
        )
        return BackendProposal(
            status=status,
            proposal=proposal,
            evidence=tuple(evidence),
            error_code=failure,
            elapsed_seconds=elapsed,
        )
