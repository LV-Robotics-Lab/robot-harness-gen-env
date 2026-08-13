#!/usr/bin/env bash
set -euo pipefail

ROBOTWIN_ROOT="${1:-${HOME}/RoboTwin}"
TASK_NAME="${2:-move_object_between_zones}"
TASK_CONFIG="${3:-demo_policy_hook}"
GPU_ID="${4:-0}"
EXPERT_DATA_NUM="${5:-2}"

cd "${ROBOTWIN_ROOT}"

mkdir -p install_logs

if [ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]; then
  # Use the standard RoboTwin environment when the machine follows the documented setup.
  # If conda lives elsewhere, activate RoboTwin before calling this script.
  source "${HOME}/miniconda3/etc/profile.d/conda.sh"
  conda activate RoboTwin
fi

if [ ! -f "task_config/${TASK_CONFIG}.yml" ]; then
  cp task_config/demo_smoke.yml "task_config/${TASK_CONFIG}.yml"
  perl -0pi -e "s/episode_num: 1/episode_num: ${EXPERT_DATA_NUM}/" "task_config/${TASK_CONFIG}.yml"
fi

python -m pip install einops==0.6.0 dm_control==1.0.9 mujoco==2.3.3

timeout 1200 bash collect_data.sh "${TASK_NAME}" "${TASK_CONFIG}" "${GPU_ID}" \
  2>&1 | tee "install_logs/policy_hook_collect_${TASK_NAME}_${TASK_CONFIG}_${EXPERT_DATA_NUM}episodes.log"

(
  cd policy/ACT
  timeout 1200 bash process_data.sh "${TASK_NAME}" "${TASK_CONFIG}" "${EXPERT_DATA_NUM}" \
    2>&1 | tee "../../install_logs/policy_act_process_data_${TASK_NAME}_${TASK_CONFIG}_${EXPERT_DATA_NUM}.log"

  rm -rf "act_ckpt/act-${TASK_NAME}/${TASK_CONFIG}-${EXPERT_DATA_NUM}-smoke"
  mkdir -p "act_ckpt/act-${TASK_NAME}/${TASK_CONFIG}-${EXPERT_DATA_NUM}-smoke"

  timeout 900 python3 imitate_episodes.py \
    --task_name "sim-${TASK_NAME}-${TASK_CONFIG}-${EXPERT_DATA_NUM}" \
    --ckpt_dir "./act_ckpt/act-${TASK_NAME}/${TASK_CONFIG}-${EXPERT_DATA_NUM}-smoke" \
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
    2>&1 | tee "../../install_logs/policy_act_train_hook_${TASK_NAME}_${TASK_CONFIG}_${EXPERT_DATA_NUM}.log"
)

cp policy/ACT/deploy_policy.yml policy/ACT/deploy_policy_demo_policy_hook.yml
perl -0pi -e "s/chunk_size: 50/chunk_size: 20/; s/hidden_dim: 512/hidden_dim: 128/; s/dim_feedforward: 3200/dim_feedforward: 512/" \
  policy/ACT/deploy_policy_demo_policy_hook.yml

set +e
timeout 180 python script/eval_policy.py \
  --config policy/ACT/deploy_policy_demo_policy_hook.yml \
  --overrides \
  --task_name "${TASK_NAME}" \
  --task_config "${TASK_CONFIG}" \
  --ckpt_setting "${TASK_CONFIG}-${EXPERT_DATA_NUM}-smoke" \
  --ckpt_dir "policy/ACT/act_ckpt/act-${TASK_NAME}/${TASK_CONFIG}-${EXPERT_DATA_NUM}-smoke" \
  --seed 0 \
  --temporal_agg False \
  2>&1 | tee "install_logs/policy_act_eval_hook_${TASK_NAME}_${TASK_CONFIG}_${EXPERT_DATA_NUM}.log"
eval_code=${PIPESTATUS[0]}
set -e

echo "eval_exit_code=${eval_code}"
echo "Policy hook finished. Note: eval_policy.py defaults to 100 rollouts, so exit code 124 means the short eval smoke timed out after successful startup."
