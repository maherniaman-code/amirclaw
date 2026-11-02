"""SQLite connections that close at the end of their `with` block.

A plain `with sqlite3.connect(path) as c:` only commits or rolls back; the file stays open until
the garbage collector frees the connection. Python 3.14's incremental collector does that late, so
a reprocess (61 days x several reports) piled up hundreds of open files on the server and failed
with "Too many open files" (02-11).
"""

import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(database, **kwargs) -> sqlite3.Connection:
    return sqlite3.connect(database, factory=ClosingConnection, **kwargs)
