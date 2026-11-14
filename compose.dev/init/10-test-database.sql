-- Runs once, when the dev PostgreSQL container first creates its data folder (compose.dev.yml mounts this folder at
-- /docker-entrypoint-initdb.d). POSTGRES_DB is tbo_test_dev, the database the tests use; the comment below is what the
-- kit's guard (tbo_platform_kit.guards.require_test_database) looks for before any TRUNCATE, DROP or flush, and only a
-- test set-up writes it. tbo_dev stays for running the service by hand; it is not a test database.
COMMENT ON DATABASE tbo_test_dev IS 'tbo-test';
CREATE DATABASE tbo_dev;
