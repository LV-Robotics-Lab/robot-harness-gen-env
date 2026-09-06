#!/usr/bin/env python3
"""Run one offline advisory Qwen assessment against a persisted replay run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.qwen_replay_vlm_provider import (
    QwenReplayVlmProviderSettings,
    SubprocessQwenReplayVlmProvider,
)
from self_improving.harness.replay_vlm_assessment import ReplayVlmAssessmentApplication
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas import ReplayVlmAssessmentInput

_REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--replay-run-id", type=UUID, required=True)
    parser.add_argument("--interpreter", type=Path, required=True)
    parser.add_argument("--module-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--model-id", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-manifest-sha256", required=True)
    parser.add_argument("--model-content-manifest", type=Path, required=True)
    parser.add_argument("--model-content-manifest-sha256", required=True)
    parser.add_argument("--model-roster-sha256", required=True)
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    database = args.database.expanduser().resolve()
    artifact_store = LocalArtifactStore(args.artifact_root)
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    provider = SubprocessQwenReplayVlmProvider(
        QwenReplayVlmProviderSettings(
            interpreter=args.interpreter.expanduser().resolve(),
            module_root=args.module_root.expanduser().resolve(),
            work_root=args.work_root.expanduser().resolve(),
            model_id=args.model_id,
            revision=args.model_revision,
            local_snapshot_path=args.model_snapshot.expanduser().resolve(),
            snapshot_manifest_sha256=args.snapshot_manifest_sha256,
            model_content_manifest_path=args.model_content_manifest.expanduser().resolve(),
            model_content_manifest_sha256=args.model_content_manifest_sha256,
            model_roster_sha256=args.model_roster_sha256,
            device_index=args.device_index,
            timeout_seconds=args.timeout_seconds,
        )
    )
    application = ReplayVlmAssessmentApplication(
        artifact_store=artifact_store,
        event_journal=journal,
        run_store=run_store,
        provider=provider,
    )
    state = application.assess(ReplayVlmAssessmentInput(replay_run_id=str(args.replay_run_id)))
    rendered = (
        json.dumps(
            state.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.output is None:
        print(rendered, end="")
    else:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0 if state.status.value == "succeeded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
