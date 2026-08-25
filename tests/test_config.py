import pytest

from pharmacy_agent.config import DISPUTE_REQUIRES_APPROVAL_ENV_VAR, dispute_requires_approval


def test_defaults_to_false_when_unset(monkeypatch):
    monkeypatch.delenv(DISPUTE_REQUIRES_APPROVAL_ENV_VAR, raising=False)
    assert dispute_requires_approval() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_truthy_values_enable_approval_gate(monkeypatch, value):
    monkeypatch.setenv(DISPUTE_REQUIRES_APPROVAL_ENV_VAR, value)
    assert dispute_requires_approval() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", ""])
def test_falsy_values_keep_approval_gate_off(monkeypatch, value):
    monkeypatch.setenv(DISPUTE_REQUIRES_APPROVAL_ENV_VAR, value)
    assert dispute_requires_approval() is False
