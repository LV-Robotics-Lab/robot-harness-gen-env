# ASPIRE reproduction preflight

Captured on 2026-08-31 before dependency installation, service startup, trial
execution, or initialization of ASPIRE's nested submodules.

## Requested and selected scope

The user requested a paper understanding, official-code clone, reproduction
attempt, and experiments focused on agent-harness relevance. No ASPIRE suite or
paper experiment was named. The study therefore inspected the canonical
LIBERO-Pro Goal-Swap Fix Loop as the upstream reference protocol, but did not
silently redefine it or claim a reduced run as a full reproduction.

## Upstream fixed protocol

At upstream commit `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`, the canonical
Goal-Swap Quick Start fixes:

- suite: `libero_goal_swap`, all ten tasks;
- development seeds: 51–65;
- held-out seeds: 1–50;
- GPU ownership: GPU 0 SAM3, GPU 1 GraspNet, GPU 2 PyRoKi, GPU 3–7 five
  concurrent task slots;
- Stage 1: initial program plus at most three repair replays per failed
  development seed;
- Stage 2: one selected program evaluated on all 50 held-out seeds;
- promotion rule: only Stage-1 evidence may edit shared skills;
- failure duration warning: a failed trial may take about 6–7 minutes, with
  held-out seeds evaluated sequentially per task.

The upstream safety boundary also states that generated Python is untrusted and
the worker isolation/watchdog is not a security sandbox. Simulator ground-truth
APIs, reward predicates, asset files, joint teleportation, and unwrapped
environment access are forbidden.

## Host observation

| Requirement | Observed state | Result |
| --- | --- | --- |
| Linux x86-64 | Ubuntu-family kernel 7.0, x86-64 | compatible |
| Eight-GPU reference topology | one RTX 5090, 32,607 MiB | blocking mismatch |
| CUDA compiler for native setup | `nvcc` absent | blocking for some stacks |
| Base/LIBERO environments | `.venv` and `.venv-libero` absent | not set up |
| Nested source submodules | all listed with leading `-` | not initialized |
| SAM3 gated access | no HF token variables observed | unavailable |
| Model endpoint key | `NVIDIA_API_KEY` not set | unavailable for named baseline |
| Perception services | ports 8114, 8115, 8116 returned `000` | down |
| Existing ASPIRE outputs | `outputs/` absent | clean slate |
| Disk | about 710 GiB free | sufficient for partial setup |

No secret value was printed or written; only presence/absence was checked.

## Decision

Do not launch or relabel the full LIBERO-Pro Quick Start. It has multiple exact,
independent blockers and upstream requires a confirmation gate before setup.
Because the user explicitly requested uninterrupted autonomous work, continue
with safe work that does not bypass those blockers:

1. inspect and execute upstream tests that do not need simulator services or
   new dependencies;
2. reproduce the harness mechanics against synthetic/committed traces;
3. build a project-native benchmark with a development/held-out boundary and
   the existing runtime validator as authority;
4. make any integration recommendation proportional to those bounded results.

This is a partial reproduction attempt, not evidence for the paper's reported
LIBERO, Robosuite, BEHAVIOR-1K, or sim-to-real success rates.

