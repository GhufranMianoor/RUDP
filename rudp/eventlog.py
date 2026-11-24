"""Small JSON-lines event logger shared by transport components.

Events are kept in memory and serialised in batches (at close, or when the
buffer holds ``flush_every`` events), so logging costs only a dict append on
the packet path. Formatting and flushing a JSON line per ACK used to cut
sender throughput by about 4x, which made logged runs slower than unlogged
ones. Wall-clock time is derived from one reference point at write time.
"""

from __future__ import annotations

import atexit
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any

DEFAULT_FLUSH_EVERY = 200_000


class EventLogger:
    def __init__(
        self, destination: str | Path | None = None, *, flush_every: int = DEFAULT_FLUSH_EVERY
    ) -> None:
        self._file = None
        self._pending: list[tuple[float, str, dict[str, Any]]] = []
        self._flush_every = max(1, int(flush_every))
        self._wall0 = datetime.now(timezone.utc)
        self._mono0 = time.monotonic()
        if destination is not None:
            self._file = Path(destination).open("a", encoding="utf-8")
            atexit.register(self.close)

    def log(self, event: str, **fields: Any) -> None:
        self._pending.append((time.monotonic(), event, fields))
        if len(self._pending) >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        lines = []
        for monotonic, event, fields in self._pending:
            wall = self._wall0 + timedelta(seconds=monotonic - self._mono0)
            record = {"time": wall.isoformat(), "monotonic": monotonic, "event": event, **fields}
            lines.append(json.dumps(record, sort_keys=True))
        self._pending.clear()
        if self._file is not None:
            self._file.write("\n".join(lines) + "\n")
            self._file.flush()
        else:
            log = logging.getLogger("rudp")
            for line in lines:
                log.info(line)

    def close(self) -> None:
        self.flush()
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
