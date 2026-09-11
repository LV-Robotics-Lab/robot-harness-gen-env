"""Sealed, camera-free Genesis CPU backend for one rigid-asset probe."""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .genesis_asset_probe import (
    GenesisAssetBackendLoad,
    GenesisAssetBackendStep,
    GenesisAssetGroundContact,
    GenesisAssetProbeError,
    GenesisAssetProbeLoadRequest,
)

GENESIS_WORLD_COMMIT = "0e74bf392781884ccad765c3f344419c86b872ca"
GENESIS_CPU_PROCESS_ENV = MappingProxyType(
    {
        "GS_HEADLESS": "1",
        "PYGLET_HEADLESS": "1",
        "GS_PARA_LEVEL": "0",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
)


class GenesisCpuBackendUnavailableError(RuntimeError):
    """The exact Genesis CPU runtime is absent, drifted, or already occupied."""


class _NoopViewerLock:
    def __enter__(self) -> "_NoopViewerLock":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> bool:
        return False


class _NoopVisualizer:
    """The minimum Scene visualizer contract needed by physics-only build and step."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.is_built = False
        self.cameras: tuple[()] = ()
        self.viewer_lock = _NoopViewerLock()

    def build(self) -> None:
        self.is_built = True

    def destroy(self) -> None:
        self.is_built = False

    def reset(self) -> None:
        pass

    def update(self, *_args: object, **_kwargs: object) -> None:
        pass


class GenesisCpuAssetProbeBackend:
    """Run the bounded probe on fixed Genesis CPU physics without a visualizer."""

    backend = "genesis"
    backend_version = "1.3.3"
    loader_contract = "harness.genesis_rigid_loader.v1"

    def __init__(
        self,
        genesis_source_root: Path,
        runtime_implementation_sha256: str,
    ) -> None:
        if not isinstance(genesis_source_root, Path):
            raise TypeError("genesis_source_root must be a concrete Path")
        if (
            type(runtime_implementation_sha256) is not str
            or len(runtime_implementation_sha256) != 64
            or any(
                character not in "0123456789abcdef" for character in runtime_implementation_sha256
            )
        ):
            raise TypeError("runtime_implementation_sha256 must be a lowercase SHA-256")
        self.implementation_sha256 = runtime_implementation_sha256
        self._source_root = genesis_source_root.resolve(strict=False)
        self._scene: Any | None = None
        self._asset: Any | None = None
        self._ground_geom_index: int | None = None
        self._asset_geom_indices: tuple[int, ...] = ()
        self._next_step = 1

    def load(self, request: GenesisAssetProbeLoadRequest) -> GenesisAssetBackendLoad:
        if type(request) is not GenesisAssetProbeLoadRequest:
            raise TypeError("request must be the exact GenesisAssetProbeLoadRequest")
        if self._scene is not None:
            raise GenesisCpuBackendUnavailableError("the backend already owns a live Genesis scene")
        self._prepare_runtime()
        gs = self._import_genesis()
        if getattr(gs, "_initialized", False):
            raise GenesisCpuBackendUnavailableError(
                "Genesis is already initialized in this process"
            )
        gs.init(backend=gs.cpu, seed=0, precision="32", logging_level=logging.WARNING)
        scene_module = importlib.import_module("genesis.engine.scene")
        original_visualizer = scene_module.Visualizer
        try:
            scene_module.Visualizer = _NoopVisualizer
            scene = gs.Scene(
                show_viewer=False,
                sim_options=gs.options.SimOptions(
                    dt=0.004,
                    substeps=1,
                    gravity=(0.0, 0.0, -9.81),
                ),
                rigid_options=gs.options.RigidOptions(
                    enable_collision=True,
                    constraint_solver=gs.constraint_solver.Newton,
                    iterations=50,
                    ls_iterations=50,
                    tolerance=1e-8,
                    constraint_timeconst=0.01,
                    use_hibernation=False,
                ),
            )
        finally:
            scene_module.Visualizer = original_visualizer

        physics = self._read_physics(request.materialized_root / "physics.json")
        source_link_count, source_joint_count = self._read_urdf_counts(request.root_path)
        ground = scene.add_entity(gs.morphs.Plane(), name="ground")
        asset = scene.add_entity(
            gs.morphs.URDF(
                file=str(request.root_path),
                fixed=False,
                convexify=False,
                recompute_inertia=False,
            ),
            material=gs.materials.Rigid(friction=physics["friction"]),
            name="asset",
            vis_mode="collision",
        )
        scene.build()
        before_mass = self._scalar(asset.get_mass())
        if not math.isclose(before_mass, physics["mass_kg"], rel_tol=1e-8, abs_tol=1e-12):
            asset.set_mass(physics["mass_kg"])
        mass = self._scalar(asset.get_mass())
        inertia = self._matrix(asset.get_links_inertia())
        friction_values = tuple(self._scalar(geom.get_friction()) for geom in asset.geoms)
        if not friction_values or any(
            not math.isclose(value, physics["friction"], rel_tol=1e-6, abs_tol=1e-8)
            for value in friction_values
        ):
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "Genesis loaded friction differs from the sealed asset parameters",
            )
        vertices = self._array(asset.get_verts()).reshape(-1, 3)
        position = self._array(asset.get_pos()).reshape(3)
        minimum_z = float(vertices[:, 2].min())
        translation = (0.0, 0.0, 0.01 - minimum_z)
        asset.set_pos(position + self._array(translation))

        self._scene = scene
        self._asset = asset
        self._ground_geom_index = int(ground.geoms[0].idx)
        self._asset_geom_indices = tuple(int(geom.idx) for geom in asset.geoms)
        self._next_step = 1
        source_collision_members = request.representation.collision_member_paths
        return GenesisAssetBackendLoad(
            source="urdf",
            source_link_count=source_link_count,
            source_joint_count=source_joint_count,
            loaded_link_count=len(asset.links),
            loaded_joint_count=len(asset.joints),
            loaded_dof_count=int(asset.n_dofs),
            source_collision_members=source_collision_members,
            loaded_collision_shape_count=len(asset.geoms),
            loaded_collision_shapes_merged=any(
                bool(getattr(geom.mesh, "metadata", {}).get("merged", False))
                for geom in asset.geoms
            ),
            loaded_collision_vertex_count=sum(int(geom.n_verts) for geom in asset.geoms),
            loaded_collision_face_count=sum(int(geom.n_faces) for geom in asset.geoms),
            dynamic=asset.n_dofs == 6,
            fixed=bool(asset.base_link.is_fixed),
            mass_kg=mass,
            inertia_kg_m2={
                "ixx": float(inertia[0, 0]),
                "ixy": float(inertia[0, 1]),
                "ixz": float(inertia[0, 2]),
                "iyy": float(inertia[1, 1]),
                "iyz": float(inertia[1, 2]),
                "izz": float(inertia[2, 2]),
            },
            friction=physics["friction"],
            loaded_collision_min_z_m=minimum_z,
            test_translation_m=translation,
        )

    def observe_initial(self) -> GenesisAssetBackendStep:
        scene, asset = self._required_scene()
        scene.rigid_solver.detect_collision()
        from genesis.utils.misc import qd_to_numpy

        counts = qd_to_numpy(scene.rigid_solver.collider._collider_state.n_contacts)
        if int(self._array(counts).reshape(-1)[0]) != 0:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_INITIAL_COLLISION",
                "the asset intersects the ground at the sealed release pose",
            )
        return self._state(asset, 0, ())

    def step(self, step_index: int) -> GenesisAssetBackendStep:
        if type(step_index) is not int or step_index != self._next_step:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "Genesis step requests must be contiguous",
            )
        scene, asset = self._required_scene()
        scene.step(update_visualizer=False, refresh_visualizer=False)
        contacts = self._contacts(scene.rigid_solver.collider.get_contacts(to_torch=False))
        self._next_step += 1
        return self._state(asset, step_index, contacts)

    def _prepare_runtime(self) -> None:
        if not self._source_root.is_dir():
            raise GenesisCpuBackendUnavailableError("the fixed Genesis source root is unavailable")
        for key, expected in GENESIS_CPU_PROCESS_ENV.items():
            actual = os.environ.get(key)
            if actual not in (None, expected):
                raise GenesisCpuBackendUnavailableError(f"process environment differs at {key}")
            os.environ[key] = expected
        try:
            actual_commit = subprocess.run(
                ["git", "-C", str(self._source_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise GenesisCpuBackendUnavailableError(
                "the Genesis source identity cannot be resolved"
            ) from error
        if actual_commit != GENESIS_WORLD_COMMIT:
            raise GenesisCpuBackendUnavailableError("the Genesis source commit has drifted")

    def _import_genesis(self) -> Any:
        source = str(self._source_root)
        if source not in sys.path:
            sys.path.insert(0, source)
        try:
            gs = importlib.import_module("genesis")
        except Exception as error:
            raise GenesisCpuBackendUnavailableError(
                "the Genesis runtime cannot be imported"
            ) from error
        module_path = Path(gs.__file__).resolve()
        if (
            not module_path.is_relative_to(self._source_root)
            or gs.__version__ != self.backend_version
        ):
            raise GenesisCpuBackendUnavailableError(
                "the imported Genesis runtime identity has drifted"
            )
        return gs

    def _required_scene(self) -> tuple[Any, Any]:
        if self._scene is None or self._asset is None:
            raise GenesisCpuBackendUnavailableError("the Genesis asset has not been loaded")
        return self._scene, self._asset

    def _contacts(self, raw: dict[str, Any]) -> tuple[GenesisAssetGroundContact, ...]:
        required = ("geom_a", "geom_b", "position", "normal", "penetration", "force")
        count = len(raw["geom_a"])
        if any(len(raw[key]) != count for key in required):
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "Genesis contact arrays have inconsistent lengths",
            )
        mask = self._array(raw.get("valid_mask", [True] * count)).reshape(-1)
        if mask.shape != (count,):
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "Genesis contact validity mask has the wrong shape",
            )
        result: list[GenesisAssetGroundContact] = []
        for index in range(count):
            if not bool(mask[index]):
                continue
            geom_a = int(raw["geom_a"][index])
            geom_b = int(raw["geom_b"][index])
            if geom_a in self._asset_geom_indices and geom_b == self._ground_geom_index:
                asset_geom = geom_a
                force = -self._array(raw["force"][index]).reshape(3)
                normal = -self._array(raw["normal"][index]).reshape(3)
            elif geom_b in self._asset_geom_indices and geom_a == self._ground_geom_index:
                asset_geom = geom_b
                force = self._array(raw["force"][index]).reshape(3)
                normal = self._array(raw["normal"][index]).reshape(3)
            else:
                raise GenesisAssetProbeError(
                    "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                    "Genesis reported a contact outside the asset-ground pair",
                )
            ground_force = -force
            normal_force = max(0.0, float(force @ normal))
            result.append(
                GenesisAssetGroundContact(
                    asset_collision_index=self._asset_geom_indices.index(asset_geom),
                    ground_collision_index=0,
                    position_m=self._vector(raw["position"][index]),
                    normal_on_asset=self._vector(normal),
                    force_on_asset_n=self._vector(force),
                    force_on_ground_n=self._vector(ground_force),
                    penetration_m=float(raw["penetration"][index]),
                    normal_force_n=normal_force,
                )
            )
        return tuple(result)

    def _state(
        self,
        asset: Any,
        step_index: int,
        contacts: tuple[GenesisAssetGroundContact, ...],
    ) -> GenesisAssetBackendStep:
        return GenesisAssetBackendStep(
            step_index=step_index,
            phase="initial" if step_index == 0 else "post_step",
            position_m=self._vector(asset.get_pos()),
            orientation_wxyz=self._quaternion(asset.get_quat()),
            linear_velocity_m_s=self._vector(asset.get_vel()),
            angular_velocity_rad_s=self._vector(asset.get_ang()),
            asset_ground_contacts=contacts,
        )

    @staticmethod
    def _read_physics(path: Path) -> dict[str, float]:
        try:
            value = json.loads(path.read_bytes())
            mass = value["mass_kg"]
            friction = value["friction"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "the staged physics metadata is invalid",
            ) from error
        if (
            type(mass) not in (int, float)
            or type(friction) not in (int, float)
            or not math.isfinite(mass)
            or not math.isfinite(friction)
            or mass <= 0
            or friction < 0
        ):
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "the staged physical parameters are invalid",
            )
        return {"mass_kg": float(mass), "friction": float(friction)}

    @staticmethod
    def _read_urdf_counts(path: Path) -> tuple[int, int]:
        try:
            root = ET.fromstring(path.read_bytes())
        except (OSError, ET.ParseError) as error:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "the staged URDF cannot be parsed",
            ) from error
        return len(root.findall("link")), len(root.findall("joint"))

    @staticmethod
    def _array(value: Any) -> Any:
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        import numpy as np

        return np.asarray(value)

    @classmethod
    def _scalar(cls, value: Any) -> float:
        return float(cls._array(value).reshape(-1)[0])

    @classmethod
    def _matrix(cls, value: Any) -> Any:
        matrices = cls._array(value).reshape(-1, 3, 3)
        if len(matrices) != 1:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "the Genesis asset must have one dynamic inertia tensor",
            )
        return matrices[0]

    @classmethod
    def _vector(cls, value: Any) -> tuple[float, float, float]:
        values = cls._array(value).reshape(3)
        return (float(values[0]), float(values[1]), float(values[2]))

    @classmethod
    def _quaternion(cls, value: Any) -> tuple[float, float, float, float]:
        values = cls._array(value).reshape(4)
        return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


__all__ = [
    "GENESIS_CPU_PROCESS_ENV",
    "GENESIS_WORLD_COMMIT",
    "GenesisCpuAssetProbeBackend",
    "GenesisCpuBackendUnavailableError",
]
