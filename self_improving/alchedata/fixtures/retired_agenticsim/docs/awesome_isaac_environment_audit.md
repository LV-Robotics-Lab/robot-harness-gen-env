# Awesome Isaac Sim Source Audit

[COMPUTED] [CONFIDENCE: HIGH] This audit separates public links, detected licenses, static environment candidates, AgenticSim source integration, and prior strict execution evidence.

- Repositories normalized: `745`
- GitHub-detected open-source licenses: `308`
- Documented current-Isaac environment sources: `84`
- Static intake candidates after content audit: `64`
- AgenticSim submodule repositories: `42`
- Existing AgenticSim strict-evidence rows: `12`
- Current RTX 5090 candidate runtime probes: `12` (`11` pass, `1` blocked)
- Academic-use runtime admissions: `11`; strict open-source closures: `6`; runtime passes with license advisories: `5`
- Blocked from intake: `439`

`static_open_environment_candidate` is not execution proof. It means public source, a detected recognized open-source license, a default branch, recent activity, current Isaac backend evidence, environment/task source paths, and documented install/run entry points.

## Current RTX 5090 Runtime Baseline

- Host/GPU: `NVIDIA GeForce RTX 5090, 580.159.03, 32607 MiB`
- Isaac Sim: `5.1.0.0`
- Gates: `{'physics': True, 'render': True, 'torch': True, 'video': True}`
- Continuous video: `32` frames, `32` unique hashes

## Current Candidate Runtime Probes

