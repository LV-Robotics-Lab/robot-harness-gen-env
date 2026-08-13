from __future__ import annotations

import numpy as np

from scripts.build_failure_score_correlation import (
    average_ranks,
    correlation,
    exact_permutation_p_value,
)


def test_average_ranks_handles_ties() -> None:
    np.testing.assert_allclose(average_ranks(np.asarray([3.0, 1.0, 1.0, 2.0])), [4.0, 1.5, 1.5, 3.0])


def test_correlation_and_exact_permutation() -> None:
    scores = np.asarray([0.0, 1.0, 2.0, 3.0])
    failures = np.asarray([0.0, 0.0, 1.0, 1.0])
    observed = correlation(scores, failures)
    assert observed is not None and observed > 0.8
    assert exact_permutation_p_value(scores, failures, observed) == 1.0 / 3.0


def test_correlation_is_undefined_for_one_outcome_class() -> None:
    assert correlation(np.asarray([0.0, 1.0]), np.asarray([1.0, 1.0])) is None
