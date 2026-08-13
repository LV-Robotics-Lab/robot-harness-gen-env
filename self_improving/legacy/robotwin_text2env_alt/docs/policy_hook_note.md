# Policy Train/Eval Hook

Updated: 2026-06-26 Asia/Shanghai

Task: `move_object_between_zones`

Goal: prove that the Text2Env-generated RoboTwin2 task can connect to a policy training/evaluation pipeline. This is not a quality training run; it is a hook/sanity check.

## Selected Policy

For the first hook, use RoboTwin2's built-in ACT policy because it is lighter than Pi0/OpenVLA-OFT and already consumes RoboTwin HDF5 demonstrations through `policy/ACT/process_data.py`.

RoboTwin policy paths:

```text
~/RoboTwin/policy/ACT/process_data.sh
~/RoboTwin/policy/ACT/imitate_episodes.py
~/RoboTwin/policy/ACT/eval.sh
~/RoboTwin/script/eval_policy.py
```

## Data Flow

```text
RoboTwin collect_data output
  data/move_object_between_zones/demo_policy_hook/data/episode*.hdf5

ACT preprocessing
  policy/ACT/processed_data/sim-move_object_between_zones/demo_policy_hook-2/episode_*.hdf5

ACT smoke checkpoint
  policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke/
```

Field mapping used by ACT:

```text
joint_action/left_arm + left_gripper + right_arm + right_gripper -> observations/qpos and action
observation/head_camera/rgb -> observations/images/cam_high
observation/right_camera/rgb -> observations/images/cam_right_wrist
observation/left_camera/rgb -> observations/images/cam_left_wrist
```

## Commands Run On 5090

Extra ACT dependencies installed into the existing `RoboTwin` conda environment:

```bash
python -m pip install einops==0.6.0 dm_control==1.0.9 mujoco==2.3.3
```

Create a two-episode policy hook task config:

```bash
cd ~/RoboTwin
cp task_config/demo_smoke.yml task_config/demo_policy_hook.yml
perl -0pi -e "s/episode_num: 1/episode_num: 2/" task_config/demo_policy_hook.yml
```

Collect two expert episodes:

```bash
cd ~/RoboTwin
source ~/miniconda3/etc/profile.d/conda.sh
conda activate RoboTwin
timeout 1200 bash collect_data.sh move_object_between_zones demo_policy_hook 0 \
  2>&1 | tee install_logs/policy_hook_collect_move_object_between_zones_2episodes.log
```

Preprocess for ACT:

```bash
cd ~/RoboTwin/policy/ACT
timeout 1200 bash process_data.sh move_object_between_zones demo_policy_hook 2 \
  2>&1 | tee ../../install_logs/policy_act_process_data_move_object_between_zones_demo_policy_hook_2.log
```

Run a one-epoch ACT smoke train:

```bash
cd ~/RoboTwin/policy/ACT
timeout 900 python3 imitate_episodes.py \
  --task_name sim-move_object_between_zones-demo_policy_hook-2 \
  --ckpt_dir ./act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke \
  --policy_class ACT \
  --kl_weight 10 \
  --chunk_size 20 \
  --hidden_dim 128 \
  --batch_size 1 \
  --dim_feedforward 512 \
  --num_epochs 1 \
  --lr 1e-5 \
  --save_freq 1 \
  --state_dim 14 \
  --seed 0 \
  2>&1 | tee ../../install_logs/policy_act_train_hook_move_object_between_zones_demo_policy_hook_2.log
```

Start the eval hook with matching lightweight ACT hyperparameters:

```bash
cd ~/RoboTwin
cp policy/ACT/deploy_policy.yml policy/ACT/deploy_policy_demo_policy_hook.yml
perl -0pi -e "s/chunk_size: 50/chunk_size: 20/; s/hidden_dim: 512/hidden_dim: 128/; s/dim_feedforward: 3200/dim_feedforward: 512/" \
  policy/ACT/deploy_policy_demo_policy_hook.yml

timeout 180 python script/eval_policy.py \
  --config policy/ACT/deploy_policy_demo_policy_hook.yml \
  --overrides \
  --task_name move_object_between_zones \
  --task_config demo_policy_hook \
  --ckpt_setting demo_policy_hook-2-smoke \
  --ckpt_dir policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke \
  --seed 0 \
  --temporal_agg False \
  2>&1 | tee install_logs/policy_act_eval_hook_move_object_between_zones_demo_policy_hook_2.log
```

## Result

Passed:

- Generated two policy-hook episodes:
  - `data/move_object_between_zones/demo_policy_hook/data/episode0.hdf5`
  - `data/move_object_between_zones/demo_policy_hook/data/episode1.hdf5`
- ACT preprocessing succeeded for both episodes.
- One-epoch ACT smoke training succeeded.
- Checkpoints and training plots were written under:
  - `policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke/`
- Eval hook loaded `dataset_stats.pkl` and `policy_last.ckpt` with all keys matched, then entered RoboTwin rollout before the 180-second timeout.

Observed train metrics:

```text
Val loss: 54.84010
Train loss: 82.72874
Best ckpt, val loss 54.840103 @ epoch0
```

Important eval log lines:

```text
Loaded normalization stats from policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke/dataset_stats.pkl
Loaded policy weights from policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke/policy_last.ckpt
Loading status: <All keys matched successfully>
Task Name: move_object_between_zones
Policy Name: ACT
Reset temporal aggregation state
```

## Known Issues / Next Step

- `script/eval_policy.py` hardcodes `test_num = 100`, so the smoke eval was intentionally stopped by `timeout 180`.
- The first manual eval smoke used `--temporal_agg false`; because the override parser uses Python `eval`, that becomes the string `"false"`, which is truthy. Use config file values or pass `False` in future runs.
- The ACT processed dataset is large: about 12 GB for two episodes because it stores decoded 640x480 RGB arrays.
- For a clean future eval smoke, add a small `--test_num` override to `script/eval_policy.py` or create a tiny wrapper that calls `eval_policy(..., test_num=1)`.
- For real training, increase demonstrations and use the original ACT hyperparameters or move to Pi0/OpenVLA-OFT after confirming their preprocessing requirements.
