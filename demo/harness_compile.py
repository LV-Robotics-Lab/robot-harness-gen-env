"""Synchronous Workbench submission over one fixed compile authority."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from demo.harness_feed import HarnessEventFeed, HarnessEventFeedCorruptionError
from self_improving.harness.application import CompileApplication
from self_improving.harness.event_journal import EventPage
from self_improving.harness.schemas import RunState, RunStatus

_SUBMISSION_SCHEMA = "harness.workbench_compile_submission.v1"
_COMPILE_SKILL_ID = "text2env.compile"
_COMPILE_SKILL_VERSION = "1.0.0"


class WorkbenchCompileInputError(ValueError):
    """The browser-facing business input is outside the fixed compile contract."""


class WorkbenchCompileUnavailableError(RuntimeError):
    """The fixed compile authority cannot accept work at present."""


class WorkbenchCompileAuthorityError(WorkbenchCompileUnavailableError):
    """Persisted RunState and journal history cannot prove one terminal result."""


class WorkbenchCompile:
    """Hide compile trust policy behind a two-field synchronous submission seam.

    A successful return means the terminal ``RunState`` and its complete event
    history were both re-read from the application's shared SQLite authority.
    It does not claim physical validation or publishability.
    """

    def __init__(
        self,
        *,
        application: CompileApplication,
        asset_catalog_path: Path,
        generate_missing_assets: bool,
    ) -> None:
        if type(application) is not CompileApplication:
            raise TypeError("application must be the exact CompileApplication")
        if not isinstance(asset_catalog_path, Path):
            raise TypeError("asset_catalog_path must be a Path")
        if type(generate_missing_assets) is not bool:
            raise TypeError("generate_missing_assets must be a bool")
        self._application = application
        self._asset_catalog_path = asset_catalog_path.expanduser().resolve(strict=False)
        self._generate_missing_assets = generate_missing_assets
        self._feed = HarnessEventFeed(application.events)
        self._submit_lock = threading.Lock()

    def page(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Project events from the same authority that accepts submissions."""

        if type(after_event_id) is not int or not 0 <= after_event_id <= 2**63 - 1:
            raise ValueError("after_event_id must fit a nonnegative SQLite integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        if run_id is not None and not isinstance(run_id, UUID):
            raise ValueError("run_id must be a UUID")
        try:
            return self._feed.page(
                after_event_id=after_event_id,
                run_id=run_id,
                limit=limit,
            )
        except HarnessEventFeedCorruptionError:
            raise
        except Exception as error:
            raise HarnessEventFeedCorruptionError(
                "Harness event history failed integrity checks"
            ) from error

    def submit(self, *, request: object, seed: object) -> dict[str, Any]:
        """Run one real compile and return its verified durable terminal summary."""

        _validate_business_input(request=request, seed=seed)
        request_value = cast(str, request)
        seed_value = cast(int, seed)
        with self._submit_lock:
            try:
                returned = self._application.compile(
                    request=request_value,
                    seed=seed_value,
                    asset_catalog_path=self._asset_catalog_path,
                    generate_missing_assets=self._generate_missing_assets,
                )
            except Exception as error:
                raise WorkbenchCompileUnavailableError("Harness compile is unavailable") from error

            try:
                persisted = self._application.run_state(returned.run_id)
                history = self._application.events(
                    after_event_id=0,
                    run_id=returned.run_id,
                    limit=500,
                )
            except Exception as error:
                raise WorkbenchCompileAuthorityError(
                    "Harness compile authority failed integrity checks"
                ) from error

            _verify_terminal_closure(returned=returned, persisted=persisted, history=history)
            blocker = (
                {
                    "code": persisted.blocker.code,
                    "retryable": persisted.blocker.retryable,
                }
                if persisted.blocker is not None
                else None
            )
            return {
                "schema_version": _SUBMISSION_SCHEMA,
                "run_id": str(persisted.run_id),
                "skill_id": persisted.skill_id,
                "skill_version": persisted.skill_version,
                "status": persisted.status.value,
                "attempt": persisted.attempt,
                "max_attempts": persisted.max_attempts,
                "terminal_event_id": str(history.events[-1].event_id),
                "blocker": blocker,
            }


def _validate_business_input(*, request: object, seed: object) -> None:
    if type(request) is not str or not 3 <= len(request) <= 2000:
        raise WorkbenchCompileInputError("request must contain 3-2000 characters")
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise WorkbenchCompileInputError("seed must be a nonnegative 32-bit integer")


def _verify_terminal_closure(
    *,
    returned: RunState,
    persisted: RunState | None,
    history: EventPage,
) -> None:
    if persisted is None:
        raise WorkbenchCompileAuthorityError("terminal RunState is missing")
    if _canonical_model_bytes(returned) != _canonical_model_bytes(persisted):
        raise WorkbenchCompileAuthorityError("returned and persisted RunState differ")
    if (
        persisted.skill_id != _COMPILE_SKILL_ID
        or persisted.skill_version != _COMPILE_SKILL_VERSION
        or persisted.status is RunStatus.RUNNING
    ):
        raise WorkbenchCompileAuthorityError("terminal RunState identity is invalid")
    if (
        history.has_more
        or len(history.events) != len(persisted.events)
        or not history.events
        or history.last_event_id != history.events[-1].event_id
    ):
        raise WorkbenchCompileAuthorityError("terminal event history is incomplete")
    for stored, expected in zip(history.events, persisted.events, strict=True):
        envelope = stored.envelope
        if (
            envelope.run_id != persisted.run_id
            or envelope.skill_id != persisted.skill_id
            or envelope.skill_version != persisted.skill_version
            or _canonical_model_bytes(envelope.event) != _canonical_model_bytes(expected)
        ):
            raise WorkbenchCompileAuthorityError("terminal event history differs from RunState")


def _canonical_model_bytes(model: Any) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
