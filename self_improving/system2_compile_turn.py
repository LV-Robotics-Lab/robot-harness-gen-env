"""Run one exact System 2 planning turn through the qualified compile Skill.

The module owns the assembly between an already-qualified ``CompileApplication``
and a planner provider.  Callers supply semantic intent and an operator-trusted
catalog path; the result is a receipt-bound ToolResult, the resulting immutable
world state, and, after successful mutation, a complete history authority for
the next turn.  Blocked or failed terminal results remain normal return values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from self_improving.harness.application import CompileApplication
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import (
    ArtifactRef,
    CompileConfig,
    RunStatus,
    Text2EnvCompileInput,
)
from self_improving.harness.system2.context import (
    ContextBudget,
    PlannerHistoryEntry,
    compile_planner_context,
)
from self_improving.harness.system2.dispatcher import (
    CompileSystem2Application,
    System2Dispatcher,
    verify_trusted_tool_receipt,
)
from self_improving.harness.system2.domain import (
    System2ToolResult,
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    apply_state_delta,
    build_world_state,
)
from self_improving.harness.system2.history import publish_planner_history_authority
from self_improving.harness.system2.planner import (
    LocalPlannerArtifactPublisher,
    PlannerExecutionError,
    PlannerProvider,
    System2Planner,
)

_COMPILE_SKILL_REF = "text2env.compile@1.0.0"
_WORLD_FACT_EVIDENCE_SCHEMA = "harness.world_fact_evidence.v1"
_CONTEXT_BUDGET = ContextBudget(
    max_facts=2,
    max_history=0,
    max_context_bytes=262_144,
)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class System2CompileTurnResult:
    """The reusable outcome of one compile-oriented planning turn.

    Successful turns carry the complete history authority needed by a later
    turn.  A non-mutating blocked/failed result retains its trusted receipt but
    cannot advance the current v1 history model, so its authority is explicit
    ``None`` rather than a fabricated lineage.
    """

    world_state: TrustedWorldState
    history_authority: ArtifactRef | None
    tool_result: System2ToolResult


class System2CompileTurnError(RuntimeError):
    """The turn could not return a reusable trusted outcome.

    The optional evidence fields preserve the deepest boundary completed before
    a pre-terminal or integrity error without exposing lower-module exception
    taxonomies to callers.  Terminal blocked/failed ToolResults are not errors.
    """

    def __init__(
        self,
        *,
        reason: str,
        planner_receipt: ArtifactRef | None = None,
        tool_result: System2ToolResult | None = None,
    ) -> None:
        self.reason = reason
        self.planner_receipt = planner_receipt
        self.tool_result = tool_result
        super().__init__(f"System 2 compile turn stopped: {reason}")


class System2CompileTurn:
    """Deep module joining trusted input, planning, execution, and state commit."""

    def __init__(
        self,
        *,
        application: CompileApplication,
        provider: PlannerProvider,
    ) -> None:
        adapter = CompileSystem2Application(application)
        artifact_store = LocalArtifactStore(application.artifact_root)
        scratch_root = application.journal_path.parent / "system2-compile-turn"
        publisher = LocalPlannerArtifactPublisher(
            artifact_store=artifact_store,
            scratch_root=scratch_root,
        )
        self._application = application
        self._adapter = adapter
        self._artifact_store = artifact_store
        self._scratch_root = scratch_root
        self._publisher = publisher
        self._planner = System2Planner(provider=provider, publisher=publisher)
        self._dispatcher = System2Dispatcher(
            artifact_store=artifact_store,
            scratch_root=scratch_root,
            applications=(adapter,),
        )

    def execute(
        self,
        *,
        request: str,
        seed: int,
        asset_catalog_path: Path,
        generate_missing_assets: bool = False,
    ) -> System2CompileTurnResult:
        """Execute exactly the compile input exposed to the planner context."""

        phase = "input_setup"
        planner_receipt: ArtifactRef | None = None
        tool_result: System2ToolResult | None = None
        try:
            catalog = self._application.snapshot_asset_catalog(asset_catalog_path)
            compile_input = Text2EnvCompileInput(
                request=request,
                seed=seed,
                asset_catalog=catalog,
                config=CompileConfig(generate_missing_assets=generate_missing_assets),
            )
            observed_at = datetime.now(timezone.utc)
            objective = self._user_fact(
                key="task.objective",
                value=request,
                observed_at=observed_at,
                artifact_name="task_objective",
            )
            trusted_input = self._user_fact(
                key="inputs.compile",
                value=compile_input.model_dump(mode="json"),
                observed_at=observed_at,
                artifact_name="compile_input",
            )
            base_state = build_world_state((objective, trusted_input), as_of=observed_at)
            context = compile_planner_context(
                state=base_state,
                skills=(self._adapter.planner_skill_card,),
                history=(),
                required_fact_keys=("inputs.compile",),
                budget=_CONTEXT_BUDGET,
            )
            phase = "planning"
            try:
                planning = self._planner.plan(context)
            except PlannerExecutionError as error:
                planner_receipt = error.receipt_ref
                raise
            planner_receipt = planning.receipt_ref
            if (
                planning.decision.action != "invoke_skill"
                or planning.decision.skill_ref != _COMPILE_SKILL_REF
            ):
                raise System2CompileTurnError(
                    reason="planner_action_not_compile",
                    planner_receipt=planner_receipt,
                )
            if planning.decision.parameters != compile_input.model_dump(mode="json"):
                raise System2CompileTurnError(
                    reason="planner_input_mismatch",
                    planner_receipt=planner_receipt,
                )
            phase = "dispatch"
            tool_result = self._dispatcher.dispatch(
                planning=planning,
                context=context,
                state=base_state,
            )
            phase = "result_integrity"
            world_state = (
                apply_state_delta(
                    base_state,
                    tool_result.state_delta,
                    artifact_store=self._artifact_store,
                )
                if tool_result.status is RunStatus.SUCCEEDED and tool_result.state_delta is not None
                else base_state
            )
            verified = verify_trusted_tool_receipt(
                self._artifact_store,
                tool_result.trusted_receipt,
            )
            blocker = verified.receipt.blocker
            history_authority: ArtifactRef | None = None
            if world_state.version == 2:
                history_entry = PlannerHistoryEntry(
                    run_id=verified.run_state.run_id,
                    skill_ref=verified.receipt.skill.skill_ref,
                    status=verified.receipt.status,
                    ended_at=verified.receipt.ended_at,
                    receipt=tool_result.trusted_receipt,
                    blocker_code=blocker.code if blocker is not None else None,
                    blocker_stage=blocker.stage if blocker is not None else None,
                )
                history_authority = publish_planner_history_authority(
                    artifact_store=self._artifact_store,
                    scratch_root=self._scratch_root,
                    lineage=(base_state, world_state),
                    entries=(history_entry,),
                )
            return System2CompileTurnResult(
                world_state=world_state,
                history_authority=history_authority,
                tool_result=tool_result,
            )
        except System2CompileTurnError:
            raise
        except Exception as error:
            raise System2CompileTurnError(
                reason=f"{phase}_failed",
                planner_receipt=planner_receipt,
                tool_result=tool_result,
            ) from error

    def _user_fact(
        self,
        *,
        key: str,
        value: object,
        observed_at: datetime,
        artifact_name: str,
    ) -> WorldFact:
        evidence = WorldFactEvidence(
            source_kind="user_input",
            key=key,
            value=value,
            observed_at=observed_at,
            valid_until=None,
        )
        evidence_ref = self._publisher.publish(
            _canonical_json_bytes(evidence.model_dump(mode="json")),
            name=artifact_name,
            media_type="application/json",
            schema_version=_WORLD_FACT_EVIDENCE_SCHEMA,
        )
        return WorldFact(
            key=key,
            value=value,
            source_kind="user_input",
            source_artifact=evidence_ref,
            observed_at=observed_at,
            valid_until=None,
        )


__all__ = [
    "System2CompileTurn",
    "System2CompileTurnError",
    "System2CompileTurnResult",
]
