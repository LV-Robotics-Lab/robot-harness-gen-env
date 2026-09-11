"""Existing control snapshot shape for the explicit native layout revision v3."""

from typing import Literal

from pydantic import model_validator

from .base import public_schema_config
from .golden_control import ControlCommitSnapshot
from .golden_control_v2 import _control, _ref


class ControlCommitSnapshotV3(ControlCommitSnapshot):
    model_config = public_schema_config("harness.control_commit_snapshot.v3")
    schema_version: Literal["harness.control_commit_snapshot.v3"]
    control_kind: Literal["prompt_revision"]

    @model_validator(mode="after")
    def snapshot_is_bound(self):
        _control(self, "harness.prompt_revision_command.v3")
        _ref(self.receipt, "harness.prompt_revision.v3")
        if self.payload != self.receipt or self.receipt_head != self.receipt:
            raise ValueError("snapshot must point to one identical control receipt")
        return self
