from pathlib import Path
import re


PUBLIC_EVIDENCE_ROOTS = (
    Path("apps/pearl_evidence_portal/public/reports"),
    Path("self_improving/validation_evidence"),
)
LOCAL_HOME_PATTERN = re.compile(r"/(?:home|Users)/[^/\s]+")


def test_public_evidence_logs_do_not_expose_local_home_paths():
    offenders = []
    for root in PUBLIC_EVIDENCE_ROOTS:
        for path in root.rglob("*.log"):
            if LOCAL_HOME_PATTERN.search(path.read_text(errors="replace")):
                offenders.append(str(path))
    assert not offenders, f"local home paths found in public evidence logs: {offenders}"
