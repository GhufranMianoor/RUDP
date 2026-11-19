"""Small JSON-lines event logger shared by transport components."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any


class EventLogger:
    def __init__(self, destination: str | Path | None = None) -> None:
        self._file = None
        if destination is not None:
            self._file = Path(destination).open("a", encoding="utf-8")

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "monotonic": time.monotonic(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, sort_keys=True)
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()
        else:
            logging.getLogger("rudp").info(line)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
