"""Fixed child execution observations; no asset qualification authority."""

from typing import Literal

from pydantic import Field, model_validator

from .base import HarnessModel, ShortString, public_schema_config
from .common import ArtifactRef
from .genesis_runtime_manifest import RuntimeManifestMemberV1, RuntimeProcessIdentityV1

GENESIS_RUNTIME_EXECUTION_REQUEST_SCHEMA_ID = "harness.genesis_runtime_execution_request.v1"
GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID = "harness.genesis_runtime_observation.v1"
GENESIS_RUNTIME_EXECUTION_RECEIPT_SCHEMA_ID = "harness.genesis_runtime_execution_receipt.v1"
RUNNER_PATH = "self_improving/harness/genesis_runtime_launcher.py"
FILESYSTEM_POLICY = "runtime_roots_readonly_two_verified_gl_files_kernel_devices_run_output_write"


def _reference(ref, schema):
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.schema_version != schema
        or ref.media_type != "application/json"
    ):
        raise ValueError("reference must be canonical CAS with the exact JSON schema")


class GenesisRuntimeExecutionRequestV1(HarnessModel):
    model_config = public_schema_config(GENESIS_RUNTIME_EXECUTION_REQUEST_SCHEMA_ID)
    schema_version: Literal[GENESIS_RUNTIME_EXECUTION_REQUEST_SCHEMA_ID]
    manifest: ArtifactRef
    representation: ArtifactRef
    runtime_lock: ArtifactRef
    runner: RuntimeManifestMemberV1

    @model_validator(mode="after")
    def exact_inputs(self):
        for value, schema in (
            (self.manifest, "harness.genesis_runtime_manifest.v1"),
            (self.representation, "harness.asset_representation.v1"),
            (self.runtime_lock, "harness.genesis_runtime_lock.v1"),
        ):
            _reference(value, schema)
        if (self.runner.root, self.runner.path) != ("backend", RUNNER_PATH):
            raise ValueError("runner must be the fixed Harness launcher member")
        return self


class GenesisRuntimeKernelFactsV1(HarnessModel):
    """Observed kernel facts, distinct from manifest file membership."""

    cpu_name: ShortString
    memory_bytes: int = Field(strict=True, gt=0)
    kernel_release: ShortString
    kernel_machine: Literal["x86_64"]
    process_maps_preopened: Literal[True]


class GenesisRuntimeObservationV1(HarnessModel):
    model_config = public_schema_config(GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID)
    schema_version: Literal[GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID]
    request: ArtifactRef
    process_identity: RuntimeProcessIdentityV1
    torch_threads: tuple[Literal[1], Literal[1]]
    modules: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    native_files: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    external_native_files: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=2, max_length=2)
    landlock_abi: int = Field(strict=True, ge=1)
    filesystem_policy: Literal[FILESYSTEM_POLICY]
    kernel_devices: tuple[Literal["/dev/null"], Literal["/dev/urandom"]]
    network_policy: Literal["socket_syscalls_denied"]
    kernel_facts: GenesisRuntimeKernelFactsV1
    generated_namespaces: tuple[Literal["torch.classes"], Literal["torch.ops"]]
    frozen_modules: tuple[ShortString, ...]
    builtin_modules: tuple[ShortString, ...]
    other_non_file_modules: tuple[ShortString, ...]
    probe_input: ArtifactRef
    load_observation: ArtifactRef
    load_evidence: ArtifactRef
    trace_rows: ArtifactRef
    contact_trace: ArtifactRef
    probe_report: ArtifactRef
    stdout: ArtifactRef
    stderr: ArtifactRef

    @model_validator(mode="after")
    def module_classifications_are_canonical(self):
        for names in (self.frozen_modules, self.builtin_modules, self.other_non_file_modules):
            if names != tuple(sorted(set(names))):
                raise ValueError("module classification names must be sorted and unique")
        all_names = self.frozen_modules + self.builtin_modules + self.other_non_file_modules
        if len(all_names) != len(set(all_names)):
            raise ValueError("module classifications must be disjoint")
        return self


class GenesisRuntimeExecutionReceiptV1(HarnessModel):
    model_config = public_schema_config(GENESIS_RUNTIME_EXECUTION_RECEIPT_SCHEMA_ID)
    schema_version: Literal[GENESIS_RUNTIME_EXECUTION_RECEIPT_SCHEMA_ID]
    authority: Literal["observed_execution_only"]
    request: ArtifactRef
    observation: ArtifactRef
    probe_report: ArtifactRef
