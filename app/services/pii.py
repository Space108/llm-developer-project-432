import re
from dataclasses import dataclass, field


@dataclass
class PiiHit:
    kind: str
    label: str
    original: str


@dataclass
class MaskResult:
    text: str
    hits: list[PiiHit] = field(default_factory=list)

    def __iter__(self):
        """Распаковка как в каркасе: cleaned, entities = mask_pii(...)."""
        yield self.text
        yield self.hits


_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)[\s\-]?(?:\(?\d{3}\)?[\s\-]?)?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_INN_RE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")

_KIND_NAMES = {
    "phone": "телефон",
    "email": "почта",
    "inn": "номер налогоплательщика",
    "card": "номер карты",
}


def mask_pii(text: str, counters: dict[str, int] | None = None) -> MaskResult:
    """Маскирует персональные данные. ИНН — только с верной контрольной суммой."""
    counters = counters if counters is not None else {}
    hits: list[PiiHit] = []
    result = text

    def _replace(kind: str, original: str) -> str:
        counters[kind] = counters.get(kind, 0) + 1
        label = f"{_KIND_NAMES[kind]} номер {counters[kind]}"
        hits.append(PiiHit(kind=kind, label=label, original=original))
        return label

    def phone_sub(match: re.Match[str]) -> str:
        return _replace("phone", match.group(0))

    def email_sub(match: re.Match[str]) -> str:
        return _replace("email", match.group(0))

    def inn_sub(match: re.Match[str]) -> str:
        digits = match.group(1)
        if not inn_checksum_ok(digits):
            return digits
        return _replace("inn", digits)

    def card_sub(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if not (13 <= len(digits) <= 19) or not luhn_ok(digits):
            return raw
        return _replace("card", raw)

    result = _PHONE_RE.sub(phone_sub, result)
    result = _EMAIL_RE.sub(email_sub, result)
    result = _INN_RE.sub(inn_sub, result)
    result = _CARD_RE.sub(card_sub, result)
    return MaskResult(text=result, hits=hits)


def contains_pii(text: str) -> bool:
    """Есть ли в тексте персональные данные с верной проверкой."""
    return bool(mask_pii(text).hits)


def inn_checksum_ok(digits: str) -> bool:
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        total = sum(int(digits[i]) * weights[i] for i in range(9))
        check = total % 11 % 10
        return check == int(digits[9])
    if len(digits) == 12:
        w1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        w2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        c1 = sum(int(digits[i]) * w1[i] for i in range(10)) % 11 % 10
        c2 = sum(int(digits[i]) * w2[i] for i in range(11)) % 11 % 10
        return c1 == int(digits[10]) and c2 == int(digits[11])
    return False


_inn_checksum = inn_checksum_ok


def luhn_ok(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for index, char in enumerate(reverse):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0
