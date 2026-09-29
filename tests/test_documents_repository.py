import hashlib
from pathlib import Path

import pytest
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.parsers.models import FragmentDraft
from app.repositories.documents import (
    count_fragments,
    insert_document,
    load_document,
    replace_fragments,
    update_document_status,
)
from app.services.ingest import prepare_fragments
from app.temporal.activities import parse_document_activity
from openpyxl import Workbook
from sqlalchemy import text


async def _postgres_or_skip() -> None:
    try:
        async with connection() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres unavailable: {exc}")


def _spec(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.append(["Артикул", "мощность", "чаша"])
    sheet.append(["BLD-800", "800 Вт", "1.5 л"])
    book.save(path)
    book.close()


async def test_same_hash_keeps_one_document(tmp_path: Path) -> None:
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        path = tmp_path / "spec.xlsx"
        _spec(path)
        digest = "hash-step5-spec"
        async with connection() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM fragments
                    WHERE document_id IN (
                        SELECT id FROM documents WHERE content_hash = :hash
                    )
                    """
                ),
                {"hash": digest},
            )
            await conn.execute(
                text("DELETE FROM documents WHERE content_hash = :hash"),
                {"hash": digest},
            )
            await conn.commit()
        first, created = await insert_document("doc-step5-a", "spec.xlsx", digest, str(path))
        assert created is True
        second, created_again = await insert_document("doc-step5-b", "spec.xlsx", digest, str(path))
        assert created_again is False
        assert second == first
    finally:
        try:
            async with connection() as conn:
                await conn.execute(
                    text(
                        """
                        DELETE FROM fragments
                        WHERE document_id IN (
                            SELECT id FROM documents WHERE content_hash = 'hash-step5-spec'
                        )
                        """
                    ),
                )
                await conn.execute(
                    text("DELETE FROM documents WHERE content_hash = 'hash-step5-spec'"),
                )
                await conn.commit()
        except Exception:
            pass
        await close_pool()


async def test_parse_activity_indexes_spec_and_rejects_blank_pdf(tmp_path: Path) -> None:
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        spec = tmp_path / "spec.xlsx"
        _spec(spec)
        blank = tmp_path / "scan.pdf"
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with blank.open("wb") as handle:
            writer.write(handle)

        spec_id = "doc-step5-spec"
        blank_id = "doc-step5-blank"
        async with connection() as conn:
            await conn.execute(
                text("DELETE FROM fragments WHERE document_id IN (:spec, :blank)"),
                {"spec": spec_id, "blank": blank_id},
            )
            await conn.execute(
                text("DELETE FROM documents WHERE id IN (:spec, :blank)"),
                {"spec": spec_id, "blank": blank_id},
            )
            await conn.commit()
        await insert_document(spec_id, "spec.xlsx", "hash-step5-activity-spec", str(spec))
        await insert_document(blank_id, "scan.pdf", "hash-step5-activity-blank", str(blank))

        await parse_document_activity(spec_id)
        stored = await load_document(spec_id)
        assert stored is not None
        assert stored["status"] == "проиндексирован"
        assert await count_fragments(spec_id) == 1

        await parse_document_activity(blank_id)
        rejected = await load_document(blank_id)
        assert rejected is not None
        assert rejected["status"] == "отказ"
        assert rejected["error"]
        assert await count_fragments(blank_id) == 0

        outcome = prepare_fragments(spec)
        assert outcome.fragments[0].text == "BLD-800: мощность 800 Вт; чаша 1.5 л"
        assert outcome.fragments[0].page == 1
        assert outcome.fragments[0].section
    finally:
        try:
            async with connection() as conn:
                await conn.execute(
                    text(
                        """
                        DELETE FROM fragments
                        WHERE document_id IN ('doc-step5-spec', 'doc-step5-blank')
                        """
                    ),
                )
                await conn.execute(
                    text(
                        "DELETE FROM documents WHERE id IN ('doc-step5-spec', 'doc-step5-blank')"
                    ),
                )
                await conn.commit()
        except Exception:
            pass
        await close_pool()


async def test_fragments_keep_page_and_section(tmp_path: Path) -> None:
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        document_id = "doc-step5-meta"
        async with connection() as conn:
            await conn.execute(
                text("DELETE FROM fragments WHERE document_id = :id"),
                {"id": document_id},
            )
            await conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            await conn.commit()
        await insert_document(
            document_id,
            "card.docx",
            "hash-step5-meta",
            str(tmp_path / "card.docx"),
        )
        await replace_fragments(
            document_id,
            [
                FragmentDraft(
                    page=2,
                    section="Характеристики",
                    article="BLD-800",
                    brand="Норд",
                    text="BLD-800: мощность 800 Вт",
                )
            ],
        )
        await update_document_status(document_id, "проиндексирован")
        async with connection() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT page, section, article, brand, text
                    FROM fragments WHERE document_id = :id
                    """
                ),
                {"id": document_id},
            )
            row = result.mappings().one()
        assert row["page"] == 2
        assert row["section"] == "Характеристики"
        assert row["article"] == "BLD-800"
        assert row["brand"] == "Норд"
        assert "мощность 800 Вт" in row["text"]
    finally:
        try:
            async with connection() as conn:
                await conn.execute(
                    text("DELETE FROM fragments WHERE document_id = 'doc-step5-meta'"),
                )
                await conn.execute(text("DELETE FROM documents WHERE id = 'doc-step5-meta'"))
                await conn.commit()
        except Exception:
            pass
        await close_pool()


