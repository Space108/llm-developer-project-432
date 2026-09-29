from decimal import Decimal

from app.core.config import settings


def calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Стоимость из расхода токенов. Не float."""
    if model == settings.llm_cheap_model:
        input_price = Decimal(settings.llm_cheap_input_per_1k)
        output_price = Decimal(settings.llm_cheap_output_per_1k)
    else:
        input_price = Decimal(settings.llm_input_per_1k)
        output_price = Decimal(settings.llm_output_per_1k)
    total = (
        Decimal(prompt_tokens) * input_price + Decimal(completion_tokens) * output_price
    ) / Decimal(1000)
    return total.quantize(Decimal("0.00000001"))