| Repository | Exact task | Runtime | Academic use | License advisory | Steps | Render | Conditions / blocker |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| [fan-ziqi/robot_lab](https://github.com/fan-ziqi/robot_lab) | `RobotLab-Isaac-Velocity-Flat-Unitree-Go2-v0` | `passed_after_declared_dependency_install` | `accepted_noncommercial_academic_local_use` | `none` | 20 | `True` | Install the declared but unpinned cusrl dependency; the bounded probe used cusrl==1.2.0 without the all extra. |
| [unitreerobotics/unitree_rl_lab](https://github.com/unitreerobotics/unitree_rl_lab) | `Unitree-Go2-Velocity` | `passed_with_external_asset_dataset` | `accepted_noncommercial_academic_local_use` | `required_asset_license_unverified` | 20 | `True` | Download the public, non-gated unitreerobotics/unitree_model dataset and configure UNITREE_MODEL_DIR.; Materialize the Go2 Git LFS object before task creation.; Frame the camera from robot.data.root_pos_w because terrain placement moved env_0 about 79 meters from the authored USD origin. |
| [lehome-official/lehome-challenge](https://github.com/lehome-official/lehome-challenge) | `LeHome-BiSO101-Direct-Garment-v2` | `passed_after_compatibility_patch` | `accepted_noncommercial_academic_local_use` | `none` | 5 | `True` | Download and materialize the public Apache-2.0 lehome/asset_challenge dataset.; Install plotly==6.5.2, pyserial==3.5, and deepdiff==8.6.1 without the full lerobot dependency set.; Set cfg.garment_name=Top_Long_Unseen_0 before environment creation.; Apply the recorded two-line GarmentObject first-reset compatibility patch. |
| [abmoRobotics/RLRoverLab](https://github.com/abmoRobotics/RLRoverLab) | `Exomy-v0` | `passed_after_declared_dependencies_and_external_terrain_archive` | `accepted_noncommercial_academic_local_use` | `required_asset_license_unverified` | 5 | `True` | Install pymeshlab==2025.7.post1, termcolor==3.3.0, and gdown==6.1.0.; Download the documented Google Drive terrain archive and verify SHA-256 792dea9a62d2e5a339fe4faddf7eb6597e63c5a805145c88a7bf5be7889358fc.; Extract the 62 required object and terrain files; the actual and expected manifests must match exactly. |
| [enactic/openarm_isaac_lab](https://github.com/enactic/openarm_isaac_lab) | `Isaac-Reach-OpenArm-Play-v0` | `passed_with_repository_root_pythonpath` | `accepted_noncommercial_academic_local_use` | `none` | 20 | `True` | Install source/openarm as an editable package.; Add the repository root to PYTHONPATH because candidate modules import source.openarm paths. |
| [iit-DLSLab/basic-locomotion-dls-isaaclab](https://github.com/iit-DLSLab/basic-locomotion-isaaclab) | `Locomotion-Go2-Flat` | `passed_after_compatibility_patch` | `accepted_noncommercial_academic_local_use` | `none` | 10 | `True` | Install the repository package and its declared Isaac Lab dependencies.; Apply the recorded import-only aliases for the absent project-specific MultiMeshRayCasterCamera classes on stock Isaac Lab 2.3.x. |
| [liorbenhorin/lerobot_so101_teleop](https://github.com/liorbenhorin/lerobot_so101_teleop) | `Lerobot-So101-Teleop-Base` | `passed_after_git_lfs_materialization` | `accepted_noncommercial_academic_local_use` | `required_asset_license_unverified` | 5 | `True` | Materialize all 13 Git LFS assets and add the repository source directory to PYTHONPATH.; Use the task-native viewer pose; a generic wide pose did not frame the arm. |
| [AccelerationConsortium/Matterix](https://github.com/AccelerationConsortium/Matterix) | `Matterix-Test-Beakers-Franka-v1` | `passed_after_asset_submodule_materialization_and_path_configuration` | `accepted_noncommercial_academic_local_use` | `required_asset_license_unverified` | 10 | `True` | Initialize Matterix_assets at the recorded commit and materialize all 14 Git LFS objects.; Set MATTERIX_PATH to the repository root before importing Matterix packages. |
| [iit-DLSLab/simple-joints-identification-isaaclab](https://github.com/iit-DLSLab/sim2real-robot-identification) | `IsaacLab-Pace-Go2` | `passed_after_declared_dependency_and_submodule_checkout` | `accepted_noncommercial_academic_local_use` | `none` | 10 | `True` | Initialize the pace-sim2real submodule at the recorded Apache-2.0 commit.; Install the declared cmaes dependency; the probe used cmaes==0.13.0. |
| [Rui-li023/LabUtopia](https://github.com/Rui-li023/LabUtopia) | `level1_pick` | `passed_with_minimal_dependency_overlay_and_bounded_external_harness` | `accepted_noncommercial_academic_local_use` | `noncommercial_asset_terms_apply` | 10 | `True` | Materialize 472 Git LFS objects; the retained assets manifest contains 471 files.; Provide zarr==3.1.5, numcodecs==0.16.5, donfig==0.8.1.post1, and google-crc32c==1.7.1 in an isolated overlay without replacing the baseline Torch stack.; Use the bounded AgenticSim harness for reset, ten task steps, three native camera frames, and clean shutdown without starting the data-writer controller. |
| [neuromeka-robotics/nrmk_isaaclab_public](https://github.com/neuromeka-robotics/nrmk_isaaclab_public) | `Indy-Deploy` | `passed_after_dependency_and_asset_repair` | `accepted_noncommercial_academic_local_use` | `none` | 20 | `True` | Set PYNPUT_BACKEND=dummy for headless execution.; Install the undeclared runtime dependencies tensordict and rsl-rl-lib==3.0.1.; Materialize the Indy7 Git LFS object before task creation. |
| [noxrick91/WobbleGo](https://github.com/noxrick91/WobbleGo) | `WobbleGo-Direct-v0` | `blocked_external_core_asset_unavailable` | `blocked_runtime_failure` | `not_evaluated_runtime_blocked` | 0 | `False` | The core WobbleGo USD is not present in the repository and the configured remote URL did not resolve in Isaac Sim. |

## Source List Audit

| List | Commit | Unique repositories | Detected license | Reuse status |
| --- | --- | ---: | --- | --- |
| [shaoxiang/awesome-isaac-sim](https://github.com/shaoxiang/awesome-isaac-sim) | `7c42a15f4a28` | 705 | `None` | `blocked_no_detected_license` |
| [sjtuyinjie/awesome-isaac-sim](https://github.com/sjtuyinjie/awesome-isaac-sim) | `be3e8f1972a7` | 28 | `MIT` | `verified_open_source` |

## Existing AgenticSim Strict Evidence

| Repository | License | Existing key | Existing visual evidence |
| --- | --- | --- | --- |
| [isaac-sim/IsaacLab](https://github.com/isaac-sim/IsaacLab) | `BSD-3-Clause` | `isaaclab` | `smoke_screenshot_captured` |
| [InternRobotics/InternUtopia](https://github.com/InternRobotics/InternUtopia) | `MIT` | `internutopia` | `smoke_screenshot_captured` |
| [isaac-sim/OmniIsaacGymEnvs](https://github.com/isaac-sim/OmniIsaacGymEnvs) | `NOASSERTION` | `omniisaacgymenvs` | `smoke_screenshot_captured` |
| [btx0424/OmniDrones](https://github.com/btx0424/OmniDrones) | `MIT` | `omnidrones` | `smoke_screenshot_captured` |
| [isaac-sim/IsaacLab-Arena](https://github.com/isaac-sim/IsaacLab-Arena) | `NOASSERTION` | `isaaclab_arena` | `smoke_screenshot_captured` |
| [NVLabs/RoboLab](https://github.com/NVlabs/RoboLab) | `Apache-2.0` | `robolab` | `full_replay_captured_531_of_531` |
| [yang-zj1026/NaVILA-Bench](https://github.com/yang-zj1026/NaVILA-Bench) | `NOASSERTION` | `navila_bench` | `smoke_screenshot_captured` |
| [MuammerBay/isaac_so_arm101](https://github.com/MuammerBay/isaac_so_arm101) | `BSD-3-Clause` | `isaac_so_arm101` | `smoke_screenshot_captured` |
| [MuammerBay/IsaacLab-SO_100](https://github.com/MuammerBay/isaac_so_arm101) | `BSD-3-Clause` | `isaaclab_so_100` | `smoke_screenshot_captured` |
| [isaac-for-healthcare/i4h-workflows](https://github.com/isaac-for-healthcare/i4h-workflows) | `Apache-2.0` | `i4h_workflows` | `smoke_screenshot_captured` |
| [isaac-sim/IsaacLabEvalTasks](https://github.com/isaac-sim/IsaacLabEvalTasks) | `Apache-2.0` | `isaaclabevaltasks` | `smoke_screenshot_captured` |
| [ALRhub/Orbit](https://github.com/ALRhub/Orbit) | `NOASSERTION` | `orbit` | `smoke_screenshot_captured` |

## Highest-signal Intake Candidates

| Repository | License | Intake | Gates | Stars | Source lists |
| --- | --- | --- | --- | ---: | --- |
| [RoboTwin-Platform/RoboTwin](https://github.com/RoboTwin-Platform/RoboTwin) | `MIT` | `integrated_source_pending_strict_runtime` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 2562 | `agenticsim_submodule` |
| [Zhefan-Xu/NavRL](https://github.com/Zhefan-Xu/NavRL) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 1537 | `shaoxiang` |
| [amazon-far/holosoma](https://github.com/amazon-far/holosoma) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 1492 | `shaoxiang` |
| [abizovnuralem/go2_omniverse](https://github.com/abizovnuralem/go2_omniverse) | `BSD-2-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 1041 | `shaoxiang, sjtuyinjie` |
| [NVlabs/HOVER](https://github.com/NVlabs/HOVER) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 752 | `shaoxiang` |
| [leggedrobotics/pace-sim2real](https://github.com/leggedrobotics/pace-sim2real) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 581 | `shaoxiang` |
| [LeCAR-Lab/HumanoidVerse](https://github.com/LeCAR-Lab/HumanoidVerse) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 462 | `shaoxiang` |
| [linden713/humanoid_amp](https://github.com/linden713/humanoid_amp) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 455 | `shaoxiang` |
| [yang-zj1026/legged-loco](https://github.com/yang-zj1026/legged-loco) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 441 | `shaoxiang` |
| [jaykorea/Isaac-RL-Two-wheel-Legged-Bot](https://github.com/jaykorea/Isaac-RL-Two-wheel-Legged-Bot) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 322 | `shaoxiang` |
| [NathanWu7/isaacLab.manipulation](https://github.com/NathanWu7/isaacLab.manipulation) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 310 | `shaoxiang` |
| [louislelay/kinova_isaaclab_sim2real](https://github.com/louislelay/kinova_isaaclab_sim2real) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 222 | `shaoxiang` |
| [kousheekc/isaac_drone_racer](https://github.com/kousheekc/isaac_drone_racer) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 186 | `shaoxiang` |
| [Marine-RL/MarineGym](https://github.com/Marine-RL/MarineGym) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 179 | `shaoxiang` |
| [mekion/the-bimo-project](https://github.com/mekion/the-bimo-project) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 160 | `shaoxiang` |
| [SamuelSchmidgall/SurgicalGym](https://github.com/SamuelSchmidgall/SurgicalGym) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 104 | `shaoxiang` |
| [TIERS/isaac-marl-mobile-manipulation](https://github.com/TIERS/isaac-marl-mobile-manipulation) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 104 | `shaoxiang` |
| [leggedrobotics/sru-navigation-sim](https://github.com/leggedrobotics/sru-navigation-sim) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 92 | `shaoxiang` |
| [Andy-xiong6/bipedal_locomotion_isaaclab](https://github.com/Andy-xiong6/bipedal_locomotion_isaaclab) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 87 | `shaoxiang` |
| [Kuriharamio/RC2026_SIM](https://github.com/Kuriharamio/RC2026_SIM) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 68 | `shaoxiang` |
| [linchangyi1/LocoTouch](https://github.com/linchangyi1/LocoTouch) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 58 | `shaoxiang` |
| [felipemohr/IsaacLab-Quadruped-Tasks](https://github.com/felipemohr/IsaacLab-Quadruped-Tasks) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 54 | `shaoxiang` |
| [BDX-R/BDX-R-IsaacLab](https://github.com/BDX-R/BDX-R-IsaacLab) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 47 | `shaoxiang` |
| [embodied-dobot/x-trainer](https://github.com/embodied-dobot/x-trainer) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_assets_require_review` | 44 | `shaoxiang` |
| [TokyoRobotics/torobo_isaac_lab](https://github.com/TokyoRobotics/torobo_isaac_lab) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 37 | `shaoxiang` |
| [humphreymunn/GCR-PPO](https://github.com/humphreymunn/GCR-PPO) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 36 | `shaoxiang` |
| [wenconggan/whole-body-mimic-lab](https://github.com/wenconggan/whole-body-mimic-lab) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 34 | `shaoxiang` |
| [flexivrobotics/isaac_sim_ws](https://github.com/flexivrobotics/isaac_sim_ws) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 31 | `shaoxiang` |
| [pietrodardano/RL_Dog](https://github.com/pietrodardano/RL_Dog) | `GPL-3.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 29 | `shaoxiang` |
| [AuTURBO/StrideSim](https://github.com/AuTURBO/StrideSim) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 25 | `shaoxiang` |
| [uiseoklee/Isaaclab-Gripper-Drone-Pickplace](https://github.com/uiseoklee/Isaaclab-Gripper-Drone-Pickplace) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 23 | `shaoxiang` |
| [yobel-sungkooklee/extreme-quadruped-parkour](https://github.com/yobel-sungkooklee/extreme-quadruped-parkour) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 23 | `shaoxiang` |
| [hucebot/isaaclab_kangaroo](https://github.com/hucebot/isaaclab_kangaroo) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 21 | `shaoxiang` |
| [BBBig-z/Isaaclab-arm-learning](https://github.com/BBBig-z/Isaaclab-arm-learning) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 20 | `shaoxiang` |
| [dyumanaditya/isaac-quad-loco](https://github.com/dyumanaditya/isaac-quad-loco) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 15 | `shaoxiang` |
| [benoit-robotics/bdx_walk_rl](https://github.com/benoit-robotics/bdx_walk_rl) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 12 | `shaoxiang` |
| [360ZMEM/EasyUUV-Isaac-Simulation](https://github.com/360ZMEM/EasyUUV-Isaac-Simulation) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 9 | `shaoxiang` |
| [ntnu-arl/olympus_lab](https://github.com/ntnu-arl/olympus_lab) | `BSD-3-Clause` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 9 | `shaoxiang` |
| [dorado-daniel/RLxUSD](https://github.com/dorado-daniel/RLxUSD) | `MIT` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 5 | `shaoxiang` |
| [ManggoF/ur5_isaaclab](https://github.com/ManggoF/ur5_isaaclab) | `Apache-2.0` | `eligible_after_dependency_review` | `isaac_sim_5_1_compatibility_not_declared, external_asset_scan_incomplete` | 5 | `shaoxiang` |

## Gate Counts

- Open-source status: `{'blocked_no_detected_license': 412, 'blocked_private': 2, 'blocked_unavailable': 23, 'verified_open_source': 308}`
- Static usability: `{'blocked_archived_or_disabled': 12, 'blocked_no_detected_license': 406, 'blocked_private': 2, 'blocked_unavailable': 23, 'legacy_isaac_gym_environment_source': 3, 'open_but_not_environment_candidate': 179, 'open_framework_or_simulator': 1, 'static_documented_isaac_source_role_review': 20, 'static_environment_source_missing_runbook': 35, 'static_open_environment_candidate': 64}`
- AgenticSim intake: `{'blocked_from_intake': 439, 'current_runtime_blocked': 1, 'current_runtime_passed_with_conditions': 11, 'eligible_after_dependency_review': 48, 'existing_agenticsim_strict_evidence': 12, 'integrated_source_pending_strict_runtime': 2, 'reference_or_manual_review': 232}`

The full per-repository evidence and source-line provenance are stored in `docs/awesome_isaac_environment_catalog.json`.
