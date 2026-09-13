"""AgenticSim — Self-Improving Agents for Physical AI.

Runtime-governed Robot-for-Robot data and evaluation system using PEARL framework:
failure memory, attribution, task-environment co-evolution, governance.
"""

__version__ = "0.1.0"

# Keep the historical explicit helper available without changing import-time paths
# or initializing optional simulator packages for the asset provider.
from ._third_party import bootstrap_vendored_isaaclab

__all__ = ["bootstrap_vendored_isaaclab"]
