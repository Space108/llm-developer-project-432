import re


def normalize_value(value: str) -> str:
    """Сравнение характеристик: регистр и хвостовая пунктуация не важны."""
    text = value.casefold().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[.,;:!?]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def characteristics_match_rate(
    expected: dict[str, str],
    actual: dict[str, str],
) -> float:
    if not expected:
        return 1.0
    hits = 0
    actual_norm = {normalize_value(key): normalize_value(val) for key, val in actual.items()}
    for key, value in expected.items():
        if actual_norm.get(normalize_value(key)) == normalize_value(value):
            hits += 1
    return hits / len(expected)
