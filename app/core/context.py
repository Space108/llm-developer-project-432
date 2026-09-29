from contextvars import ContextVar

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_ids(*, job_id: str | None = None, request_id: str | None = None) -> None:
    if job_id is not None:
        job_id_var.set(job_id)
    if request_id is not None:
        request_id_var.set(request_id)


def current_job_id() -> str | None:
    return job_id_var.get()


def current_request_id() -> str | None:
    return request_id_var.get()
