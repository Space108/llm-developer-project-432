import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.context import bind_ids
from app.core.db import close_pool, open_pool, remember_loop
from app.core.logging import bind_log, configure_logging, get_logger
from app.core.migrate import apply_migrations
from app.llm.parse import ModelResponseError
from app.repositories.documents import (
    find_document_id_by_filename,
    insert_document,
    load_document,
)
from app.services.metrics import score_card
from app.services.pipeline import run_context_pipeline
from app.services.retrieve import retrieve_context

# Как GENERATE_RETRY в процессе: один неудачный ответ модели не должен ронять весь прогон.
EVAL_ATTEMPTS = 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="весь эталонный набор")
    parser.add_argument("--golden", default=settings.golden_path)
    args = parser.parse_args()
    configure_logging()
    asyncio.run(_run(Path(args.golden), full=args.all))
    return 0


async def _run(golden_path: Path, *, full: bool) -> None:
    open_pool()
    remember_loop(asyncio.get_running_loop())
    try:
        await apply_migrations()
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        documents = golden["documents"]
        names = list(documents) if full else list(golden.get("eval_defaults") or documents)
        rows: list[dict] = []
        for name in names:
            row = await _eval_with_retry(name, documents[name])
            rows.append(row)
            print(
                f"{name}\tchars={_fmt(row['characteristics'])}\t"
                f"citation={_fmt(row['citation'])}\tjudge={_fmt_judge(row['judge_supported'])}"
            )
        # Среднее только по тем документам, где метрика что-то измеряла (не `None`).
        avg = {
            "characteristics": _avg([item["characteristics"] for item in rows]),
            "citation": _avg([item["citation"] for item in rows]),
            "judge": _avg(
                [
                    None if item["judge_supported"] is None else float(item["judge_supported"])
                    for item in rows
                ]
            ),
        }
        counted = {
            "characteristics": _counted([item["characteristics"] for item in rows]),
            "citation": _counted([item["citation"] for item in rows]),
            "judge": _counted([item["judge_supported"] for item in rows]),
        }
        print(
            f"AVG\tchars={_fmt(avg['characteristics'])}\t"
            f"citation={_fmt(avg['citation'])}\tjudge={_fmt(avg['judge'])}"
        )
        print(
            "counted\t"
            + "\t".join(f"{name}={count}/{len(rows)}" for name, count in counted.items())
        )
        # Метрики зависят от порога релевантности: фиксируем его вместе с цифрами.
        print(f"threshold\trelevance={settings.relevance_threshold}")
        report_dir = Path(settings.metrics_report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = report_dir / f"metrics_{stamp}.json"
        report = {
            "relevance_threshold": settings.relevance_threshold,
            "documents": rows,
            "averages": avg,
            "counted": {**counted, "documents": len(rows)},
        }
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        get_logger().info("metrics_report", path=str(path), documents=len(rows))
        print(f"report\t{path}")
    finally:
        await close_pool()


async def _eval_with_retry(
    filename: str, expected: dict, *, attempts: int = EVAL_ATTEMPTS
) -> dict:
    """Карточка по документу. Неверный формат ответа повторяем, последнюю ошибку не прячем."""
    last: ModelResponseError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await _eval_one(filename, expected)
        except ModelResponseError as exc:
            last = exc
            get_logger().warning(
                "metrics_retry",
                filename=filename,
                attempt=attempt,
                attempts=attempts,
                error=str(exc)[:200],
            )
    assert last is not None
    raise last


async def _eval_one(filename: str, expected: dict) -> dict:
    document_id = await _ensure_document(filename)
    bind_ids(job_id=f"metrics-{document_id}", request_id=f"metrics-req-{document_id}")
    bind_log(job_id=f"metrics-{document_id}", request_id=f"metrics-req-{document_id}")
    hint = str(expected.get("product") or filename)
    screened = await retrieve_context([document_id], hint)
    built = screened.built
    existing = {item.id for item in built.fragments}
    draft, _attempts, _verdict = await asyncio.to_thread(
        run_context_pipeline,
        built.text,
        existing,
        existing,
    )
    scores = await asyncio.to_thread(score_card, draft, built.fragments, expected)
    return {
        "filename": filename,
        "document_id": document_id,
        "title": draft.title,
        # Характеристики самой карточки: по ним видно, за что поставлена оценка.
        "card_characteristics": draft.characteristics,
        "characteristics": scores["characteristics"],
        "citation": scores["citation"],
        "judge_supported": scores["judge_supported"],
        "unsupported_claims": scores["unsupported_claims"],
        "fragment_ids": [item.id for item in built.fragments],
        "cited_fragment_ids": scores["cited_fragment_ids"],
        "citation_probes": scores["citation_probes"],
        "security_blocked": screened.blocked,
    }


async def _ensure_document(filename: str) -> str:
    from app.temporal.activities import index_document_activity, parse_document_activity

    existing = await find_document_id_by_filename(filename)
    if existing is not None:
        # Запись могла остаться разобранной, но без векторов (например, после тестов):
        # без них поиск идёт только по словам и метрики занижаются. Повтор безопасен.
        await index_document_activity(existing)
        return existing
    path = Path("data") / filename
    if not path.exists():
        raise FileNotFoundError(f"нет файла {path}")
    content = path.read_bytes()
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    document_id = "met" + digest[:9]
    await insert_document(document_id, filename, digest, str(path))
    await parse_document_activity(document_id)
    await index_document_activity(document_id)
    stored = await load_document(document_id)
    if stored is None:
        raise RuntimeError(f"документ {filename} не записался")
    return document_id


def _avg(values: list[float | None]) -> float | None:
    """Среднее по измеренным значениям. Если измерять было нечего — `None`, не 0 и не 1."""
    measured = [item for item in values if item is not None]
    if not measured:
        return None
    return sum(measured) / len(measured)


def _counted(values: list[object]) -> int:
    return sum(1 for item in values if item is not None)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_judge(value: bool | None) -> str:
    return "n/a" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
