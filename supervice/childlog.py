"""Size-rotated log sink for child output streams."""

import os


class _ChildLogWriter:
    """Size-rotated log sink for one child output stream.

    Writes are ordinary buffered file I/O performed on the event loop — the
    same tradeoff the daemon's own RotatingFileHandler makes.
    """

    def __init__(self, path: str, maxbytes: int, backups: int) -> None:
        self.path = path
        self.maxbytes = maxbytes
        self.backups = backups
        self._file = open(path, "ab")
        try:
            self._size = os.fstat(self._file.fileno()).st_size
        except OSError:
            self._size = 0

    def write(self, chunk: bytes) -> None:
        if self.maxbytes <= 0:
            self._file.write(chunk)
            self._file.flush()
            self._size += len(chunk)
            return
        # Split oversized chunks so the live file never exceeds maxbytes.
        while chunk:
            room = self.maxbytes - self._size
            if room <= 0:
                self._rotate()
                room = self.maxbytes
            part = chunk[:room]
            self._file.write(part)
            self._file.flush()
            self._size += len(part)
            chunk = chunk[len(part):]

    def _rotate(self) -> None:
        self._file.close()
        if self.backups > 0:
            for i in range(self.backups - 1, 0, -1):
                src = "%s.%d" % (self.path, i)
                if os.path.exists(src):
                    os.replace(src, "%s.%d" % (self.path, i + 1))
            os.replace(self.path, "%s.1" % self.path)
        else:
            try:
                os.remove(self.path)
            except OSError:
                pass
        self._file = open(self.path, "ab")
        self._size = 0

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass
