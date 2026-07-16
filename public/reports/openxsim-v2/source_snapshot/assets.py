"""Search, download, provenance, conversion, and registration for public assets."""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .ir import AssetBundle, AssetRepresentation


SUPPORTED_ASSET_EXTENSIONS = {
    ".obj": "obj",
    ".stl": "stl",
    ".ply": "ply",
    ".glb": "glb",
    ".gltf": "gltf",
    ".usd": "usd",
    ".usda": "usda",
    ".usdc": "usdc",
    ".urdf": "urdf",
    ".xml": "mjcf",
    ".zip": "zip",
}


class AssetScoutError(RuntimeError):
    """Raised when an asset acquisition or conversion step fails."""


@dataclass(frozen=True)
class AssetCandidate:
    """Search result with enough provenance to fetch one concrete asset."""

    candidate_id: str
    name: str
    category: str
    download_url: str
    source_page: str
    format: str
    provider: str
    license: str = "unknown"
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, provider: str = "catalog") -> "AssetCandidate":
        url = str(data.get("download_url") or data.get("url") or "")
        name = str(data.get("name") or Path(urllib.parse.urlparse(url).path).name or "asset")
        candidate_id = str(data.get("candidate_id") or hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
        fmt = str(data.get("format") or detect_format_from_name(name))
        return cls(
            candidate_id=candidate_id,
            name=name,
            category=str(data.get("category") or "object"),
            download_url=url,
            source_page=str(data.get("source_page") or url),
            format=fmt,
            provider=str(data.get("provider") or provider),
            license=str(data.get("license") or "unknown"),
            score=float(data.get("score", 0.0)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class DownloadedAsset:
    candidate: AssetCandidate
    path: str
    sha256: str
    size_bytes: int
    media_type: str
    detected_format: str
    provenance_path: str
    cache_hit: bool = False


class AssetSearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 20) -> list[AssetCandidate]: ...


def detect_format_from_name(name: str) -> str:
    return SUPPORTED_ASSET_EXTENSIONS.get(Path(name).suffix.lower(), Path(name).suffix.lower().lstrip("."))


def _query_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) > 1}


def _relevance(query: str, candidate: AssetCandidate) -> float:
    tokens = _query_tokens(query)
    haystack = " ".join(
        [candidate.name, candidate.category, candidate.source_page, json.dumps(candidate.metadata, sort_keys=True)]
    ).lower()
    matches = sum(1 for token in tokens if token in haystack)
    format_bonus = 0.2 if candidate.format.lower() in set(SUPPORTED_ASSET_EXTENSIONS.values()) else 0.0
    provenance_bonus = 0.1 if candidate.source_page and candidate.download_url else 0.0
    return candidate.score + matches + format_bonus + provenance_bonus


