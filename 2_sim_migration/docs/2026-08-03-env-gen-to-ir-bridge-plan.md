# env-gen → openxsim IR 桥接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 openxsim 加一个一等 importer `import_env_gen`，把 env-gen 的 `resolved_scene.json` 转成合法 `EnvironmentPackage`，让 env-gen 场景能进 openxsim 现成 `transfer`。

**Architecture:** 新文件 `openxsim/env_gen.py` 提供 `import_env_gen(path)->EnvironmentPackage`；把 `resolved_scene.json` 当纯 dict 读（不 import scene_gen），映射物体/资产/工作区、复用 `_task_from_contract` 产 unbound 任务、`_valid_identifier` 消毒数字开头 id；在 `importers.py::import_environment` 加一行分派。全保真（关节/关系/来源入 IR），资产用 SAPIEN 表示、不转 USD。

**Tech Stack:** Python 3.10、openxsim（`agenticsim.openxsim`）、pytest。运行环境 conda `env-gen-yuxin`。

## Global Constraints
- **不 import env-gen 的 `scene_gen`**：`resolved_scene.json` 当外部 dict 读。
- **不改上游 env-gen**（`external/env-gen-github` 只读）；openxsim 是本项目引擎，可改。
- **不静默降级 / 不编造 / 标未知**：缺资产硬失败；mass/inertia/friction 标 `{"status":"unknown"}`。
- **位姿约定**：米 + wxyz 四元数（两边一致，直搬）。
- **PYTHONPATH**（跑测试/CLI 必需）：`openxsim/{source/agenticsim, deps/metasim_core, third_party/MetaSim}`。
- 路径基准：`OX=shared/openxsim`；包目录 `PKG=$OX/source/agenticsim/agenticsim/openxsim`。

## File Structure
- **Create** `$PKG/env_gen.py` — 导入器（本任务核心）。
- **Modify** `$PKG/importers.py` — `import_environment` 加分派分支。
- **Create** `$OX/tests/test_env_gen_import.py` — 单测。
- **Create** `$OX/tests/fixtures/env_gen/can_on_plate.resolved_scene.json` — 真实刚体场景 fixture（从复现产物拷贝）。
- **Modify** `2_sim_migration/README.md`、`2_sim_migration/docs/design 相关` — 注明 importer 落位（Task 5）。

---

### Task 1: importer 核心（刚体场景 → 合法 IR）

**Files:**
- Create: `$PKG/env_gen.py`
- Create: `$OX/tests/fixtures/env_gen/can_on_plate.resolved_scene.json`
- Test: `$OX/tests/test_env_gen_import.py`

**Interfaces:**
- Consumes: `ir.EnvironmentPackage/EnvSpec/SceneObject/AssetBundle/AssetRepresentation/Pose`；`importers._task_from_contract(contract, *, backend)->(TaskSpec,list)`、`importers._valid_identifier(value, prefix)->str`、`importers.EnvironmentImportError`。
- Produces: `env_gen.import_env_gen(path: str|Path) -> EnvironmentPackage`；`env_gen._is_env_gen(data: dict)->bool`。

- [ ] **Step 1: 准备 fixture**

```bash
OX=/home/jingxiang/yuxin/env-gen-dev/shared/openxsim
mkdir -p $OX/tests/fixtures/env_gen
cp /home/jingxiang/yuxin/env-gen-github/data/generated_scenes/place_a_can_on_top_of_a_plate_fe0b76e316/resolved_scene.json \
   $OX/tests/fixtures/env_gen/can_on_plate.resolved_scene.json
```
> fixture 里的 `source_files` 是绝对路径（RoboTwin 资产），在 lv-5090 上存在，故资产存在性检查会通过。

- [ ] **Step 2: 写失败测试**

```python
# $OX/tests/test_env_gen_import.py
from pathlib import Path
import pytest
from agenticsim.openxsim.env_gen import import_env_gen

FIX = Path(__file__).parent / "fixtures" / "env_gen" / "can_on_plate.resolved_scene.json"

def test_rigid_scene_maps_to_valid_ir():
    pkg = import_env_gen(FIX)
    pkg.validate()                                   # 不抛即合法
    assert pkg.package_id.startswith("place_a_can")
    assert len(pkg.env.objects) == 2
    ids = {o.instance_id for o in pkg.env.objects}
    assert ids == {"can_1", "plate_1"}
    can = next(o for o in pkg.env.objects if o.instance_id == "can_1")
    assert can.pose.position[0] == pytest.approx(-0.125948517)
    assert can.static is False
    assert can.asset_id.startswith("asset_071_can")   # 数字开头被消毒
    # 资产表示来自 .glb（按后缀取 format）
    asset = next(a for a in pkg.assets if a.asset_id == can.asset_id)
    rep = asset.representations[0]
    assert rep.backend == "sapien" and rep.format in {"glb", "obj", "dae", "stl", "urdf"}
    # 场景无任务 → unbound，但 instruction 保住 prompt
    assert pkg.task.instruction  # 非空
    assert any(c.get("type") == "unbound" for c in pkg.task.success)
```

- [ ] **Step 3: 跑测试确认失败**

Run:
```bash
cd $OX && PYTHONPATH="$OX/source/agenticsim:$OX/deps/metasim_core:$OX/third_party/MetaSim" \
  conda run -n env-gen-yuxin python -m pytest tests/test_env_gen_import.py -q
```
Expected: FAIL（`ModuleNotFoundError: agenticsim.openxsim.env_gen`）。

- [ ] **Step 4: 写 importer 实现**

