"""One lock for the workflow's database access from its parallel threads.

The departments run in parallel threads (Part 2 loops, Part 3 branches), and each node writes its state, its decision and its trace event. In production every session has its own connection
(PostgreSQL, or a SQLite file with its own pool); a development SQLite database or a test database in memory shares ONE connection, where a session closing in one thread would roll back what
another thread has not committed yet. Taking one re-entrant lock around every read and write of the workflow makes both cases behave the same: the writes are short, so serializing them costs nothing.
"""

import threading

LOCK = threading.RLock()
