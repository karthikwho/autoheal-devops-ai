"""Runtime configuration for the AutoHeal API.

Deliberately dependency-light: a frozen dataclass populated from environment
variables. No ``pydantic-settings``, no secrets, no external service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = ["Settings", "StoreBackend"]

StoreBackend = Literal["memory", "json_file"]

#: Root of this repository, used to resolve the default data directory.
REPO_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_DATA_DIR = REPO_ROOT / ".autoheal-data"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the API needs to start.

    Attributes
    ----------
    data_dir
        Directory used by file-backed stores. Created on demand.
    store_backend
        ``memory`` (nothing survives a restart) or ``json_file`` (a single
        JSONL file, rewritten atomically on every mutation).
    default_page_size / max_page_size
        Pagination bounds for ``GET /api/v1/incidents``.
    """

    app_name: str = "autoheal-api"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = DEFAULT_DATA_DIR
    store_backend: StoreBackend = "json_file"
    default_page_size: int = 50
    max_page_size: int = 200

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        """Build settings from ``env`` (defaults to ``os.environ``)."""
        source = os.environ if env is None else env

        def text(key: str, default: str) -> str:
            value = source.get(key, "").strip()
            return value or default

        def integer(key: str, default: int, minimum: int, maximum: int) -> int:
            raw = source.get(key, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer, got {raw!r}") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}, got {value}")
            return value

        backend = text("AUTOHEAL_STORE_BACKEND", "json_file")
        if backend not in {"memory", "json_file"}:
            raise ValueError(
                f"AUTOHEAL_STORE_BACKEND must be 'memory' or 'json_file', got {backend!r}"
            )

        return cls(
            app_name=text("AUTOHEAL_APP_NAME", "autoheal-api"),
            version=text("AUTOHEAL_APP_VERSION", "0.1.0"),
            host=text("AUTOHEAL_HOST", "127.0.0.1"),
            port=integer("AUTOHEAL_PORT", 8000, 1, 65535),
            data_dir=Path(text("AUTOHEAL_DATA_DIR", str(DEFAULT_DATA_DIR))),
            store_backend=backend,  # type: ignore[arg-type]
            default_page_size=integer("AUTOHEAL_DEFAULT_PAGE_SIZE", 50, 1, 500),
            max_page_size=integer("AUTOHEAL_MAX_PAGE_SIZE", 200, 1, 2000),
        )
