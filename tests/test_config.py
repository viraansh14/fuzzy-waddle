"""Tests for Config.validate_risk_params range checks."""

import pytest

from .conftest import make_config


def test_valid_risk_params_ok():
    # make_config provides a sane, internally-consistent set of defaults.
    make_config().validate_risk_params()  # must not raise


def test_rejects_stop_loss_out_of_range():
    with pytest.raises(ValueError):
        make_config(stop_loss_pct=0).validate_risk_params()
    with pytest.raises(ValueError):
        make_config(stop_loss_pct=150).validate_risk_params()


def test_rejects_non_positive_take_profit():
    with pytest.raises(ValueError):
        make_config(take_profit_pct=0).validate_risk_params()


def test_rejects_position_size_exceeding_exposure():
    cfg = make_config(max_position_size_usdc=600, max_total_exposure_usdc=500)
    with pytest.raises(ValueError):
        cfg.validate_risk_params()


def test_rejects_non_positive_exposure():
    with pytest.raises(ValueError):
        make_config(max_total_exposure_usdc=0).validate_risk_params()


def test_rejects_zero_positions():
    with pytest.raises(ValueError):
        make_config(max_positions=0).validate_risk_params()


def test_rejects_negative_loss_limit():
    with pytest.raises(ValueError):
        make_config(max_total_loss_usdc=-1).validate_risk_params()


def test_zero_loss_limit_is_valid():
    # 0 is the documented "disabled" sentinel and must pass validation.
    make_config(max_total_loss_usdc=0).validate_risk_params()
