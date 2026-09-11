"""Project the existing camera qualification with limited workflow ancestry.

This narrow adapter reuses native projection document and child-intent checks.
It does not add a generic binding allowlist entry, requalify the camera, or claim
terminal deep admission. The policy still checks the typed camera result and its
child SQLite evidence. Only the ancestry policy and adapter source identity differ.
"""

from dataclasses import dataclass
from pathlib import Path

from . import native_skill_projection as native
from .artifacts import LocalArtifactStore
from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .golden_execution import (
    _create_golden_execution_binding,
    _create_golden_execution_evidence_hook,
)
from .limited_image_preview_workflow_policies import create_limited_observation_binding
from .run_store import SQLiteRunStore
from .schemas.common import ArtifactRef
from .typed_qualification_admission import (
    VerifiedTypedQualificationAdmission,
    verify_typed_qualification_admission,
)

_TOKEN = object()
_SOURCES = (
    "limited_observation_registry_projection.py",
    "limited_image_preview_workflow_policies.py",
    "limited_image_preview_policy.py",
    "genesis_fresh_capture_application.py",
)


class LimitedObservationProjectionError(ValueError):
    """A limited projection no longer matches its original qualification or code."""


@dataclass(frozen=True)
class LimitedObservationRegistryProjection:
    """Verifier-issued adapter documents and sealed Golden binding."""

    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef
    implementation_sources: tuple[ArtifactRef, ...]
    registry_entry: object
    binding: object
    _token: object

    def __post_init__(self):
        if self._token is not _TOKEN:
            raise TypeError("limited projections require their verifier")


def _require(value, message):
    if not value:
        raise LimitedObservationProjectionError(message)


def _sources(store, *, publish):
    refs = list(native._current_implementation_sources())
    if publish:
        native._publish_implementation_sources(store)
    for logical in _SOURCES:
        path = Path(__file__).parent / logical
        ref = native._payload_ref(
            path.read_bytes(), name=logical, media_type="text/x-python", schema_version=None
        )
        if publish:
            _require(
                store.put_file(path, name=logical, media_type="text/x-python", schema_version=None)
                == ref,
                "projection source changed during publication",
            )
        refs.append(ref)
    for ref in refs:
        store.resolve(ref)
    return tuple(refs)


@dataclass(frozen=True)
class _Authority:
    artifact_store: LocalArtifactStore
    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef
    native_binding: object
    terminal_resolver: object = None

    def verify(self):
        store = self.artifact_store
        admission = verify_typed_qualification_admission(
            artifact_store=store, admission=native._read_native_admission(store, self.admission_ref)
        )
        _require(admission.admission_ref == self.admission_ref, "admission reference drift")
        _require(
            admission.admission.skill_ref == "genesis.observe@3.0.0",
            "limited projection requires the exact camera qualification",
        )
        descriptor = native._native_descriptor(admission, store, self.native_binding.application)
        expected = native._build_documents(
            admission, descriptor, _sources(store, publish=False), self.native_binding.policy, None
        )
        for actual, ref, model in (
            (self.descriptor_ref, expected.descriptor_ref, expected.adapter_descriptor),
            (self.qualification_ref, expected.qualification_ref, expected.adapter_qualification),
            (self.qualification_report_ref, expected.report_ref, expected.adapter_report),
        ):
            _require(actual == ref, "limited projection document reference drift")
            _require(
                native._read_canonical_model(store, actual, type(model)) == model,
                "limited projection document drift",
            )
        return expected


def _binding(application, replay_authority):
    if type(application) is not GenesisFreshCaptureApplication:
        raise TypeError("limited projection requires the exact registered capture application")
    if type(replay_authority) is not SQLiteRunStore:
        raise TypeError("limited projection requires the exact replay SQLite authority")
    if application.replay_application.run_store is not replay_authority:
        raise TypeError("limited projection replay authority differs from its camera application")
    if type(application.producer.artifact_store) is not LocalArtifactStore:
        raise TypeError("limited projection requires the exact camera CAS")
    return create_limited_observation_binding(application, replay_authority=replay_authority)


def issue_limited_observation_registry_projection(*, application, admission, replay_authority):
    """Seal a new policy identity around the original, verified camera admission."""
    binding = _binding(application, replay_authority)
    if (
        type(admission) is not VerifiedTypedQualificationAdmission
        or not admission._is_verifier_issued()
    ):
        raise TypeError("limited projection requires a verified typed admission")
    store = application.producer.artifact_store
    verified = verify_typed_qualification_admission(
        artifact_store=store, admission=admission.admission
    )
    _require(verified.admission_ref == admission.admission_ref, "admission reference drift")
    _require(
        verified.admission.skill_ref == "genesis.observe@3.0.0",
        "limited projection requires the exact camera qualification",
    )
    descriptor = native._native_descriptor(verified, store, binding.application)
    documents = native._build_documents(
        verified, descriptor, _sources(store, publish=True), binding.policy, None
    )
    for model, ref in (
        (documents.adapter_report, documents.report_ref),
        (documents.adapter_qualification, documents.qualification_ref),
        (documents.adapter_descriptor, documents.descriptor_ref),
    ):
        _require(
            native._publish_model(store, model, name=ref.name, schema_version=ref.schema_version)
            == ref,
            "projection publication identity drift",
        )
    return load_limited_observation_registry_projection(
        application=application,
        replay_authority=replay_authority,
        admission_ref=verified.admission_ref,
        descriptor_ref=documents.descriptor_ref,
        qualification_ref=documents.qualification_ref,
        qualification_report_ref=documents.report_ref,
    )


def load_limited_observation_registry_projection(
    *,
    application,
    replay_authority,
    admission_ref,
    descriptor_ref,
    qualification_ref,
    qualification_report_ref,
):
    """Recompute adapter identity from CAS and the same exact restored applications."""
    source = _binding(application, replay_authority)
    authority = _Authority(
        application.producer.artifact_store,
        admission_ref,
        descriptor_ref,
        qualification_ref,
        qualification_report_ref,
        source,
    )
    verified = authority.verify()
    projected = native._NativeProjectedApplication(
        authority,
        source.application,
        source.child_authority,
        verified.adapter_descriptor,
        source.policy,
    )
    binding = _create_golden_execution_binding(
        policy=source.policy,
        application=projected,
        child_authority=source.child_authority,
        evidence_hook=_create_golden_execution_evidence_hook(
            native._NativeProjectionEvidenceHook(authority)
        ),
    )
    return LimitedObservationRegistryProjection(
        admission_ref,
        descriptor_ref,
        qualification_ref,
        qualification_report_ref,
        verified.implementation_sources,
        verified.registry_entry,
        binding,
        _TOKEN,
    )