```python
# $PKG/env_gen.py
"""Import env-gen resolved_scene.json into the Open-X-Sim IR (first-class importer)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .importers import EnvironmentImportError, _task_from_contract, _valid_identifier
from .ir import AssetBundle, AssetRepresentation, EnvSpec, EnvironmentPackage, Pose, SceneObject

_MESH_SUFFIXES = {"glb", "obj", "dae", "stl", "ply", "usd", "usda", "usdc"}


def _is_env_gen(data: dict[str, Any]) -> bool:
    return str(data.get("compiler_version", "")).startswith("scene_gen")


def _representation(load_type: str, source_files: list[str], base: Path) -> AssetRepresentation:
    def _resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (base / path).resolve()

    candidates = list(source_files)
    if load_type == "urdf":
        for f in candidates:
            if f.lower().endswith(".urdf"):
                return AssetRepresentation(format="urdf", uri=str(_resolve(f)), backend="sapien")
    for f in candidates:
        suffix = Path(f).suffix.lower().lstrip(".")
        if suffix in _MESH_SUFFIXES:
            return AssetRepresentation(format=suffix, uri=str(_resolve(f)), backend="sapien")
    raise EnvironmentImportError(f"no mesh/urdf representation in source_files: {source_files}")


def import_env_gen(path: str | Path) -> EnvironmentPackage:
    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentImportError(f"env-gen scene parse failed for {source}: {exc}") from exc
    if not _is_env_gen(data):
        raise EnvironmentImportError(
            f"not an env-gen resolved_scene (compiler_version={data.get('compiler_version')!r})"
        )

    ws = data.get("workspace") or {}
    x_lo, x_hi = (ws.get("x_bounds_m") or [-1.0, 1.0])
    y_lo, y_hi = (ws.get("y_bounds_m") or [-1.0, 1.0])
    table = float(ws.get("table_height_m", 0.0))
    workspace_bounds = (float(x_lo), float(y_lo), table, float(x_hi), float(y_hi), table + 0.5)

    objects: list[SceneObject] = []
    assets: list[AssetBundle] = []
    seen: set[str] = set()
    for index, obj in enumerate(data.get("objects") or []):
        instance_id = _valid_identifier(str(obj.get("object_id") or f"object_{index}"), "obj")
        asset_id = _valid_identifier(f"{obj.get('asset_id')}_m{obj.get('model_id', 0)}", "asset")
        pose = obj.get("pose") or {}
        objects.append(
            SceneObject(
                instance_id=instance_id,
                asset_id=asset_id,
                pose=Pose(
                    position=tuple(float(v) for v in (pose.get("position_m") or [0.0, 0.0, 0.0])),
                    orientation_wxyz=tuple(
                        float(v) for v in (pose.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0])
                    ),
                ),
                static=bool(obj.get("is_static", False)),
                scale=tuple(float(v) for v in (obj.get("mesh_scale") or [1.0, 1.0, 1.0])),
                metadata={
                    "category": obj.get("category"),
                    "color": obj.get("color"),
                    "material": obj.get("material"),
                    "support_relation": obj.get("support_relation"),
                    "support_target": obj.get("support_target"),
                    "grounding_score": obj.get("grounding_score"),
                },
            )
        )
        if asset_id in seen:
            continue
        seen.add(asset_id)
        joints = list(obj.get("articulation_joint_names") or [])
        assets.append(
            AssetBundle(
                asset_id=asset_id,
                category=str(obj.get("category") or "object"),
                representations=(
                    _representation(
                        str(obj.get("load_type") or "rigid"),
                        list(obj.get("source_files") or []),
                        source.parent,
                    ),
                ),
                physical={
                    "dimensions_m": obj.get("dimensions_m"),
                    "mass_kg": {"status": "unknown"},
                    "inertia": {"status": "unknown"},
                    "friction": {"status": "unknown"},
                },
                articulation=(
                    {
                        "joint_names": joints,
                        "joint_limits": list(obj.get("articulation_joint_limits") or []),
                        "qpos": list(obj.get("articulation_qpos") or []),
                    }
                    if joints
                    else {}
                ),
                source={
                    "kind": "env_gen",
                    "asset_id": obj.get("asset_id"),
                    "model_id": obj.get("model_id"),
                    "asset_provenance": obj.get("asset_provenance"),
                    "source_files": list(obj.get("source_files") or []),
                },
                tags=tuple(t for t in (obj.get("color"), obj.get("material")) if t),
            )
        )

    task, limitations = _task_from_contract(None, backend="env_gen")
    task = replace(
        task,
        instruction=str(data.get("request") or task.instruction),
        intent="env_gen_scene_import",
    )
    name = _valid_identifier(str(data.get("scene_id") or source.stem), "env_gen")
    package = EnvironmentPackage(
        package_id=name,
        env=EnvSpec(
            name=name,
            objects=tuple(objects),
            gravity_mps2=(0.0, 0.0, -9.81),
            workspace_bounds_m=workspace_bounds,
            metadata={
                "request": data.get("request"),
                "seed": data.get("seed"),
                "compiler_version": data.get("compiler_version"),
                "source_scene_spec_sha256": data.get("source_scene_spec_sha256"),
                "asset_catalog_sha256": data.get("asset_catalog_sha256"),
                "relations": data.get("relations") or [],
            },
        ),
        assets=tuple(assets),
        task=task,
        source={
            "mode": "existing_environment_import",
            "backend": "env_gen",
            "path": str(source),
            "limitations": limitations,
        },
        target_backends=("robotwin",),
    )
    package.validate()
    return package
```

- [ ] **Step 5: 跑测试确认通过**

Run: 同 Step 3。 Expected: PASS（1 passed）。

- [ ] **Step 6: Commit**

```bash
cd /home/jingxiang/yuxin/env-gen-dev
git add shared/openxsim/source shared/openxsim/tests
git commit -m "feat(2_sim_migration): env_gen importer core (rigid scene -> IR)"
```

---

### Task 2: 保真度 —— 关节 / 未知物理 / 血缘 / relations

