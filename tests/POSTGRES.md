Backend suite with PostgreSQL

The full suite imports app.crm.router, which creates the PostgreSQL pool during module import. A running PostgreSQL instance is therefore required before pytest collection.

Compatible local setup (do not change project dependency versions):

1. Start PostgreSQL 14+ (15 is recommended) and create an empty test database/user.
2. Export a test-only connection string, never a production URL:

   export DATABASE_URL='postgresql://livre_test:REDACTED@127.0.0.1:5432/livre_cloud_test'

3. Apply the repository migrations in order, using the project's migration runner or psql. For a disposable database, the SQL files under app/migrations/ are the source of truth.
4. Run with the dependencies already required by the project. If the environment does not have them, install only the compatible missing packages (the current verification used pytest 8.4.2, httpx, fastapi, pydantic, python-dotenv and psycopg2-binary via uv's ephemeral --with flags):

   PYTHONPATH=. uv run --with pytest==8.4.2 --with httpx --with fastapi --with pydantic --with python-dotenv --with psycopg2-binary pytest -q

5. Tear down or discard the test database after the run. Never point DATABASE_URL at production.

Current environment result: the focused financial suite passes (9 passed), but the complete suite cannot collect because no PostgreSQL server is listening at /var/run/postgresql/.s.PGSQL.5432 and this machine has no docker, podman, postgres, initdb, or pg_ctl executable. This is an environment blocker, not a dependency-version failure.
