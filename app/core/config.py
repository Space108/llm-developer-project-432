from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Типизированные настройки. Без DATABASE_URL объект не создаётся."""

    database_url: str
    llm_base_url: str = "http://127.0.0.1:1234/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "local-model"
    llm_cheap_model: str = "local-cheap"
    llm_timeout_seconds: float = 60
    llm_max_retries: int = 2
    llm_input_per_1k: str = "0.00015"
    llm_output_per_1k: str = "0.0006"
    llm_cheap_input_per_1k: str = "0.00005"
    llm_cheap_output_per_1k: str = "0.0002"
    confidence_threshold: float = 0.6
    max_upload_bytes: int = 10 * 1024 * 1024
    chunk_size: int = 1000
    chunk_overlap: int = 200
    embedding_model: str = "google/embeddinggemma-300m"
    embedding_dimensions: int = 768
    embedding_query_prefix: str = "task: search result | query: "
    embedding_document_prefix: str = "title: none | text: "
    embedding_batch_size: int = 32
    relevance_threshold: float = 0.40
    rrf_k: int = 60
    search_limit: int = 10
    context_size_limit: int = 12000
    injection_block_threshold: int = 2
    # Синхронное `with connection()` (слой каркаса) применяет недостающие миграции. Нужны права
    # на DDL; там, где их нет, ставят false и применяют схему заранее: `python -m app.core.migrate`.
    auto_migrate: bool = True
    golden_path: str = "data/golden_cards.json"
    metrics_report_dir: str = "data/reports"
    temporal_host: str = "localhost:7233"
    temporal_task_queue: str = "card-queue"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def embed_dim(self) -> int:
        """Имя из каркаса Хекслета."""
        return self.embedding_dimensions

    @property
    def suspicious_chunk_limit(self) -> int:
        """Сколько подозрительных фрагментов допустимо: их отсеивают, документ остаётся.

        Значение берётся из `injection_block_threshold`; имя свойства — из каркаса Хекслета.
        """
        return self.injection_block_threshold

    def too_many_suspicious(self, count: int) -> bool:
        """Единое правило блокировки документа: подозрительных строго больше допустимого."""
        return count > self.suspicious_chunk_limit


settings = Settings()