_GIVEN = (
    ("blender_passport.pdf", "проиндексирован", None),
    ("kettle_manual.pdf", "проиндексирован", None),
    ("kettle_spec.xlsx", "проиндексирован", None),
    ("blender_kp.docx", "проиндексирован", None),
    ("boiler_scan.pdf", "отказ", "нет текстового слоя"),
)


async def test_five_given_files_are_stored() -> None:
    root = Path(__file__).resolve().parents[1] / "data"
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        spec_id = ""
        for name, status, error in _GIVEN:
            path = root / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            document_id = "giv" + hashlib.sha256(name.encode()).hexdigest()[:9]
            stored_id, _created = await insert_document(
                document_id,
                name,
                digest,
                str(path.resolve()),
            )
            if name == "kettle_spec.xlsx":
                spec_id = stored_id
            again, created_again = await insert_document(
                document_id + "x",
                name,
                digest,
                str(path.resolve()),
            )
            assert created_again is False
            assert again == stored_id
            row = await load_document(stored_id)
            assert row is not None
            if row["status"] != status:
                await parse_document_activity(stored_id)
                row = await load_document(stored_id)
                assert row is not None
            assert row["status"] == status
            assert row["error"] == error
            if status == "проиндексирован":
                async with connection() as conn:
                    stored = await conn.execute(
                        text(
                            """
                            SELECT page, section
                            FROM fragments WHERE document_id = :id
                            """
                        ),
                        {"id": stored_id},
                    )
                    fragment_rows = stored.mappings().all()
                assert fragment_rows
                assert all(item["page"] >= 1 and item["section"] for item in fragment_rows)
            async with connection() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT count(*) FROM documents WHERE content_hash = :digest
                        """
                    ),
                    {"digest": digest},
                )
                assert int(result.scalar_one()) == 1
        assert spec_id
        async with connection() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT article, section, text
                    FROM fragments WHERE document_id = :id ORDER BY position
                    """
                ),
                {"id": spec_id},
            )
            rows = result.mappings().all()
        assert [item["article"] for item in rows] == ["KTL-1700", "KTL-1000", "KTL-THRM"]
        assert {item["section"] for item in rows} == {"Спецификация"}
        assert "KTL-1000" not in rows[0]["text"]
        assert rows[0]["text"].startswith("KTL-1700: ")
    finally:
        await close_pool()
