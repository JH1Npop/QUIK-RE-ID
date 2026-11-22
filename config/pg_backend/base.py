"""PostgreSQL backend that retries the connection a few times.

A free hosted database (Aiven) can stall the first connection after it has been
idle. Without this, psycopg waits up to 130 seconds and then fails; a fresh
attempt usually connects within a second. So we use a short timeout and retry.
"""
import logging
import time

from django.db.backends.postgresql import base
from django.db.utils import OperationalError
from psycopg import OperationalError as PsycopgOperationalError

logger = logging.getLogger(__name__)

ATTEMPTS = 4


class DatabaseWrapper(base.DatabaseWrapper):
    def get_connection_params(self):
        params = super().get_connection_params()
        params.setdefault("connect_timeout", 10)
        return params

    def get_new_connection(self, conn_params):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return super().get_new_connection(conn_params)
            except (OperationalError, PsycopgOperationalError) as exc:
                if attempt == ATTEMPTS:
                    raise
                logger.warning("Database connection attempt %s failed (%s). Retrying.", attempt, exc)
                time.sleep(1)
