import re


def normalize_value(value: str) -> str:
    """Сравнение характеристик: регистр и хвостовая пунктуация не важны."""
    text = value.casefold().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[.,;:!?]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


# Артикул позиции: буквенный префикс, дефис, код (`BLD-800`, `KTL-THRM`).
_ARTICLE = re.compile(r"^[A-Za-z]{2,}-[A-Za-z0-9]+$")
# Параметры в строке позиции разделены запятой с пробелом; запятая внутри числа («1,7 л») — нет.
_PARAMETER_SPLIT = re.compile(r",\s+")


def is_article(key: str) -> bool:
    return bool(_ARTICLE.match(key.strip()))


def _for_occurrence(value: str) -> str:
    """Запись числа не должна мешать вхождению: «2 190» = «2190», «1,7» = «1.7».

    Пробел тысяч убирается только в группах «1 234» / «2 190», а не между разными
    токенами вроде «BLD-500» и «500 Вт».
    """
    text = normalize_value(value).replace("\u00a0", " ")

    def _collapse_thousands(match: re.Match[str]) -> str:
        return re.sub(r"\s+", "", match.group(0))

    text = re.sub(r"\b\d{1,3}(?:\s\d{3})+\b", _collapse_thousands, text)
    return re.sub(r"(?<=\d),(?=\d)", ".", text)


def _occurs(haystack: str, needle: str) -> bool:
    """Вхождение параметра: не «800» внутри «1800», но «пластик» в «сталь/пластик» — да."""
    token = _for_occurrence(needle)
    if not token:
        return False
    if re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", haystack):
        return True
    # Составное значение («сталь/пластик»): сегменты по разделителям тоже ищем целиком.
    if any(sep in token for sep in "/|;"):
        return all(
            _occurs(haystack, part) for part in re.split(r"[/|;]", needle) if part.strip()
        )
    return False


def article_row_rate(article: str, row: str, card_text: str) -> float:
    """Позиция «артикул → строка параметров»: доля параметров строки в тексте карточки.

    Сравниваем по артикулу как по единице эталона, а не по целой строке целиком: карточка
    хранит пары «параметр → значение» и часто не повторяет код `BLD-800`. Код артикула,
    если он есть в карточке, тоже считается найденным параметром. Нет ни одного параметра
    строки в карточке — ноль.
    """
    haystack = _for_occurrence(card_text)
    parameters = [item for item in _PARAMETER_SPLIT.split(row) if _for_occurrence(item)]
    # Код артикула — такой же опознавательный признак позиции, как модель или мощность.
    needles = [article, *parameters]
    found = sum(1 for item in needles if _occurs(haystack, item))
    if found == 0:
        return 0.0
    return found / len(needles)


def characteristics_match_rate(
    expected: dict[str, str],
    actual: dict[str, str],
    extra_text: str = "",
) -> float | None:
    """Доля совпавших характеристик. Пустой эталон ничего не измеряет: `None`, не 1.0.

    Обычная пара «параметр → значение» сравнивается точно (с нормализацией). Позиция с
    артикулом в ключе (КП, спецификация) оценивается по вхождению параметров строки
    (`article_row_rate`); `extra_text` — остальной текст карточки (название, описание).
    """
    if not expected:
        return None
    actual_norm = {normalize_value(key): normalize_value(val) for key, val in actual.items()}
    # Двоеточие отделяет ключ от значения: иначе «BLD-500» + «500 Вт» слипаются при
    # нормализации чисел с пробелами.
    card_text = " ".join(
        [extra_text, *(f"{key}: {value}" for key, value in actual.items())]
    )
    total = 0.0
    for key, value in expected.items():
        if is_article(key):
            total += article_row_rate(key, value, card_text)
        elif actual_norm.get(normalize_value(key)) == normalize_value(value):
            total += 1.0
    return total / len(expected)
