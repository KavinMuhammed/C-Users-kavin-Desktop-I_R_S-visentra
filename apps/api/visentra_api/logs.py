"""Structured JSON logging with request/job correlation IDs. Never log secrets or file contents."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k in ("request_id", "job_id"):
            v = (request_id_var if k == "request_id" else job_id_var).get()
            if v:
                out[k] = v
        for k in ("user_id", "project_id", "training_id", "inference_id", "path", "status", "duration_ms", "method"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.handlers = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


log = logging.getLogger("visentra")
