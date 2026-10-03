-- Колонка kind и значения по умолчанию для таблицы documents.
-- Вставки по контракту каркаса (INSERT INTO documents (id, kind) ...) не задают все поля.
-- Рабочий код всегда пишет поля явно, умолчания ему не мешают.
-- content_hash остаётся уникальным: умолчание каждый раз даёт новое значение.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT '';
ALTER TABLE documents ALTER COLUMN filename SET DEFAULT '';
ALTER TABLE documents ALTER COLUMN status SET DEFAULT '';
ALTER TABLE documents ALTER COLUMN path SET DEFAULT '';
ALTER TABLE documents ALTER COLUMN content_hash SET DEFAULT gen_random_uuid()::text;
