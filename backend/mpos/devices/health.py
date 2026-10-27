"""Phone check-ins on their way to Snowflake (MPOS_DEVICE_HEARTBEATS), where device health is reported.

Each heartbeat adds a row here in memory; every FLUSH_SECONDS a background thread writes the rows in
chunks on the device connection, then sets each terminal's LAST_SEEN_AT. Five minutes between writes
lets the Snowflake warehouse sleep in between (about 288 short wake-ups a day).

If Snowflake is down the rows wait for the next flush. At most MAX_ROWS wait (about 1.8 hours of 900
phones); past that the oldest are dropped and the count is logged - health history may then have a gap,
billing is never affected.
"""
import logging
import threading
from collections import deque

from . import store

FLUSH_SECONDS = 300
CHUNK_ROWS = 5000  # 10 columns -> 50,000 bound values per INSERT, under the connector's staging threshold
MAX_ROWS = 200_000
log = logging.getLogger(__name__)


class Buffer:
    def __init__(self, max_rows=MAX_ROWS):
        self._rows = deque()
        self._lock = threading.Lock()
        self.max_rows = max_rows
        self.dropped = 0

    def add(self, row):
        """row: a tuple in store.HEARTBEAT_COLUMNS order."""
        with self._lock:
            self._rows.append(row)
            self._trim()

    def take(self):
        """Every waiting row, oldest first; the buffer is left empty."""
        with self._lock:
            rows, self._rows = list(self._rows), deque()
            return rows

    def put_back(self, rows):
        """Rows a flush could not write go back in front of anything that arrived meanwhile."""
        with self._lock:
            self._rows.extendleft(reversed(rows))
            self._trim()

    def _trim(self):
        while len(self._rows) > self.max_rows:
            self._rows.popleft()
            self.dropped += 1

    def __len__(self):
        return len(self._rows)


def flush(buffer):
    """Write the waiting rows; returns how many were written. Never raises: what could not be written
    goes back into the buffer for the next flush."""
    rows = buffer.take()
    if not rows:
        return 0
    written = 0
    try:
        for i in range(0, len(rows), CHUNK_ROWS):
            store.insert_heartbeats(rows[i:i + CHUNK_ROWS])
            written = i + len(rows[i:i + CHUNK_ROWS])
    except Exception:  # noqa: BLE001 - keep the rows, try again next time
        buffer.put_back(rows[written:])
        log.exception("Could not write %d phone check-ins to Snowflake - kept for the next flush",
                      len(rows) - written)
    if written:
        try:
            store.update_last_seen(min(r[2] for r in rows[:written]))
        except Exception:  # noqa: BLE001 - the next flush sets it again from the check-ins
            log.exception("Could not update LAST_SEEN_AT")
    if buffer.dropped:
        log.warning("%d phone check-ins dropped so far: Snowflake was unreachable for too long", buffer.dropped)
    return written


def start_flusher(buffer):
    """Background thread: flush every FLUSH_SECONDS. Set the returned event to stop it."""
    stop = threading.Event()

    def loop():
        while not stop.wait(FLUSH_SECONDS):
            flush(buffer)

    threading.Thread(target=loop, name="mpos-heartbeat-flush", daemon=True).start()
    return stop


buffer = Buffer()