**Files:**
- Test: `$OX/tests/test_env_gen_import.py`（追加）

**Interfaces:** Consumes Task 1 的 `import_env_gen`。（无新产出——本任务锁死 Task 1 已写的保真字段行为，用断言固定契约。）

- [ ] **Step 1: 追加保真断言测试**

```python
def test_fidelity_unknown_physics_and_provenance_and_relations():
    pkg = import_env_gen(FIX)
    asset = pkg.assets[0]
    # 未知物理显式标记，不编造
    assert asset.physical["mass_kg"] == {"status": "unknown"}
    assert asset.physical["inertia"] == {"status": "unknown"}
    assert asset.physical["dimensions_m"] is not None
    # 血缘入 IR
    assert asset.source["kind"] == "env_gen"
    assert asset.source["asset_provenance"] == "robotwin_catalog"
    # relations 作为数据带入 env.metadata（本任务不合成 task）
    rels = pkg.env.metadata["relations"]
    assert {r["relation"] for r in rels} == {"on_table", "on_top_of"}
    # env-gen 溯源哈希带入
    assert pkg.env.metadata["source_scene_spec_sha256"]
    assert pkg.env.metadata["compiler_version"].startswith("scene_gen")

def test_rigid_asset_has_empty_articulation():
    pkg = import_env_gen(FIX)
    assert all(a.articulation == {} for a in pkg.assets)   # can/plate 无关节
```

- [ ] **Step 2: 跑测试确认通过**（Task 1 实现已覆盖这些字段）

Run: `... python -m pytest tests/test_env_gen_import.py -q`  Expected: PASS（3 passed）。
> 若失败，说明 Task 1 的保真字段有缺，回 Task 1 补齐再来。

- [ ] **Step 3: Commit**

```bash
git add shared/openxsim/tests
git commit -m "test(2_sim_migration): lock env_gen importer fidelity (unknown physics, provenance, relations)"
```

---

### Task 3: 错误处理（缺资产 / 非 env-gen / 坏 JSON）

**Files:**
- Test: `$OX/tests/test_env_gen_import.py`（追加）
- Create: `$OX/tests/fixtures/env_gen/missing_asset.resolved_scene.json`

**Interfaces:** Consumes `import_env_gen`、`importers.EnvironmentImportError`。

- [ ] **Step 1: 造"缺资产" fixture**

```bash
OX=/home/jingxiang/yuxin/env-gen-dev/shared/openxsim
python3 - <<'PY'
import json, pathlib
base = pathlib.Path("$OX/tests/fixtures/env_gen/can_on_plate.resolved_scene.json".replace("$OX","/home/jingxiang/yuxin/env-gen-dev/shared/openxsim"))
d = json.loads(base.read_text())
d["objects"][0]["source_files"] = ["/nonexistent/missing.glb"]
out = base.with_name("missing_asset.resolved_scene.json")
out.write_text(json.dumps(d, indent=2))
print("wrote", out)
PY
```

- [ ] **Step 2: 写错误处理测试**

