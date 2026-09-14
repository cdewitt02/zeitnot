-- Runs once, on first container start, against the POSTGRES_DB database.
-- zeitnot creates its own tables on first run; this only enables the extension
-- they depend on, so that `docker compose up -d` is the whole database setup.
CREATE EXTENSION IF NOT EXISTS vector;
