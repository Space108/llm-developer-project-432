from decimal import Decimal

from app.core.config import settings
from app.services.cost import calc_cost


def test_cost_uses_decimal_not_float(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_model", "main-model")
    monkeypatch.setattr(settings, "llm_cheap_model", "cheap-model")
    monkeypatch.setattr(settings, "llm_input_per_1k", "0.001")
    monkeypatch.setattr(settings, "llm_output_per_1k", "0.002")
    cost = calc_cost("main-model", 1000, 500)
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.002")
    assert not isinstance(cost, float)


def test_cheap_model_uses_cheap_prices(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_model", "main-model")
    monkeypatch.setattr(settings, "llm_cheap_model", "cheap-model")
    monkeypatch.setattr(settings, "llm_cheap_input_per_1k", "0.0001")
    monkeypatch.setattr(settings, "llm_cheap_output_per_1k", "0.0002")
    cost = calc_cost("cheap-model", 2000, 1000)
    assert cost == Decimal("0.0004")
