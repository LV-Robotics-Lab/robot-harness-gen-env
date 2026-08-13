"""AgenticSim — Self-Improving Agents for Physical AI.

Runtime-governed Robot-for-Robot data and evaluation system using PEARL framework:
failure memory, attribution, task-environment co-evolution, governance.
"""

__version__ = "0.1.0"

from ._third_party import bootstrap_vendored_isaaclab

bootstrap_vendored_isaaclab()

try:
    from isaaclab_tasks.utils import import_packages as _import_packages
    _BLACKLIST_PKGS = ["utils", ".mdp"]
    _import_packages(f"{__name__}.tasks", _BLACKLIST_PKGS)
except ImportError:
    pass

try:
    from .envs.env_factory import EnvFactory as _EnvFactory

    _EnvFactory.discover_and_register()
except ImportError:
    pass