class CatalogSearchProvider:
    """Search a local or HTTP JSON catalog containing concrete download URLs."""

    name = "json_catalog"

    def __init__(self, catalog: str | Path | Iterable[Mapping[str, Any]], *, timeout_s: float = 20.0):
        self.catalog = catalog
        self.timeout_s = timeout_s

    def _load(self) -> list[Mapping[str, Any]]:
        if not isinstance(self.catalog, (str, Path)):
            return list(self.catalog)
        value = str(self.catalog)
        if urllib.parse.urlparse(value).scheme in {"http", "https"}:
            request = urllib.request.Request(value, headers={"User-Agent": "AgenticSim-AssetScout/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    payload = json.load(response)
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise AssetScoutError(f"catalog request failed for {value}: {exc}") from exc
        else:
            try:
                payload = json.loads(Path(value).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AssetScoutError(f"catalog read failed for {value}: {exc}") from exc
        if isinstance(payload, dict):
            container = payload
            payload = container.get("assets") or container.get("items") or container.get("candidates") or []
            if not payload and container.get("selected"):
                payload = [container["selected"]]
        if not isinstance(payload, list):
            raise AssetScoutError("asset catalog must be a list or contain an assets/items list")
        return payload

    def search(self, query: str, limit: int = 20) -> list[AssetCandidate]:
        results = [AssetCandidate.from_dict(item, provider=self.name) for item in self._load()]
        ranked = sorted(results, key=lambda item: (-_relevance(query, item), item.candidate_id))
        return ranked[:limit]


class GitHubTreeSearchProvider:
    """Search supported files in one public GitHub repository tree."""

    name = "github_tree"

    def __init__(
        self,
        repository: str,
        *,
        branch: str = "main",
        token: str | None = None,
        license: str = "unknown",
        timeout_s: float = 30.0,
    ):
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise ValueError("repository must be owner/name")
        self.repository = repository
        self.branch = branch
        self.token = token
        self.license = license
        self.timeout_s = timeout_s

    def search(self, query: str, limit: int = 20) -> list[AssetCandidate]:
        encoded_branch = urllib.parse.quote(self.branch, safe="")
        url = f"https://api.github.com/repos/{self.repository}/git/trees/{encoded_branch}?recursive=1"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AgenticSim-AssetScout/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AssetScoutError(f"GitHub tree search failed for {self.repository}: {exc}") from exc
        if payload.get("truncated"):
            raise AssetScoutError(f"GitHub tree for {self.repository} was truncated; refuse incomplete search")
        tokens = _query_tokens(query)
        candidates: list[AssetCandidate] = []
        for item in payload.get("tree", []):
            path = str(item.get("path") or "")
            fmt = detect_format_from_name(path)
            if item.get("type") != "blob" or fmt not in set(SUPPORTED_ASSET_EXTENSIONS.values()):
                continue
            lower = path.lower()
            token_matches = sum(1 for token in tokens if token in lower)
            if tokens and not token_matches:
                continue
            quoted_path = urllib.parse.quote(path, safe="/")
            candidate = AssetCandidate(
                candidate_id=f"github:{self.repository}:{path}",
                name=Path(path).name,
                category=Path(path).parent.name or "object",
                download_url=f"https://raw.githubusercontent.com/{self.repository}/{encoded_branch}/{quoted_path}",
                source_page=f"https://github.com/{self.repository}/blob/{encoded_branch}/{quoted_path}",
                format=fmt,
                provider=self.name,
                license=self.license,
                score=float(token_matches),
                metadata={"repository": self.repository, "branch": self.branch, "path": path},
            )
            candidates.append(candidate)
        return sorted(candidates, key=lambda item: (-_relevance(query, item), item.name))[:limit]


class GitHubRepositoryDiscoveryProvider:
    """Discover public asset repositories, then search their concrete file trees."""

    name = "github_repository_discovery"

    def __init__(
        self,
        *,
        repository_query: str | None = None,
        repository_limit: int = 5,
        token: str | None = None,
        timeout_s: float = 30.0,
    ):
        if not 1 <= repository_limit <= 20:
            raise ValueError("repository_limit must be between 1 and 20")
        self.repository_query = repository_query
        self.repository_limit = repository_limit
        self.token = token
        self.timeout_s = timeout_s
        self.last_errors: list[dict[str, str]] = []

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AgenticSim-AssetScout/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AssetScoutError(f"GitHub API request failed for {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise AssetScoutError(f"GitHub API response is not an object: {url}")
        return payload

    def search(self, query: str, limit: int = 20) -> list[AssetCandidate]:
        discovery_query = (self.repository_query or query).strip()
        if not discovery_query:
            raise ValueError("GitHub repository discovery query is empty")
        encoded_query = urllib.parse.quote(discovery_query)
        search_url = (
            "https://api.github.com/search/repositories"
            f"?q={encoded_query}&sort=stars&order=desc&per_page={self.repository_limit}"
        )
        payload = self._json(search_url)
        repositories = payload.get("items") or []
        if not isinstance(repositories, list):
            raise AssetScoutError("GitHub repository search returned an invalid items field")

        self.last_errors = []
        tokens = _query_tokens(query)
        candidates: list[AssetCandidate] = []
        for repository in repositories[: self.repository_limit]:
            full_name = str(repository.get("full_name") or "")
            branch = str(repository.get("default_branch") or "main")
            if not re.fullmatch(r"[^/\s]+/[^/\s]+", full_name):
                continue
            encoded_branch = urllib.parse.quote(branch, safe="")
            tree_url = f"https://api.github.com/repos/{full_name}/git/trees/{encoded_branch}?recursive=1"
            try:
                tree = self._json(tree_url)
                if tree.get("truncated"):
                    raise AssetScoutError(f"GitHub tree for {full_name} was truncated")
            except AssetScoutError as exc:
                self.last_errors.append({"repository": full_name, "error": str(exc)})
                continue
            license_data = repository.get("license") or {}
            license_name = str(license_data.get("spdx_id") or license_data.get("name") or "unknown")
            stars = int(repository.get("stargazers_count") or 0)
            for item in tree.get("tree") or []:
                path = str(item.get("path") or "")
                fmt = detect_format_from_name(path)
                if item.get("type") != "blob" or fmt not in set(SUPPORTED_ASSET_EXTENSIONS.values()):
                    continue
                token_matches = sum(1 for token in tokens if token in path.lower())
                if tokens and not token_matches:
                    continue
                quoted_path = urllib.parse.quote(path, safe="/")
                candidates.append(
                    AssetCandidate(
                        candidate_id=f"github:{full_name}:{path}",
                        name=Path(path).name,
                        category=Path(path).parent.name or "object",
                        download_url=f"https://raw.githubusercontent.com/{full_name}/{encoded_branch}/{quoted_path}",
                        source_page=f"https://github.com/{full_name}/blob/{encoded_branch}/{quoted_path}",
                        format=fmt,
                        provider=self.name,
                        license=license_name,
                        score=float(token_matches) + min(math.log10(stars + 1), 5.0) * 0.05,
                        metadata={
                            "repository": full_name,
                            "repository_url": repository.get("html_url"),
                            "repository_description": repository.get("description"),
                            "repository_stars": stars,
                            "repository_query": discovery_query,
                            "branch": branch,
                            "path": path,
                        },
                    )
                )
        if not candidates and self.last_errors:
            raise AssetScoutError(f"discovered repositories could not be searched: {self.last_errors}")
        return sorted(candidates, key=lambda item: (-_relevance(query, item), item.candidate_id))[:limit]


class AssetScout:
    """Aggregate providers, rank candidates, and preserve failed-provider evidence."""

    def __init__(self, providers: Iterable[AssetSearchProvider]):
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("AssetScout requires at least one provider")
        self.last_errors: list[dict[str, str]] = []

    def search(self, query: str, *, limit: int = 20, strict: bool = False) -> list[AssetCandidate]:
        if not query.strip():
            raise ValueError("asset search query is empty")
        candidates: list[AssetCandidate] = []
        self.last_errors = []
        for provider in self.providers:
            try:
                candidates.extend(provider.search(query, limit=limit))
                for error in getattr(provider, "last_errors", []):
                    self.last_errors.append(
                        {
                            "provider": provider.name,
                            "error": f"{error.get('repository', 'provider')}: {error.get('error', 'unknown error')}",
                        }
                    )
            except Exception as exc:
                self.last_errors.append({"provider": provider.name, "error": str(exc)})
                if strict:
                    raise
        deduplicated: dict[str, AssetCandidate] = {}
        for candidate in candidates:
            previous = deduplicated.get(candidate.download_url)
            if previous is None or _relevance(query, candidate) > _relevance(query, previous):
                deduplicated[candidate.download_url] = candidate
        return sorted(
            deduplicated.values(), key=lambda item: (-_relevance(query, item), item.candidate_id)
        )[:limit]


def _safe_filename(value: str, fallback: str = "asset.bin") -> str:
    name = Path(urllib.parse.urlparse(value).path).name or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_candidate(
    candidate: AssetCandidate,
    cache_dir: str | Path,
    *,
    timeout_s: float = 60.0,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> DownloadedAsset:
    """Download one result atomically and write a provenance sidecar."""

    parsed = urllib.parse.urlparse(candidate.download_url)
    if parsed.scheme not in {"http", "https", "file"}:
        raise AssetScoutError(f"unsupported download scheme: {parsed.scheme!r}")
    cache = Path(cache_dir).expanduser().resolve()
    key = hashlib.sha256(candidate.download_url.encode("utf-8")).hexdigest()[:20]
    target_dir = cache / key
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _safe_filename(candidate.download_url, fallback=f"asset.{candidate.format}")
    provenance = target_dir / "provenance.json"
    if target.is_file() and provenance.is_file():
        payload = json.loads(provenance.read_text(encoding="utf-8"))
        current_sha = _sha256(target)
        if payload.get("sha256") == current_sha and int(payload.get("size_bytes", -1)) == target.stat().st_size:
            return DownloadedAsset(
                candidate=candidate,
                path=str(target),
                sha256=current_sha,
                size_bytes=target.stat().st_size,
                media_type=str(payload.get("media_type") or "application/octet-stream"),
                detected_format=str(payload.get("detected_format") or detect_format_from_name(target.name)),
                provenance_path=str(provenance),
                cache_hit=True,
            )

    request = urllib.request.Request(candidate.download_url, headers={"User-Agent": "AgenticSim-AssetScout/1.0"})
    temporary = target.with_suffix(target.suffix + ".part")
    size = 0
    media_type = "application/octet-stream"
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response, temporary.open("wb") as stream:
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > max_bytes:
                raise AssetScoutError(f"asset exceeds max_bytes before download: {content_length} > {max_bytes}")
            media_type = response.headers.get_content_type() or media_type
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise AssetScoutError(f"asset exceeds max_bytes during download: {size} > {max_bytes}")
                stream.write(chunk)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if size <= 0:
        temporary.unlink(missing_ok=True)
        raise AssetScoutError("downloaded asset is empty")
    temporary.replace(target)
    detected = detect_format_from_name(target.name)
    payload = {
        "schema": "agenticsim.asset_provenance.v1",
        "candidate": asdict(candidate),
        "downloaded_at_unix": int(time.time()),
        "path": str(target),
        "sha256": _sha256(target),
        "size_bytes": target.stat().st_size,
        "media_type": media_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        "detected_format": detected,
    }
    provenance.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DownloadedAsset(
        candidate=candidate,
        path=str(target),
        sha256=str(payload["sha256"]),
        size_bytes=int(payload["size_bytes"]),
        media_type=str(payload["media_type"]),
        detected_format=detected,
        provenance_path=str(provenance),
        cache_hit=False,
    )


def _safe_extract_zip(path: Path, output: Path, *, max_members: int = 10000) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > max_members:
            raise AssetScoutError(f"zip contains too many entries: {len(members)}")
        root = output.resolve()
        for member in members:
            destination = (output / member.filename).resolve()
            if os.path.commonpath([str(root), str(destination)]) != str(root):
                raise AssetScoutError(f"zip path traversal blocked: {member.filename}")
        archive.extractall(output)
    for item in output.rglob("*"):
        if item.is_file():
            extracted.append(item)
    return tuple(extracted)


def _parse_obj(path: Path) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("v "):
            values = line.split()[1:4]
            if len(values) == 3:
                vertices.append(tuple(float(value) for value in values))
        elif line.startswith("f "):
            indices: list[int] = []
            for token in line.split()[1:]:
                raw_index = int(token.split("/", 1)[0])
                index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                indices.append(index)
            if len(indices) >= 3:
                faces.append(tuple(indices))
    if not vertices or not faces:
        raise AssetScoutError(f"OBJ has no usable geometry: {path}")
    return vertices, faces


def _bounds(vertices: list[tuple[float, float, float]]) -> tuple[list[float], list[float], list[float]]:
    minimum = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    extent = [maximum[axis] - minimum[axis] for axis in range(3)]
    if max(extent) <= 1e-12:
        raise AssetScoutError("asset geometry has a degenerate bounding box")
    return minimum, maximum, extent


def _write_obj(
    target: Path,
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    *,
    material_library: str | None = None,
    material_name: str | None = None,
) -> None:
    lines = ["# Generated by AgenticSim AssetCompiler"]
    if material_library:
        lines.append(f"mtllib {material_library}")
    lines.extend(f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in vertices)
    if material_name:
        lines.append(f"usemtl {material_name}")
    lines.extend("f " + " ".join(str(index + 1) for index in face) for face in faces)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_obj_preview(
    source: str | Path,
    target: str | Path,
    *,
    size_px: int = 512,
    max_faces: int = 20000,
) -> dict[str, Any]:
    """Render a deterministic orthographic proof preview without a GPU runtime."""

    if size_px < 128:
        raise ValueError("size_px must be at least 128")
    if max_faces <= 0:
        raise ValueError("max_faces must be positive")
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise AssetScoutError("OBJ preview rendering requires Pillow") from exc

    path = Path(source).expanduser().resolve()
    output = Path(target).expanduser().resolve()
    vertices, faces = _parse_obj(path)
    minimum, maximum, extent = _bounds(vertices)
    axis_pairs = ((0, 1), (0, 2), (1, 2))
    first_axis, second_axis = max(axis_pairs, key=lambda pair: extent[pair[0]] * extent[pair[1]])
    depth_axis = next(axis for axis in range(3) if axis not in {first_axis, second_axis})
    drawable = [face for face in faces if all(0 <= index < len(vertices) for index in face)]
    if not drawable:
        raise AssetScoutError(f"OBJ has no drawable faces: {path}")
    stride = max(1, math.ceil(len(drawable) / max_faces))
    sampled = drawable[::stride]
    span_x = max(extent[first_axis], 1e-12)
    span_y = max(extent[second_axis], 1e-12)
    margin = size_px * 0.08
    scale = min((size_px - 2 * margin) / span_x, (size_px - 2 * margin) / span_y)
    center_x = (minimum[first_axis] + maximum[first_axis]) / 2.0
    center_y = (minimum[second_axis] + maximum[second_axis]) / 2.0

    def project(vertex: tuple[float, float, float]) -> tuple[float, float]:
        return (
            size_px / 2.0 + (vertex[first_axis] - center_x) * scale,
            size_px / 2.0 - (vertex[second_axis] - center_y) * scale,
        )

    depth_min = minimum[depth_axis]
    depth_span = max(extent[depth_axis], 1e-12)
    ordered = sorted(
        sampled,
        key=lambda face: sum(vertices[index][depth_axis] for index in face) / len(face),
    )
    image = Image.new("RGB", (size_px, size_px), color=(244, 246, 248))
    draw = ImageDraw.Draw(image)
    for face in ordered:
        depth = sum(vertices[index][depth_axis] for index in face) / len(face)
        normalized_depth = (depth - depth_min) / depth_span
        shade = int(105 + 85 * normalized_depth)
        draw.polygon(
            [project(vertices[index]) for index in face],
            fill=(shade, min(210, shade + 14), min(224, shade + 28)),
            outline=(58, 67, 76),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return {
        "path": str(output),
        "source": str(path),
        "width": size_px,
        "height": size_px,
        "projection_axes": [first_axis, second_axis],
        "depth_axis": depth_axis,
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "rendered_face_count": len(ordered),
        "sha256": _sha256(output),
    }


def _normalize_obj(source: Path, target: Path, *, target_max_extent_m: float = 1.0) -> dict[str, Any]:
    vertices, faces = _parse_obj(source)
    minimum, maximum, extent = _bounds(vertices)
    center = [(minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)]
    scale = target_max_extent_m / max(extent)
    normalized = [
        tuple((vertex[axis] - center[axis]) * scale for axis in range(3))
        for vertex in vertices
    ]
    normalized_minimum, normalized_maximum, normalized_extent = _bounds(normalized)
    material_path = target.parent / "default_material.mtl"
    material_path.write_text(
        "newmtl AgenticSimDefault\n"
        "Kd 0.65 0.68 0.72\n"
        "Ka 0.05 0.05 0.05\n"
        "Ks 0.08 0.08 0.08\n"
        "Ns 16\n",
        encoding="utf-8",
    )
    _write_obj(
        target,
        normalized,
        faces,
        material_library=material_path.name,
        material_name="AgenticSimDefault",
    )
    return {
        "source_bounds": {"minimum": minimum, "maximum": maximum, "extent": extent},
        "source_center": center,
        "scale_factor": scale,
        "target_max_extent_m": target_max_extent_m,
        "normalized_bounds": {
            "minimum": normalized_minimum,
            "maximum": normalized_maximum,
            "extent": normalized_extent,
        },
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "material_path": str(material_path),
        "material_status": "generated_default_material",
    }


def _write_box_collision_proxy(target: Path, bounds: Mapping[str, Any]) -> None:
    minimum = [float(value) for value in bounds["minimum"]]
    maximum = [float(value) for value in bounds["maximum"]]
    vertices = [
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    faces = [
        (0, 1, 3, 2),
        (4, 6, 7, 5),
        (0, 4, 5, 1),
        (2, 3, 7, 6),
        (0, 2, 6, 4),
        (1, 5, 7, 3),
    ]
    _write_obj(target, vertices, faces)


def _articulation_audit(path: Path, source_format: str) -> dict[str, Any]:
    if source_format not in {"urdf", "mjcf"}:
        return {
            "status": "rigid_mesh_no_joint_schema" if source_format in {"obj", "stl", "ply", "glb", "gltf"} else "not_checked",
            "articulated": False,
            "joint_count": 0,
            "joint_types": [],
        }
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise AssetScoutError(f"could not parse {source_format} articulation: {exc}") from exc
    if source_format == "urdf":
        joints = root.findall(".//joint")
        joint_types = [str(joint.get("type") or "unknown") for joint in joints]
        movable = [value for value in joint_types if value != "fixed"]
    else:
        joints = root.findall(".//joint") + root.findall(".//freejoint")
        joint_types = [joint.tag if joint.tag == "freejoint" else str(joint.get("type") or "hinge") for joint in joints]
        movable = joint_types
    return {
        "status": "parsed",
        "articulated": bool(movable),
        "joint_count": len(joints),
        "movable_joint_count": len(movable),
        "joint_types": joint_types,
    }


def _obj_to_usda(source: Path, target: Path) -> None:
    vertices, faces = _parse_obj(source)
    points = ", ".join(f"({x:.9g}, {y:.9g}, {z:.9g})" for x, y, z in vertices)
    counts = ", ".join(str(len(face)) for face in faces)
    indices = ", ".join(str(index) for face in faces for index in face)
    target.write_text(
        "#usda 1.0\n"
        "(\n    defaultPrim = \"Asset\"\n    metersPerUnit = 1\n    upAxis = \"Z\"\n)\n\n"
        "def Xform \"Asset\"\n{\n"
        "    def Mesh \"Mesh\"\n    {\n"
        f"        point3f[] points = [{points}]\n"
        f"        int[] faceVertexCounts = [{counts}]\n"
        f"        int[] faceVertexIndices = [{indices}]\n"
        "        color3f[] primvars:displayColor = [(0.65, 0.68, 0.72)]\n"
        "        uniform token subdivisionScheme = \"none\"\n"
        "    }\n}\n",
        encoding="utf-8",
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _write_mesh_wrapper(
    visual_source: Path,
    collision_source: Path,
    target: Path,
    *,
    format: str,
    asset_id: str,
) -> None:
    visual_relative = os.path.relpath(visual_source, target.parent)
    collision_relative = os.path.relpath(collision_source, target.parent)
    if format == "mjcf":
        target.write_text(
            "<mujoco model=\"asset\">\n"
            "  <asset>\n"
            f"    <mesh name=\"{_xml_escape(asset_id)}_visual\" file=\"{_xml_escape(visual_relative)}\"/>\n"
            f"    <mesh name=\"{_xml_escape(asset_id)}_collision\" file=\"{_xml_escape(collision_relative)}\"/>\n"
            "  </asset>\n"
            f"  <worldbody><body name=\"{_xml_escape(asset_id)}\">\n"
            f"    <geom type=\"mesh\" mesh=\"{_xml_escape(asset_id)}_visual\" contype=\"0\" conaffinity=\"0\" rgba=\"0.65 0.68 0.72 1\"/>\n"
            f"    <geom type=\"mesh\" mesh=\"{_xml_escape(asset_id)}_collision\" density=\"1000\"/>\n"
            "  </body></worldbody>\n"
            "</mujoco>\n",
            encoding="utf-8",
        )
    elif format == "urdf":
        target.write_text(
            f"<robot name=\"{_xml_escape(asset_id)}\">\n"
            "  <link name=\"base\">\n"
            f"    <visual><geometry><mesh filename=\"{_xml_escape(visual_relative)}\"/></geometry></visual>\n"
            f"    <collision><geometry><mesh filename=\"{_xml_escape(collision_relative)}\"/></geometry></collision>\n"
            "  </link>\n</robot>\n",
            encoding="utf-8",
        )
    else:
        raise AssetScoutError(f"unsupported mesh wrapper format: {format}")


def compile_downloaded_asset(
    downloaded: DownloadedAsset,
    output_dir: str | Path,
    *,
    asset_id: str,
    category: str | None = None,
    target_formats: Iterable[str] = ("usda", "mjcf", "urdf", "sapien_manifest", "metasim_object"),
) -> AssetBundle:
    """Convert a downloaded asset into portable and backend representations."""

    source = Path(downloaded.path).resolve()
    output = Path(output_dir).expanduser().resolve() / asset_id
    output.mkdir(parents=True, exist_ok=True)
    source_format = downloaded.detected_format.lower()
    if source_format == "zip":
        extracted = _safe_extract_zip(source, output / "extracted")
        supported = [item for item in extracted if detect_format_from_name(item.name) in set(SUPPORTED_ASSET_EXTENSIONS.values()) - {"zip"}]
        if not supported:
            raise AssetScoutError("downloaded archive contains no supported asset files")
        source = sorted(supported, key=lambda item: (item.suffix.lower() not in {".urdf", ".obj", ".usd", ".usda"}, str(item)))[0]
        source_format = detect_format_from_name(source.name)

    source_copy = output / f"source{source.suffix.lower()}"
    if source != source_copy:
        shutil.copy2(source, source_copy)
    representations: list[AssetRepresentation] = [
        AssetRepresentation(
            format=f"{source_format}_source" if source_format == "obj" else source_format,
            uri=str(source_copy),
            backend="portable",
            role="source",
            sha256=_sha256(source_copy),
            size_bytes=source_copy.stat().st_size,
            metadata={"original_download": downloaded.path, "preserved_source": True},
        )
    ]
    generated: dict[str, str] = {}
    visual_source = source_copy
    collision_source = source_copy
    if source_format == "obj":
        visual_source = output / f"{asset_id}.normalized.obj"
        normalization = _normalize_obj(source_copy, visual_source)
        collision_source = output / f"{asset_id}.collision_box.obj"
        _write_box_collision_proxy(collision_source, normalization["normalized_bounds"])
        generated.update(
            {
                "normalized_obj": str(visual_source),
                "collision_proxy": str(collision_source),
                "material": str(normalization["material_path"]),
            }
        )
        representations.extend(
            [
                AssetRepresentation(
                    format="obj",
                    uri=str(visual_source),
                    backend="portable",
                    role="visual",
                    sha256=_sha256(visual_source),
                    size_bytes=visual_source.stat().st_size,
                    metadata={"normalization": normalization},
                ),
                AssetRepresentation(
                    format="obj_collision_proxy",
                    uri=str(collision_source),
                    backend="portable",
                    role="collision",
                    sha256=_sha256(collision_source),
                    size_bytes=collision_source.stat().st_size,
                    metadata={"strategy": "axis_aligned_convex_box"},
                ),
            ]
        )
        collision_audit = {
            "status": "generated",
            "strategy": "axis_aligned_convex_box",
            "path": str(collision_source),
            "sha256": _sha256(collision_source),
        }
        material_audit = {
            "status": normalization["material_status"],
            "path": normalization["material_path"],
            "sha256": _sha256(Path(normalization["material_path"])),
        }
    else:
        normalization = {
            "status": "not_applied",
            "reason": f"normalization is not implemented for {source_format}",
        }
        collision_audit = {
            "status": "source_geometry_unmodified",
            "strategy": "source_geometry",
            "path": str(collision_source),
        }
        material_audit = {
            "status": "source_materials_preserved_not_validated",
        }
    articulation_audit = _articulation_audit(source_copy, source_format)

    for requested in dict.fromkeys(value.lower() for value in target_formats):
        if requested == source_format:
            continue
        if requested == "usda" and source_format == "obj":
            target = output / f"{asset_id}.usda"
            _obj_to_usda(visual_source, target)
            backend = "isaacsim"
        elif requested in {"mjcf", "urdf"} and source_format in {"obj", "stl"}:
            suffix = ".xml" if requested == "mjcf" else ".urdf"
            target = output / f"{asset_id}{suffix}"
            _write_mesh_wrapper(
                visual_source,
                collision_source,
                target,
                format=requested,
                asset_id=asset_id,
            )
            backend = "mujoco" if requested == "mjcf" else "portable"
        elif requested == "sapien_manifest":
            target = output / "sapien_asset.json"
            target.write_text(
                json.dumps(
                    {
                        "schema": "agenticsim.sapien_asset.v1",
                        "asset_id": asset_id,
                        "visual_path": str(visual_source),
                        "collision_path": str(collision_source),
                        "source_format": source_format,
                        "normalization": normalization,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            backend = "sapien"
        elif requested == "metasim_object":
            target = output / "metasim_object.json"
            known_paths = {
                "mesh_path": str(visual_source) if source_format in {"obj", "stl", "ply", "glb", "gltf"} else None,
                "usd_path": str(source_copy) if source_format in {"usd", "usda", "usdc"} else None,
                "urdf_path": str(source_copy) if source_format == "urdf" else generated.get("urdf"),
                "mjcf_path": str(source_copy) if source_format == "mjcf" else generated.get("mjcf"),
            }
            target.write_text(
                json.dumps(
                    {"schema": "agenticsim.metasim_object.v1", "asset_id": asset_id, **known_paths},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            backend = "metasim"
        else:
            continue
        generated[requested] = str(target)
        representations.append(
            AssetRepresentation(
                format=requested,
                uri=str(target),
                backend=backend,
                sha256=_sha256(target),
                size_bytes=target.stat().st_size,
                metadata={"converted_from": source_format},
            )
        )

    validation = {
        "schema": "agenticsim.asset_validation.v1",
        "asset_id": asset_id,
        "source_format": source_format,
        "normalization": normalization,
        "material": material_audit,
        "collision": collision_audit,
        "articulation": articulation_audit,
        "runtime_import_required": True,
    }
    validation_path = output / "asset_validation.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    generated["validation_manifest"] = str(validation_path)
    representations.append(
        AssetRepresentation(
            format="validation_manifest",
            uri=str(validation_path),
            backend="portable",
            role="validation",
            sha256=_sha256(validation_path),
            size_bytes=validation_path.stat().st_size,
        )
    )
    conversion = {
        "schema": "agenticsim.asset_conversion.v1",
        "asset_id": asset_id,
        "source_format": source_format,
        "source_sha256": _sha256(source_copy),
        "generated": generated,
        "validation_manifest": str(validation_path),
        "provenance_path": downloaded.provenance_path,
    }
    (output / "conversion.json").write_text(
        json.dumps(conversion, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    bundle = AssetBundle(
        asset_id=asset_id,
        category=category or downloaded.candidate.category,
        representations=tuple(representations),
        source={
            "provider": downloaded.candidate.provider,
            "source_page": downloaded.candidate.source_page,
            "download_url": downloaded.candidate.download_url,
            "license": downloaded.candidate.license,
            "download_sha256": downloaded.sha256,
            "provenance_path": downloaded.provenance_path,
        },
        physical={
            "normalization": normalization,
            "material_status": material_audit["status"],
            "collision_status": collision_audit["status"],
            "collision_strategy": collision_audit["strategy"],
            "runtime_import_required": True,
        },
        articulation=articulation_audit,
        tags=("downloaded", "provenance_recorded", "validated", "normalized" if source_format == "obj" else "normalization_pending"),
    )
    bundle.validate()
    return bundle
