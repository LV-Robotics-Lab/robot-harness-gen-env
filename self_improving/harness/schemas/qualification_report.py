"""Public schema projection for the established qualification report record."""

from __future__ import annotations

from ..qualification import QualificationReportV1
from .base import public_schema_config

QUALIFICATION_REPORT_SCHEMA_ID = "harness.skill_qualification_report.v1"


class PublicQualificationReportV1(QualificationReportV1):
    """Schema-catalog projection that preserves the qualified record contract."""

    model_config = public_schema_config(QUALIFICATION_REPORT_SCHEMA_ID)


__all__ = [
    "QUALIFICATION_REPORT_SCHEMA_ID",
    "PublicQualificationReportV1",
]
