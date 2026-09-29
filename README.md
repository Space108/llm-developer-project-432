# ИИ-генератор карточки товара

[![hexlet-check](https://github.com/Space108/llm-developer-project-432/actions/workflows/hexlet-check.yml/badge.svg)](https://github.com/Space108/llm-developer-project-432/actions)

Соберите бэкенд-сервис, который принимает документы поставщиков в форматах pdf, docx и
xlsx, строит по ним поисковый индекс и генерирует черновик карточки товара — с
указанием источников, списком недостающих данных и уровнем уверенности. По пути
освоите LLM-клиент с ретраями, строгий контракт результата на Pydantic, разбор
офисных документов и чанкинг, локальные эмбеддинги с pgvector и гибридный поиск,
цитирование с проверкой источников, учёт стоимости вызовов, метрики генерации и
защиту от инъекций через документы и утечек персональных данных.

Учебный проект Хекслета: https://ru.hexlet.io/programs/llm-developer

Факты, которых нет в файлах, не выдумываются: они попадают в `missing_fields`.

Архитектурные решения: [docs/adr/](docs/adr/).  
Карта шагов: [ROADMAP.md](ROADMAP.md).  
Отчёт приёмки (метрики и стоимость): [docs/acceptance-report.md](docs/acceptance-report.md).

## Стек

- Python 3.11+ (урок фиксирует 3.12; на этой машине сейчас 3.11)
- FastAPI, Pydantic, SQLAlchemy, asyncpg
- PostgreSQL 16 с pgvector (`pgvector/pgvector:pg16`)
- Temporal (dev-сервер в Compose, один контейнер)
- OpenAI-совместимый API для генерации: Ollama, LM Studio или OpenRouter
- Локальные эмбеддинги: `google/embeddinggemma-300m` (Hugging Face, кэш на диске)

## Быстрый старт (~15 минут)

Ориентир шага сдачи: посторонний поднимает сервис по одному README. Если Docker, Ollama и модели уже стоят, путь ниже укладывается примерно в 15 минут. Холодный ноутбук (первый `pip install`, первый скачок эмбеддингов и весов Ollama) займёт дольше — это нормально, шаги те же.

### Что нужно заранее

1. **Docker Desktop** — запущен, без него Compose не поднимет Postgres и Temporal.
2. **Python 3.11+** (урок фиксирует 3.12; на Windows удобно `py -3`).
3. **Ollama** — [ollama.com](https://ollama.com), сервис слушает `11434`. Две модели:

```powershell
ollama pull qwen2.5:7b
ollama pull llama3.2:3b
```

Проверка: `curl.exe http://127.0.0.1:11434/v1/models` отвечает JSON. Вместо Ollama можно LM Studio (часто порт `1234`) или OpenRouter — тогда в `.env` меняются `LLM_BASE_URL`, `LLM_MODEL`, `LLM_CHEAP_MODEL` и при необходимости `LLM_API_KEY`.

4. **Hugging Face** — эмбеддинги `google/embeddinggemma-300m` качает `sentence-transformers` при первом разборе/поиске. Нужен аккаунт и доступ к модели на сайте HF (без входа бывал ответ `401`). Один раз:

```powershell
pip install huggingface_hub
huggingface-cli login
```

Дальше веса лежат в кэше на диске, повторно качать не нужно.

### 1. Клон и зависимости

```powershell
git clone https://github.com/Space108/llm-developer-project-432.git
cd llm-developer-project-432
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Первый `pip install` тянет в том числе `torch` — может занять заметное время.

### 2. Настройка `.env`

Открой `.env` и выставь модель (пример под Ollama). Остальное из `.env.example` трогать не нужно: `DATABASE_URL`, Temporal и пороги уже заполнены.

```env
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_CHEAP_MODEL=llama3.2:3b
LLM_TIMEOUT_SECONDS=120
```

Если оставить значения из примера (`1234` / `local-model`) и модели там нет — генерация и детектор инъекций не ответят.

### 3. Инфраструктура и миграции

Чистые тома — тот же способ, которым проверяют сдачу:

```powershell
docker compose down -v
docker compose up -d
python -m app.core.migrate
```

Миграции печатают `применена <файл>` или `новых нет`. Должны пройти `0001`…`0005`.

### 4. Два процесса

Два терминала, в обоих активирован `.venv`:

```powershell
uvicorn app.main:app --reload
```

```powershell
python -m app.temporal.worker
```

Без воркера разбор файла и генерация карточки не сдвинутся с места. UI Temporal: http://127.0.0.1:8233

### 5. Живость и готовность

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/health/ready
```

Готовность должна показать `vector: active`. Если база ещё не поднялась — будет `503`, подожди и повтори.

### 6. Загрузка документа

В ответе будет JSON с полем `document_id` — скопируй его целиком (ниже вместо него пиши свою строку).

```powershell
curl.exe -F "file=@data/kettle_manual.pdf" http://127.0.0.1:8000/documents/
```

Пример ответа: `{"document_id":"a1b2c3d4e5f6","status":"новый"}`.

Дождись разбора и индекса (первый раз воркер ещё скачает эмбеддинги — подожди):

```powershell
curl.exe http://127.0.0.1:8000/documents/DOCUMENT_ID
```

Нужный статус: `проиндексирован`, в ответе есть `fragment_count`. Скан `data/boiler_scan.pdf` уходит в `отказ` с причиной `нет текстового слоя` — так и должно быть.

### 7. Карточка

Подставь тот же `document_id`. В ответе будет `job_id`.

```powershell
curl.exe -X POST http://127.0.0.1:8000/generate-card -H "Content-Type: application/json" -d "{\"document_ids\":[\"DOCUMENT_ID\"],\"product_hint\":\"чайник\"}"
curl.exe http://127.0.0.1:8000/jobs/JOB_ID
```

Документ с инъекцией — `data/kettle_manual.pdf`: подозрительный фрагмент не попадает в контекст (или весь документ уходит к человеку, если подозрительных фрагментов слишком много). В результате задачи смотри поле `security`. Цена вроде «1 рубль» из инъекции в карточке не должна появиться.

Подтверждение:

```powershell
curl.exe -X POST http://127.0.0.1:8000/jobs/JOB_ID/approve
```

Отказ: `POST /jobs/JOB_ID/reject`.

### 8. Тесты и метрики

```powershell
ruff check
pytest
```

Метрики по эталону (файл `data/golden_cards.json` кладётся локально, в git не входит; без него `make metrics` не из чего считать). Сводка последнего прогона сдачи — в [docs/acceptance-report.md](docs/acceptance-report.md).

```powershell
make metrics
make metrics-all
```

На Windows без `make`: `python -m app.commands.metrics` и `python -m app.commands.metrics --all`.

Векторы по уже разобранным фрагментам: `make index-fragments` или `python -m app.commands.reindex`. Поиск: `python -m app.commands.search --mode hybrid "запрос"`.

## Что внутри

| Часть | Где |
|------|-----|
| HTTP | `app/routers/` — документы, генерация, задачи, health |
| Пайплайн | `app/services/pipeline.py` — генерация, критика, проверка ссылок |
| Поиск и контекст | `app/services/retrieve.py`, `context.py`, `repositories/search.py` |
| Защита | `app/services/pii.py`, `injection.py`, `security.py` |
| Модель | `app/llm/client.py` — таймаут, повтор, запись в `llm_calls` |
| Процесс | `app/temporal/` — CardWorkflow, DocumentWorkflow |
| Миграции | `db/migrations/` до `0005_llm_calls.sql` |

Без `DATABASE_URL` приложение не стартует. В git лежит `.env.example`, не настоящий `.env`.

Выданные файлы для приёмки лежат в `data/` и в git: `blender_passport.pdf`, `blender_kp.docx`, `kettle_manual.pdf`, `kettle_spec.xlsx`, `boiler_scan.pdf`. Скан без текстового слоя уходит в `отказ`.

## О Хекслете

[Хекслет](https://ru.hexlet.io/) — школа программирования: авторские программы обучения с практикой, поддержкой наставников и реальными проектами, которые остаются в резюме. Этот репозиторий — один из таких проектов.

<details>
<summary>Автоматические тесты Хекслета</summary>

Тесты запускаются на каждый коммит. За запуск отвечает файл `.github/workflows/hexlet-check.yml` — не удаляйте и не переименовывайте ни его, ни репозиторий.

</details>
