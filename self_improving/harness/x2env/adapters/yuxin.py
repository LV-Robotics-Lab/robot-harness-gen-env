"""Canonical license-gated adapter of the original Yuxin provider engine.

Uses the single migrated asset_reuse implementation; no search/download engine fork.
OpenXSim must be supplied as a deployment dependency, never injected into sys.path here.
"""

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Protocol

from ..contracts import ArtifactRef, Model


class ArtifactStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


class LicenseEvidence(Model):
    status: Literal["declared", "unknown"] = "unknown"
    spdx: str | None = None
    attribution: str | None = None
    evidence: ArtifactRef | None = None
    source_url: str | None = None


class ProviderCandidate(Model):
    source: Literal["local", "web"]
    entity_id: str
    candidate_id: str
    category: str
    provider: str
    format: str
    download_url: str
    source_page: str
    score: float
    license: LicenseEvidence
    engine_record: ArtifactRef
    catalog_ref: ArtifactRef | None = None


class ProviderSearchResult(Model):
    status: Literal["succeeded", "failed", "blocked"]
    candidates: tuple[ProviderCandidate, ...] = ()
    receipt: ArtifactRef
    error_code: str | None = None


class ProviderFetchResult(Model):
    status: Literal["succeeded", "failed", "blocked"]
    source_path: str | None = None
    staging_manifest: str | None = None
    receipt: ArtifactRef
    error_code: str | None = None


def _permitted(license):
    return (
        license.status == "declared"
        and license.evidence is not None
        and license.spdx in {"CC0-1.0", "CC-BY-4.0"}
        and bool(license.source_url)
        and (license.spdx == "CC0-1.0" or bool(license.attribution))
    )


