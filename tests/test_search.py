import secrets

import pytest
from app.core.config import settings
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.repositories.documents import insert_document
from app.repositories.search import (
    SearchFilters,
    missing_fragments,
    search_hybrid,
    search_vectors,
    search_words,
    write_embeddings,
)
from app.services.index import index_missing
from sqlalchemy import text


def test_prefixes_are_the_model_card() -> None:
    assert settings.embedding_model == "google/embeddinggemma-300m"
    assert settings.embedding_dimensions == 768
    assert settings.embedding_query_prefix == "task: search result | query: "
    assert settings.embedding_document_prefix == "title: none | text: "


def _axis(index: int) -> list[float]:
    vector = [0.0] * settings.embedding_dimensions
    vector[index] = 1.0
    return vector


def _half() -> list[float]:
    vector = [0.0] * settings.embedding_dimensions
    vector[0] = 0.70710678
    vector[1] = 0.70710678
    return vector


async def _postgres_or_skip() -> None:
    try:
        async with connection() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres unavailable: {exc}")


async def _drop(document_id: str) -> None:
    async with connection() as conn:
        await conn.execute(
            text("DELETE FROM fragments WHERE document_id = :id"),
            {"id": document_id},
        )
        await conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
        await conn.commit()


async def _fragment(document_id: str, fragment_id: str, body: str, *, article: str = "") -> None:
    async with connection() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO fragments (
                    id, document_id, page, section, article, brand, text, position
                )
                VALUES (
                    :id, :document_id, 1, 'Спецификация', :article, '', :text, 0
                )
                """
            ),
            {
                "id": fragment_id,
                "document_id": document_id,
                "article": article,
                "text": body,
            },
        )
        await conn.commit()


async def test_vector_threshold_drops_a_far_fragment() -> None:
    document_id = "srch" + secrets.token_hex(4)
    near_id = document_id + "n"
    far_id = document_id + "f"
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "spec.xlsx", "hash-" + document_id, "data/x")
            await _fragment(document_id, near_id, "близко")
            await _fragment(document_id, far_id, "далеко")
            await write_embeddings([(near_id, _axis(0)), (far_id, _axis(1))])
            hits = await search_vectors(
                _axis(0),
                SearchFilters(document_id=document_id),
                threshold=0.8,
            )
            assert [item.id for item in hits] == [near_id]
            assert hits[0].score == pytest.approx(1.0)
            assert hits[0].text == "близко"
            assert hits[0].page == 1
        finally:
            await _drop(document_id)
    finally:
        await close_pool()


async def test_word_search_finds_article_and_obeys_filter() -> None:
    document_id = "srch" + secrets.token_hex(4)
    fragment_id = document_id + "a"
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "spec.xlsx", "hash-" + document_id, "data/x")
            await _fragment(
                document_id,
                fragment_id,
                "KTL-1700: Бренд КеттлПро; Мощность, Вт 2200",
                article="KTL-1700",
            )
            hits = await search_words("KTL-1700", SearchFilters(document_id=document_id))
            assert [item.id for item in hits] == [fragment_id]
            assert hits[0].article == "KTL-1700"
            assert hits[0].score > 0
            missed = await search_words(
                "KTL-1700",
                SearchFilters(document_id=document_id, article="HTR-1000"),
            )
            assert missed == []
        finally:
            await _drop(document_id)
    finally:
        await close_pool()


async def test_hybrid_ranks_a_fragment_found_by_both_lists_first() -> None:
    document_id = "srch" + secrets.token_hex(4)
    both_id = document_id + "b"
    vector_id = document_id + "v"
    word_id = document_id + "w"
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "spec.xlsx", "hash-" + document_id, "data/x")
            await _fragment(document_id, both_id, "альфа мощность")
            await _fragment(document_id, vector_id, "бета ничего")
            await _fragment(document_id, word_id, "альфа чайник")
            await write_embeddings(
                [
                    (both_id, _half()),
                    (vector_id, _axis(0)),
                    (word_id, _axis(1)),
                ]
            )
            filters = SearchFilters(document_id=document_id)
            hits = await search_hybrid("альфа", _axis(0), filters, threshold=0.5)
            assert {item.id for item in hits} == {both_id, vector_id, word_id}
            assert hits[0].id == both_id
            assert hits[0].score > hits[1].score
        finally:
            await _drop(document_id)
    finally:
        await close_pool()


async def test_document_ids_keep_only_selected_documents() -> None:
    first = "srch" + secrets.token_hex(4)
    second = "srch" + secrets.token_hex(4)
    first_fragment = first + "a"
    second_fragment = second + "b"
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(first)
        await _drop(second)
        try:
            await insert_document(first, "a.xlsx", "hash-" + first, "data/a")
            await insert_document(second, "b.xlsx", "hash-" + second, "data/b")
            await _fragment(first, first_fragment, "мощность 800")
            await _fragment(second, second_fragment, "мощность 900")
            await write_embeddings([(first_fragment, _axis(0)), (second_fragment, _axis(0))])
            hits = await search_vectors(
                _axis(0),
                SearchFilters(document_ids=(first,)),
                threshold=0.8,
            )
            assert [item.id for item in hits] == [first_fragment]
        finally:
            await _drop(first)
            await _drop(second)
    finally:
        await close_pool()


async def test_second_index_does_not_embed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    document_id = "srch" + secrets.token_hex(4)
    fragment_id = document_id + "e"
    calls: list[int] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [_axis(0) for _ in texts]

    monkeypatch.setattr("app.services.embeddings.embed_documents", fake_embed)
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "spec.xlsx", "hash-" + document_id, "data/x")
            await _fragment(document_id, fragment_id, "нужен вектор")
            assert await missing_fragments(document_id)
            assert await index_missing(document_id) == 1
            assert calls == [1]
            assert await missing_fragments(document_id) == []
            assert await index_missing(document_id) == 0
            assert calls == [1]
        finally:
            await _drop(document_id)
    finally:
        await close_pool()
