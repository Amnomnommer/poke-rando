"""Engine-neutral feature planning contracts.

These modules deliberately do not patch ROMs.  Adapters opt into a feature only
after they can apply and independently verify the resulting plan.
"""

from .contracts import FeaturePlan, FeatureState, Requirement

__all__ = ["FeaturePlan", "FeatureState", "Requirement"]
