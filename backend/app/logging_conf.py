import logging


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("terrawatch")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_exception(logger: logging.Logger, exc: Exception, context: str | None = None) -> None:
    message = f"{context}: {exc.__class__.__name__}: {exc}" if context else str(exc)
    logger.exception(message)
