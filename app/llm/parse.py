import json


class ModelResponseError(ValueError):
    pass


def parse_model_json(text: str) -> object:
    """Чистый JSON, markdown-забор или JSON с текстом вокруг. Иначе понятная ошибка."""
    if text is None or not str(text).strip():
        raise ModelResponseError("пустой ответ")
    body = _strip_fence(str(text).strip())
    if not body:
        raise ModelResponseError("пустой ответ")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(body):
        if char not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(body[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise ModelResponseError("не удалось разобрать ответ модели")


def _strip_fence(text: str) -> str:
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