class YuxinProviderAdapter:
    def __init__(
        self, config: dict, artifact_store: ArtifactStore, license_records: Path | None = None
    ):
        self.config = config
        self.store = artifact_store
        self.license_records = license_records

    def _record(self, value):
        return self.store.write_artifact(
            json.dumps(value, sort_keys=True).encode(), "application/json"
        )

    def _web_license(self, candidate):
        import urllib.request

        from self_improving.asset_pipeline.active.asset_reuse.lib.a3_webfetch import (
            _lookup_model_license,
        )

        captured = []

        def metadata_fetch(url):
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("oversized license metadata")
            captured.append(data)
            return data

        raw = _lookup_model_license(candidate.download_url, fetch=metadata_fetch)
        if raw:
            legal = json.loads(captured[0]).get("legal", [])
            if not legal or any(item.get("spdx") != raw.get("spdx") for item in legal):
                return LicenseEvidence()
            metadata_ref = self.store.write_artifact(captured[0], "application/json")
            return LicenseEvidence(
                status="declared",
                spdx=raw.get("spdx"),
                attribution=raw.get("owner"),
                source_url=raw.get("metadata_url"),
                evidence=self._record({"metadata": raw, "raw": metadata_ref.model_dump()}),
            )
        meta = candidate.metadata or {}
        if meta.get("license_spdx") and meta.get("license_metadata_url"):
            return LicenseEvidence(
                status="declared",
                spdx=meta["license_spdx"],
                attribution=meta.get("author") or meta.get("sketchfab_name"),
                source_url=meta["license_metadata_url"],
                evidence=self._record(meta),
            )
        return LicenseEvidence()

    def _local_license(self, candidate, catalog):
        if self.license_records is None:
            return LicenseEvidence()
        index = json.loads(Path(self.license_records).read_bytes())
        asset_id = candidate.metadata["asset_id"]
        if asset_id not in index:
            return LicenseEvidence()
        raw = Path(index[asset_id]).read_bytes()
        document = json.loads(raw)
        entries = catalog["entries"] if isinstance(catalog, dict) else catalog
        entry = next(e for e in entries if e["asset_id"] == asset_id)
        usable = {m["model_id"]: m for m in entry["models"] if m.get("usable")}
        for model in document["models"]:
            if model["model_id"] not in usable:
                continue
            for representation in model.get("representations", []):
                expected = usable[model["model_id"]].get("urdf_path")
                if (
                    not expected
                    or representation.get("uri") != expected
                    or representation.get("format") != "urdf"
                ):
                    continue
                license = model.get("source", {}).get("license", {})
                evidence = self._record(
                    {
                        "ledger": self.store.write_artifact(raw, "application/json").model_dump(),
                        "asset_root": entry["asset_path"],
                        "representation": representation,
                        "model_id": model["model_id"],
                        "asset_id": asset_id,
                    }
                )
                result = LicenseEvidence(
                    status=license.get("status", "unknown"),
                    spdx=license.get("spdx"),
                    attribution=license.get("attribution"),
                    source_url=model["source"].get("source_url"),
                    evidence=evidence,
                )
                if _permitted(result):
                    self._local_bytes(result)
                    return result
        return LicenseEvidence()

    def _local_bytes(self, license):
        evidence = json.loads(self.store.read_artifact(license.evidence))
        ledger = json.loads(
            self.store.read_artifact(ArtifactRef.model_validate(evidence["ledger"]))
        )
        model = next(m for m in ledger["models"] if m["model_id"] == evidence["model_id"])
        original_license = model["source"]["license"]
        if (
            evidence["representation"] not in model["representations"]
            or license.spdx != original_license.get("spdx")
            or license.attribution != original_license.get("attribution")
            or license.status != original_license.get("status")
            or license.source_url != model["source"].get("source_url")
        ):
            raise ValueError("asset_integrity_mismatch")
        root = Path(evidence["asset_root"])
        if not root.is_absolute() or root != root.resolve():
            raise ValueError("asset_integrity_mismatch")
        representation = evidence["representation"]
        primary = Path(representation["uri"])
        contents = {}
        for member in representation["files"]:
            path = Path(member["uri"])
            if (
                not path.is_absolute()
                or path != path.resolve()
                or any(p.is_symlink() for p in (path, *path.parents))
            ):
                raise ValueError("asset_integrity_mismatch")
            logical = str(path.relative_to(root))
            raw = path.read_bytes()
            if len(raw) != member["bytes"] or hashlib.sha256(raw).hexdigest() != member["sha256"]:
                raise ValueError("asset_integrity_mismatch")
            contents[logical] = raw
        name = str(primary.relative_to(root))
        if (
            name not in contents
            or hashlib.sha256(contents[name]).hexdigest() != representation["sha256"]
        ):
            raise ValueError("asset_integrity_mismatch")
        if b"<!DOCTYPE" in contents[name] or b"<!ENTITY" in contents[name]:
            raise ValueError("asset_integrity_mismatch")
        tree = ET.fromstring(contents[name])
        for element in tree.iter():
            if "filename" in element.attrib:
                dependency = (primary.parent / element.attrib["filename"]).resolve()
                if str(dependency.relative_to(root.resolve())) not in contents:
                    raise ValueError("asset_integrity_mismatch")
        return name, contents

    def search(self, entity, source: Literal["local", "web"], limit: int = 8):
        if source not in {"local", "web"} or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("invalid source or candidate limit")
        candidates = []
        status, error = "blocked", "asset_not_found"
        details = {"entity_id": entity.id, "category": entity.category, "source": source}
        try:
            from self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers import (
                RoboTwinLocalProvider,
            )

            if source == "local":
                catalog_path = Path(self.config["providers"]["robotwin_local"]["catalog"])
                catalog_raw = catalog_path.read_bytes()
                catalog_ref = self.store.write_artifact(catalog_raw, "application/json")
                engine = RoboTwinLocalProvider(catalog_path)
                raw = engine.search_phrases([entity.category], limit=limit)
                details["provider_stats"] = engine.last_stats
                for candidate in raw:
                    candidates.append(
                        ProviderCandidate(
                            source=source,
                            entity_id=entity.id,
                            candidate_id=candidate.candidate_id,
                            category=candidate.category,
                            provider=candidate.provider,
                            format=candidate.format,
                            download_url=candidate.download_url,
                            source_page=candidate.source_page,
                            score=candidate.score,
                            license=self._local_license(candidate, json.loads(catalog_raw)),
                            engine_record=self._record(asdict(candidate)),
                            catalog_ref=catalog_ref,
                        )
                    )
            else:
                from self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers import (
                    load_providers,
                    tiered_search,
                )

                tiers, _ = load_providers(self.config)
                tiers = [tier for tier in tiers if tier.provider.name != "robotwin_local"]
                licenses = {}
                seen = {}

                def viable(candidate):
                    if not candidate.download_url.startswith(
                        "https://"
                    ) or candidate.format not in {"glb", "obj", "ply", "stl"}:
                        return False
                    seen[candidate.candidate_id] = candidate
                    licenses[candidate.candidate_id] = self._web_license(candidate)
                    return _permitted(licenses[candidate.candidate_id])

                result = tiered_search(tiers, entity.category, viable_fn=viable, limit=limit)
                details["tiers_consulted"] = result["tiers_consulted"]
                details["provider_errors"] = result["provider_errors"]
                details["provider_stats"] = result["provider_stats"]
                accepted = result.get("accepted", [])
                for candidate in accepted or list(seen.values())[:limit]:
                    candidates.append(
                        ProviderCandidate(
                            source=source,
                            entity_id=entity.id,
                            candidate_id=candidate.candidate_id,
                            category=candidate.category,
                            provider=candidate.provider,
                            format=candidate.format,
                            download_url=candidate.download_url,
                            source_page=candidate.source_page,
                            score=candidate.score,
                            license=licenses[candidate.candidate_id],
                            engine_record=self._record(asdict(candidate)),
                        )
                    )
            if candidates:
                status, error = (
                    ("succeeded", None)
                    if any(_permitted(c.license) for c in candidates)
                    else ("blocked", "blocked_license")
                )
        except ImportError:
            error = "blocked_external_resource"
        except (OSError, ValueError, KeyError, TypeError):
            status, error = "failed", "invalid_provider_evidence"
        receipt = self._record(
            {
                **details,
                "status": status,
                "error_code": error,
                "candidates": [c.model_dump() for c in candidates],
            }
        )
        return ProviderSearchResult(
            status=status, candidates=tuple(candidates), receipt=receipt, error_code=error
        )

    def fetch(self, candidate: ProviderCandidate, output_dir: Path):
        if not _permitted(candidate.license):
            return ProviderFetchResult(
                status="blocked",
                error_code="blocked_license",
                receipt=self._record(
                    {"candidate": candidate.model_dump(), "error_code": "blocked_license"}
                ),
            )
        status, error, source_path, manifest = "failed", None, None, None
        created = False
        try:
            output = Path(output_dir)
            if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
                raise ValueError("unsafe_output_path")
            if candidate.source == "local":
                name, contents = self._local_bytes(candidate.license)
                output.mkdir(parents=True, exist_ok=False)
                created = True
                for logical, raw in contents.items():
                    path = output / logical
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                source_path = str(output / name)
                manifest = str(output / "source-manifest.json")
                Path(manifest).write_text(
                    json.dumps(
                        {
                            "source": candidate.model_dump(),
                            "files": {
                                name: hashlib.sha256(raw).hexdigest()
                                for name, raw in contents.items()
                            },
                        }
                    )
                )
                status = "succeeded"
            else:
                from agenticsim.openxsim.assets import AssetCandidate, download_candidate

                from self_improving.asset_pipeline.active.asset_reuse.lib.a3_webfetch import (
                    stage_web_candidate,
                )

                original = AssetCandidate(
                    **json.loads(self.store.read_artifact(candidate.engine_record))
                )
                if (
                    original.download_url != candidate.download_url
                    or original.candidate_id != candidate.candidate_id
                ):
                    raise ValueError("asset_integrity_mismatch")
                current_license = self._web_license(original)
                if current_license != candidate.license or not _permitted(current_license):
                    return ProviderFetchResult(
                        status="blocked",
                        error_code="blocked_license",
                        receipt=self._record(
                            {"candidate": candidate.model_dump(), "error_code": "blocked_license"}
                        ),
                    )
                output.mkdir(parents=True, exist_ok=False)
                created = True
                record = stage_web_candidate(
                    original,
                    {"category": candidate.category},
                    "candidate",
                    0,
                    output / "staging",
                    output / "cache",
                    fetch_fn=lambda selected, cache: download_candidate(
                        selected, cache, timeout_s=60, max_bytes=64 * 1024 * 1024
                    ),
                )
                if record.get("license_spdx") != current_license.spdx:
                    raise ValueError("asset_integrity_mismatch")
                # The original a3 provider fallback is not author attribution. Preserve the
                # original evidence owner in our receipt without rewriting engine bytes.
                source_path = record["glb"]
                manifest = str(output / "staging" / "staging_manifest.json")
                status = "succeeded"
        except ImportError:
            status, error = "blocked", "blocked_external_resource"
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ET.ParseError) as exc:
            error = (
                str(exc)
                if str(exc) in {"asset_integrity_mismatch", "unsafe_output_path"}
                else "invalid_provider_evidence"
            )
        partials = []
        output = Path(output_dir)
        if created and output.is_dir() and not output.is_symlink():
            for path in output.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    ref = self.store.write_artifact(path.read_bytes(), "application/octet-stream")
                    partials.append(
                        {"path": str(path.relative_to(output)), "artifact": ref.model_dump()}
                    )
        receipt = self._record(
            {
                "candidate": candidate.model_dump(),
                "status": status,
                "output_dir": str(output_dir),
                "partial_artifacts": partials,
                "error_code": error,
                "source_path": source_path,
                "staging_manifest": manifest,
            }
        )
        return ProviderFetchResult(
            status=status,
            source_path=source_path,
            staging_manifest=manifest,
            receipt=receipt,
            error_code=error,
        )
