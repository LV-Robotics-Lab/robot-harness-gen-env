"""Receipt facts for Harness-managed experimental stages, without freshness claims."""

import re
from dataclasses import dataclass

from .schemas.common import RunState, RunStatus
from .schemas.experiment import ExperimentSkillInput, ExperimentSkillOutput
from .schemas.workflow_operation import ReceiptDerivedFactClaimV2

_KEYS = {
    "interpret": "experiment.intent",
    "acquire": "experiment.asset",
    "compile": "environment.scene",
    "replay": "environment.replay",
    "observe": "environment.observation",
    "visual": "experiment.visual",
    "validate": "environment.validation",
    "revise_scene": "experiment.intent",
    "revise_asset": "experiment.asset",
}


def _ordered(refs):
    return tuple(sorted(set(refs), key=lambda r: (r.sha256, r.schema_version or "", r.media_type)))


@dataclass(frozen=True)
class ExperimentSkillEvidencePolicy:
    skill_ref: str
    stage: str
    store: object
    implementation_sha256: str

    def __post_init__(self):
        if (
            self.stage not in _KEYS
            or re.fullmatch(r"[0-9a-f]{64}", self.implementation_sha256) is None
        ):
            raise ValueError("invalid experiment policy configuration")

    @property
    def input_model(self):
        return ExperimentSkillInput

    @property
    def evidence_policy_id(self):
        return f"experiment.{self.stage}.evidence"

    evidence_policy_version = "1.0.0"

    @property
    def evidence_policy_implementation_sha256(self):
        return self.implementation_sha256

    def normalize_parameters(self, parameters):
        return ExperimentSkillInput.model_validate(parameters)

    def _resolve(self, ref):
        if ref.uri != f"artifact://sha256/{ref.sha256}":
            raise ValueError("experimental evidence requires CAS identity")
        return self.store.resolve(ref)

    def authorize(self, state, parameters):
        if type(parameters) is not ExperimentSkillInput:
            raise TypeError("experiment policy requires ExperimentSkillInput")
        facts = {f.key: f.value for f in state.facts}
        if facts.get("request.experiment_input") != parameters.input_ref.model_dump(mode="json"):
            raise ValueError("input differs from trusted experiment input")
        self._resolve(parameters.input_ref)
        for key, ref in parameters.dependencies.items():
            if key not in facts or facts[key] != ref.model_dump(mode="json"):
                raise ValueError(f"untrusted dependency: {key}")
            self._resolve(ref)

    def _output(self, state):
        if f"{state.skill_id}@{state.skill_version}" != self.skill_ref:
            raise ValueError("terminal child differs from configured Skill")
        if state.status is not RunStatus.SUCCEEDED:
            return None
        output = ExperimentSkillOutput.model_validate(state.output)
        if output.stage != self.stage:
            raise ValueError("typed stage differs from configured Skill")
        if output.receipt not in state.artifacts:
            raise ValueError("stage receipt absent from terminal artifacts")
        self._resolve(output.receipt)
        return output

    def claims(self, state):
        output = self._output(state)
        if output is None or output.status != "passed":
            return ()
        return (
            ReceiptDerivedFactClaimV2(
                key=_KEYS[self.stage], value=output.receipt.model_dump(mode="json")
            ),
        )

    def diagnostics(self, state):
        output = self._output(state)
        refs = []
        if output is not None and output.status != "passed":
            refs.append(output.receipt)
        if state.blocker is not None:
            refs.extend(state.blocker.artifact_refs)
        for ref in refs:
            self._resolve(ref)
        return _ordered(refs)

    def fresh_observations(self, state, invocation):
        return ()

    def verify_result(self, *, state, claims, result, supporting):
        if RunState.model_validate_json(self._resolve(result.run_state).path.read_bytes()) != state:
            raise ValueError("result persisted terminal differs")
        if (
            result.child_run_id != state.run_id
            or result.skill_ref != self.skill_ref
            or result.status != state.status
            or result.typed_output != state.output
            or result.fresh_observations
            or claims != self.claims(state)
            or result.diagnostics != self.diagnostics(state)
            or _ordered(supporting) != _ordered(state.artifacts)
        ):
            raise ValueError("experiment result differs from terminal evidence")
        for ref in supporting:
            self._resolve(ref)
