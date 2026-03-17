import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    logger = logging.getLogger("job_applier")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(message)s")  # raw JSON lines
        )
        file_handler.emit = _json_emit_factory(file_handler)  # type: ignore[method-assign]
        logger.addHandler(file_handler)

    return logger


def _json_emit_factory(handler: logging.FileHandler):
    original_emit = handler.__class__.emit

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            if hasattr(record, "extra"):
                entry.update(record.extra)
            self.stream.write(json.dumps(entry) + "\n")
            self.flush()
        except Exception:
            original_emit(self, record)

    return lambda record: emit(handler, record)


def get_logger(name: str = "job_applier") -> logging.Logger:
    return logging.getLogger(name)
