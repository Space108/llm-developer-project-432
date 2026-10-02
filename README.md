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

- Python 3.11+ (урок фиксирует 3.12; в CI тоже 3.12)
- FastAPI, Pydantic, SQLAlchemy, asyncpg
- PostgreSQL 16 с pgvector (`pgvector/pgvector:pg16`)
- Temporal (dev-сервер в Compose, один контейнер)
- OpenAI-совместимый API для генерации: Ollama, LM Studio или OpenRouter
- Локальные эмбеддинги: `google/embeddinggemma-300m` (Hugging Face, кэш на диске)

## Быстрый старт (~15 минут)

Ориентир шага сдачи: посторонний поднимает сервис по одному README. Если Docker, Ollama и модели уже стоят, путь ниже укладывается примерно в 15 минут. Холодный ноутбук (первая установка зависимостей, первый скачок эмбеддингов и весов Ollama) займёт дольше — это нормально, шаги те же.

Весь путь ниже пройден целиком на Windows 11 в Windows PowerShell 5.1: от пустой базы до подтверждённой карточки. Отличия для bash и zsh (Linux, macOS) отмечены в замечаниях, на них путь отдельно не прогонялся.

### Что нужно заранее

1. **Docker Desktop** — запущен, без него Compose не поднимет Postgres и Temporal. На хосте должны быть свободны порты `5432` (Postgres), `7233` и `8233` (Temporal) и `8000` (API); если на `5432` уже стоит свой Postgres, останови его на время работы.
2. **Python 3.11+** (урок фиксирует 3.12; на Windows удобно `py -3`) и **uv** ([docs.astral.sh/uv](https://docs.astral.sh/uv/), подойдёт `pip install uv`): зависимости ставятся строго из `uv.lock`.
3. **Ollama** — [ollama.com](https://ollama.com), сервис слушает `11434`. Две модели (около 4,7 и 2 ГБ; на ноутбуке без видеокарты карточка по одному документу собиралась около полутора минут):

```powershell
ollama pull qwen2.5:7b
ollama pull llama3.2:3b
```

Проверка: `curl.exe http://127.0.0.1:11434/v1/models` отвечает JSON. Вместо Ollama можно LM Studio (часто порт `1234`) или OpenRouter — тогда в `.env` меняются `LLM_BASE_URL`, `LLM_MODEL`, `LLM_CHEAP_MODEL` и при необходимости `LLM_API_KEY`.

4. **Hugging Face** — эмбеддинги `google/embeddinggemma-300m` качает `sentence-transformers` при индексации первого документа. Модель закрытая, поэтому нужны три вещи: аккаунт на [huggingface.co](https://huggingface.co), принятые условия на [странице модели](https://huggingface.co/google/embeddinggemma-300m) и токен с правом чтения ([создать токен](https://huggingface.co/settings/tokens)). Без них загрузка падает с ответом `401` или `403`. Вход делается один раз в шаге 1 командой `hf auth login`, отдельно ничего ставить не нужно. Дальше веса лежат в кэше на диске, повторно качать их не придётся.

### 1. Клон и зависимости

```powershell
git clone https://github.com/Space108/llm-developer-project-432.git
cd llm-developer-project-432
uv sync --frozen --extra dev
.\.venv\Scripts\Activate.ps1
copy .env.example .env
hf auth login
```

`uv sync --frozen` ставит ровно те версии, что записаны в `uv.lock` (то же делают `make setup` и CI), и сам создаёт `.venv`. Первая установка тянет в том числе `torch` (CPU-сборка) — может занять заметное время.

`hf auth login` просит токен Hugging Face из пункта 4 выше. Вставь его; на вопрос про git credential можно ответить `n`.

Следующие команды выполняй в этом же окне, где активирован `.venv` (в начале строки появится название окружения в скобках). В каждом новом окне нужно перейти в папку проекта и снова выполнить `.\.venv\Scripts\Activate.ps1`.

Если PowerShell пишет, что выполнение сценариев отключено, выполни `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` и повтори активацию. Для bash и zsh вместо Activate.ps1 и `copy`: `source .venv/bin/activate` и `cp .env.example .env`.

Без uv запасной путь: `py -3 -m venv .venv`, активация и `pip install -e ".[dev]"`; версии тогда не закреплены lock-файлом.

### 2. Настройка `.env`

Открой `.env` (например, `notepad .env`) и выставь модель (пример под Ollama). Остальное из `.env.example` трогать не нужно: `DATABASE_URL`, Temporal и пороги уже заполнены.

```env
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_CHEAP_MODEL=llama3.2:3b
LLM_TIMEOUT_SECONDS=120
```

Если оставить значения из примера (`1234` / `local-model`) и модели там нет — генерация и детектор инъекций не ответят.

### 3. Инфраструктура и миграции

```powershell
docker compose up -d --wait
python -m app.core.migrate
```

`--wait` возвращает управление, когда Postgres и Temporal прошли проверку здоровья (около 10 секунд; в первый раз дольше, пока скачиваются образы). Без него миграции могут стартовать раньше, чем база готова.

Миграции печатают `применена <файл>` или `новых нет`. Должны пройти `0001`…`0006`; второй запуск печатает `новых нет`.

Начать с чистой базы, как делает проверка сдачи: `docker compose down -v` (все данные базы будут удалены), затем те же две команды.

### 4. Два процесса

Два отдельных окна PowerShell, в обоих активирован `.venv` (каждое окно занято своим процессом, закрывать их нельзя):

```powershell
uvicorn app.main:app --reload
```

```powershell
python -m app.temporal.worker
```

Без воркера разбор файла и генерация карточки не сдвинутся с места. Воркер при запуске ничего не печатает, это нормально. UI Temporal: http://127.0.0.1:8233

### 5. Живость и готовность

Шаги 5–7 выполняй в третьем окне (`.venv` там нужен только для шага 8). В bash и zsh пиши `curl` вместо `curl.exe`. Если в Windows PowerShell 5.1 русские слова в ответах показываются кракозябрами, выполни один раз в этом окне `[Console]::OutputEncoding = [Text.Encoding]::UTF8`.

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

Нужный статус: `проиндексирован`. Для `data/kettle_manual.pdf` ответ выглядит так: `{"document_id":"8b7cf1509ce2","status":"проиндексирован","fragment_count":4,"error":null}`. Скан `data/boiler_scan.pdf` уходит в `отказ`: `{"document_id":"…","status":"отказ","fragment_count":0,"error":"нет текстового слоя"}` — так и должно быть.

### 7. Карточка

Подставь тот же `document_id`. Запросу нужно тело в JSON, а кавычки в PowerShell зависят от версии (запись `-d "{\"…\"}"` с `curl.exe` в 5.1 ломает JSON), поэтому ниже способы, которые работают без возни с кавычками.

**Swagger в браузере (любая ОС).** Открой http://127.0.0.1:8000/docs, раскрой `POST /generate-card`, нажми *Try it out*, вставь тело и нажми *Execute*:

```json
{"document_ids": ["DOCUMENT_ID"], "product_hint": "чайник"}
```

**PowerShell** (проверено в 5.1):

```powershell
$body = @{ document_ids = @("DOCUMENT_ID"); product_hint = "чайник" } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/generate-card -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

**bash и zsh:**

```bash
curl -X POST http://127.0.0.1:8000/generate-card -H "Content-Type: application/json" -d '{"document_ids":["DOCUMENT_ID"],"product_hint":"чайник"}'
```

В ответе будет `job_id`. Состояние задачи (вместо `JOB_ID` подставь свою строку):

```powershell
curl.exe -s http://127.0.0.1:8000/jobs/JOB_ID
```

Статус меняется: `поиск`, `генерация`, `проверка` и, наконец, `ожидание`. Это значит, что карточка готова и ждёт решения человека; в `result` лежат поля карточки, `sources` (откуда взят каждый факт), `missing_fields` (чего в документе нет) и `confidence` (ниже 1, если данных не хватило). Для `kettle_manual.pdf` с подсказкой `чайник` это около полутора минут без видеокарты. Пока статус другой, `result` пуст — просто повтори запрос.

Подтверждение (статус станет `согласовано`):

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/jobs/JOB_ID/approve
```

Отказ: `POST /jobs/JOB_ID/reject`.

**Защита в деле.** `data/kettle_manual.pdf` содержит раздел с инъекцией («игнорируй инструкции…», цена «1 рубль») и контактами. Подсказка `чайник` его не затрагивает, и в `security` пусто. Чтобы увидеть защиту, повтори запрос на генерацию с `"product_hint": "условия сотрудничества"`: подозрительный фрагмент исключается (`security.excluded`), телефон и почта маскируются (`security.masked`), контекста не остаётся, поэтому карточка пустая, `missing_fields` перечисляет все поля, а `error` равен `контекст пуст`. Если подозрительных фрагментов слишком много, весь документ уходит к человеку. Цена вроде «1 рубль» из инъекции в карточке не появляется ни в одном из двух запросов.

### 8. Тесты и метрики

```powershell
ruff check
pytest
```

Тестам модель не нужна. Тесты, которым нужны база и Temporal, без поднятого Compose пропускаются (`skipped`), с ним выполняются.

Необязательный набор на сто документов `data/bulk/` прогоняет `tests/test_bulk_sweep.py`: только разбор, без базы и модели, около 6 секунд. Он проверяет, что разбор не заточен под пять выданных файлов (результаты — в [docs/acceptance-report.md](docs/acceptance-report.md)). Нет папки `data/bulk/` — тесты пропускаются.

Метрики по эталону `data/golden_cards.json`: он входит в выданный набор и лежит в репозитории рядом с документами. Сводка последнего прогона сдачи — в [docs/acceptance-report.md](docs/acceptance-report.md).

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
| Миграции | `db/migrations/` до `0006_hexlet_chunks.sql` |
| Имена каркаса | `app/rag/`, `app/guardrails/`, `services/rag_pipeline.py`, `repositories/chunks.py` и др. повторяют имена из контракта модулей; боевой поток идёт мимо них, см. [ADR 0005](docs/adr/0005-scaffold-compat-layer.md) |

Без `DATABASE_URL` приложение не стартует. В git лежит `.env.example`, не настоящий `.env`.

Выданные файлы для приёмки лежат в `data/` и в git: `blender_passport.pdf`, `blender_kp.docx`, `kettle_manual.pdf`, `kettle_spec.xlsx`, `boiler_scan.pdf` и эталон `golden_cards.json`. Скан без текстового слоя уходит в `отказ`. Необязательный набор на сто документов для прогона на объёме лежит в `data/bulk/`, его проверяет `tests/test_bulk_sweep.py`.

## О Хекслете

[Хекслет](https://ru.hexlet.io/) — школа программирования: авторские программы обучения с практикой, поддержкой наставников и реальными проектами, которые остаются в резюме. Этот репозиторий — один из таких проектов.

<details>
<summary>Автоматические тесты Хекслета</summary>

Тесты запускаются на каждый коммит. За запуск отвечает файл `.github/workflows/hexlet-check.yml` — не удаляйте и не переименовывайте ни его, ни репозиторий.

</details>
