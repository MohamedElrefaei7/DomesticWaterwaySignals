"""Connection plumbing. The one place a credential enters this process.

Everything here reads DATABASE_URL from the environment and nothing else. There is no default
host, no default user, and above all no default password: a module that can construct a working
connection without being told a credential is a module that will silently connect to the wrong
database on the day the environment is misconfigured, which is CLAUDE.md § 2's theme 1 wearing a
different hat.

CLAUDE.md § 1 forbids this project's agent from handling secrets at all. The corresponding
discipline in code is that secrets are referenced by variable name and never written down —
tests/orchestration/test_migration_ordering.py asserts this file contains no credential literal.
"""

from __future__ import annotations

import contextlib
import os
from urllib.parse import urlsplit, urlunsplit

import psycopg

DATABASE_URL_VAR = "DATABASE_URL"


class ConfigurationError(RuntimeError):
    """DATABASE_URL is absent or unusable. Raised early and loudly, never defaulted around."""


def database_url(env: dict | None = None) -> str:
    """Return DATABASE_URL, or raise naming the variable and how to set it.

    `env` exists so tests can pass an explicit mapping instead of mutating os.environ.
    """
    environ = os.environ if env is None else env
    url = environ.get(DATABASE_URL_VAR, "").strip()
    if not url:
        raise ConfigurationError(
            f"{DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and export it "
            f"into the environment (`set -a; . ./.env; set +a`) before running anything that "
            f"touches the database."
        )
    return url


def sqlalchemy_url(url: str | None = None) -> str:
    """Rewrite DATABASE_URL into the form SQLAlchemy needs for APScheduler's job store.

    A bare `postgresql://` URL makes SQLAlchemy reach for psycopg2, which this project does not
    install; naming the driver explicitly turns a confusing ModuleNotFoundError deep inside the
    scheduler's first start into a non-event.
    """
    parts = urlsplit(url if url is not None else database_url())
    if parts.scheme in ("postgres", "postgresql"):
        parts = parts._replace(scheme="postgresql+psycopg")
    return urlunsplit(parts)


def redacted(url: str | None = None) -> str:
    """The URL with the password replaced, for log lines and error messages.

    Connection errors are one of the few places a password reliably leaks into a log file or a
    pasted traceback, so nothing in this project logs a raw DATABASE_URL.
    """
    parts = urlsplit(url if url is not None else database_url())
    if parts.password is None:
        return urlunsplit(parts)
    userinfo = f"{parts.username}:***" if parts.username else "***"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{host}"))


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection.

    autocommit=False is the default because the migration runner's whole correctness argument is
    about transaction boundaries it controls explicitly. The two callers that genuinely want
    autocommit — the runner's `-- migrate:no-transaction` path and the @job decorator's
    bookkeeping — ask for it by name at the call site, where the reason is visible.
    """
    return psycopg.connect(url if url is not None else database_url(), autocommit=autocommit)


@contextlib.contextmanager
def connection(url: str | None = None, *, autocommit: bool = False):
    """`with db.connection() as conn:` — closes on the way out, commits nothing implicitly.

    psycopg's own connection context manager commits on a clean exit. That is a reasonable default
    in general and the wrong one here: this project has two places where the difference between
    "committed" and "not committed" is the entire point of the code. So this wrapper closes and
    leaves committing to the caller.
    """
    conn = connect(url, autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()