```python
def test_missing_asset_file_raises():
    from agenticsim.openxsim.importers import EnvironmentImportError
    bad = FIX.with_name("missing_asset.resolved_scene.json")
    with pytest.raises(EnvironmentImportError):
        import_env_gen(bad)

def test_non_env_gen_input_raises(tmp_path):
    from agenticsim.openxsim.importers import EnvironmentImportError
    p = tmp_path / "other.json"
    p.write_text('{"compiler_version": "something_else", "objects": []}')
    with pytest.raises(EnvironmentImportError):
        import_env_gen(p)

def test_malformed_json_raises(tmp_path):
    from agenticsim.openxsim.importers import EnvironmentImportError
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(EnvironmentImportError):
        import_env_gen(p)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `... python -m pytest tests/test_env_gen_import.py -k "raises" -q`
Expected: `test_missing_asset_file_raises` 失败——因为当前 `_representation` 用后缀选中了 `/nonexistent/missing.glb` 但**未检查文件存在**。

- [ ] **Step 4: 补"资产存在性硬检查"**

在 `$PKG/env_gen.py` 的 `_representation`，把命中的 mesh/urdf 分支改成"解析后校验存在"：

```python
def _representation(load_type: str, source_files: list[str], base: Path) -> AssetRepresentation:
    def _resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (base / path).resolve()

    def _make(fmt: str, resolved: Path) -> AssetRepresentation:
        if not resolved.exists():
            raise EnvironmentImportError(f"env-gen asset file missing: {resolved}")
        return AssetRepresentation(format=fmt, uri=str(resolved), backend="sapien")

    if load_type == "urdf":
        for f in source_files:
            if f.lower().endswith(".urdf"):
                return _make("urdf", _resolve(f))
    for f in source_files:
        suffix = Path(f).suffix.lower().lstrip(".")
        if suffix in _MESH_SUFFIXES:
            return _make(suffix, _resolve(f))
    raise EnvironmentImportError(f"no mesh/urdf representation in source_files: {source_files}")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `... python -m pytest tests/test_env_gen_import.py -q`  Expected: PASS（6 passed）。

- [ ] **Step 6: Commit**

```bash
git add shared/openxsim/source shared/openxsim/tests
git commit -m "feat(2_sim_migration): env_gen importer error handling (missing asset / non-env-gen / bad json)"
```

---

### Task 4: 分派器接线 + 确定性

**Files:**
- Modify: `$PKG/importers.py`（`import_environment` 内加分支）
- Test: `$OX/tests/test_env_gen_import.py`（追加）

**Interfaces:** Consumes `env_gen.import_env_gen`、`env_gen._is_env_gen`。Produces：`import_environment(path, source_backend="env_gen")` 与自动识别路由到 `import_env_gen`。

- [ ] **Step 1: 写分派 + 确定性测试**

```python
def test_dispatcher_routes_env_gen():
    from agenticsim.openxsim.importers import import_environment
    pkg = import_environment(FIX)                      # 自动识别
    assert pkg.source["backend"] == "env_gen"
    pkg2 = import_environment(FIX, source_backend="env_gen")  # 显式
    assert pkg2.package_id == pkg.package_id

def test_determinism_same_input_same_digest():
    assert import_env_gen(FIX).digest() == import_env_gen(FIX).digest()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `... python -m pytest tests/test_env_gen_import.py -k "dispatcher or determinism" -q`
Expected: `test_dispatcher_routes_env_gen` 失败（`import_environment` 还不认 env-gen）。

- [ ] **Step 3: 在 `import_environment` 加分派分支**

打开 `$PKG/importers.py`，在 `import_environment` 读到 `data` 后、其它 schema 判断**之前**插入：

```python
    # env-gen (robot-harness-gen-env) resolved_scene.json
    from .env_gen import _is_env_gen, import_env_gen  # 局部 import 避免循环
    if source_backend == "env_gen" or _is_env_gen(data):
        return import_env_gen(source)
```
> 放在 `import_environment` 里 `data = json.loads(...)` 之后。`source` 为该函数已解析的路径变量（若变量名不同，用其对应的已解析 Path）。

- [ ] **Step 4: 跑测试确认通过**

Run: `... python -m pytest tests/test_env_gen_import.py -q`  Expected: PASS（8 passed）。

- [ ] **Step 5: 全量回归（不破坏 openxsim 原有 36 测试）**

Run: `... python -m pytest tests -q`  Expected: PASS（≥ 36 + 8）。

- [ ] **Step 6: Commit**

```bash
git add shared/openxsim/source shared/openxsim/tests
git commit -m "feat(2_sim_migration): wire env_gen into import_environment dispatcher"
```

---

### Task 5: 端到端（DoD）+ 文档

**Files:**
- Test: `$OX/tests/test_env_gen_transfer_e2e.py`
- Modify: `2_sim_migration/README.md`、`2_sim_migration/docs/design.md`（若存在则同步；否则更新项目 `docs/design.md` 的 task2 段）

**Interfaces:** Consumes `pipeline.OpenXSimPipeline` / `backends`（openxsim 现成）。

- [ ] **Step 1: 写端到端测试（RoboTwin 顺 + Isaac 如实报 blocker）**

```python
# $OX/tests/test_env_gen_transfer_e2e.py
from pathlib import Path
from agenticsim.openxsim.importers import import_environment
from agenticsim.openxsim.backends import IsaacSimCompiler

FIX = Path(__file__).parent / "fixtures" / "env_gen" / "can_on_plate.resolved_scene.json"

def test_env_gen_ir_compiles_to_isaac_reports_missing_usd(tmp_path):
    pkg = import_environment(FIX)
    result = IsaacSimCompiler().compile(pkg, tmp_path / "isaac")   # 见下方签名说明
    # 网格资产无 USD → 明确 blocker，不崩、不静默
    assert any("USD" in b for b in result.blockers)
```
> ⚠️ `IsaacSimCompiler` 的编译方法名/返回结构以其 `backends.py` 实际为准（Task 5 Step 2 先核对：`grep -n "def compile\|class IsaacSimCompiler\|blockers" $PKG/backends.py`），据此调整调用与断言字段名，再跑。RoboTwin 顺路径可用 `robotwin.write_robotwin_bundle(pkg, out)` 冒烟（产出 bundle 不抛即通过）。

- [ ] **Step 2: 核对编译器真实签名并使测试通过**

```bash
grep -n "def compile\|class IsaacSimCompiler\|blockers\|class BackendCompiler" $PKG/backends.py | head
```
据实修正 Step 1 的调用/断言，跑：`... python -m pytest tests/test_env_gen_transfer_e2e.py -q`  Expected: PASS。

- [ ] **Step 3: 更新文档**

在 `2_sim_migration/README.md` 的"里面有什么"表补一行：
```markdown
| `openxsim/source/agenticsim/agenticsim/openxsim/env_gen.py` | env-gen `resolved_scene.json` → IR 的一等 importer（本项目新增） |
```
在项目 `docs/design.md` 的 task2 段把"待做：桥接"改为"已实现：`import_env_gen` + dispatcher，端到端跑通到 RoboTwin；Isaac 网格待资产 USD 转换"。

- [ ] **Step 4: 全量回归 + Commit**

```bash
cd $OX && PYTHONPATH="$OX/source/agenticsim:$OX/deps/metasim_core:$OX/third_party/MetaSim" \
  conda run -n env-gen-yuxin python -m pytest tests -q     # 全绿
cd /home/jingxiang/yuxin/env-gen-dev
git add 2_sim_migration
git commit -m "test(2_sim_migration): env_gen->IR->transfer e2e + docs"
git push origin main
```

---

## Self-Review

**1. Spec coverage** — 逐条对 spec：范围/边界(Task1,5)、数据流(Task1,5)、组件落位(Task1,4)、字段映射(Task1,2)、任务 unbound+instruction(Task1)、错误处理含数字 id/缺资产/未知物理(Task1,3)、测试与 DoD(Task2,4,5)。均有对应任务。✅
**2. Placeholder scan** — 无 TBD/TODO；唯一"以实际为准"是 Task5 的 `IsaacSimCompiler` 签名，已给核对命令与调整步骤（因该 API 未在本 spec 冻结，属合理的实测校准，非占位）。
**3. Type consistency** — `import_env_gen`、`_is_env_gen`、`_representation`、`_task_from_contract`、`_valid_identifier`、`replace(task, ...)` 全程一致；asset_id 消毒规则 `asset_{orig}_m{model_id}` 在 object 与 asset 两处同源生成、引用一致。
