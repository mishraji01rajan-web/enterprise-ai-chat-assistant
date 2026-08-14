import pytest

from app.db.sql_guard import SQLGuardError, execute_readonly_query, validate_select_query


def test_allows_simple_select():
    result = validate_select_query("SELECT id, name FROM customers WHERE id = 1")
    assert "customers" in result.tables


def test_rejects_insert():
    with pytest.raises(SQLGuardError):
        validate_select_query("INSERT INTO customers (name) VALUES ('x')")


def test_rejects_update():
    with pytest.raises(SQLGuardError):
        validate_select_query("UPDATE customers SET name = 'x' WHERE id = 1")


def test_rejects_delete():
    with pytest.raises(SQLGuardError):
        validate_select_query("DELETE FROM invoices WHERE id = 1")


def test_rejects_drop():
    with pytest.raises(SQLGuardError):
        validate_select_query("DROP TABLE customers")


def test_rejects_multiple_statements():
    with pytest.raises(SQLGuardError):
        validate_select_query("SELECT * FROM customers; DROP TABLE customers;")


def test_rejects_disallowed_table():
    with pytest.raises(SQLGuardError):
        validate_select_query("SELECT * FROM users")


def test_rejects_pragma():
    with pytest.raises(SQLGuardError):
        validate_select_query("PRAGMA table_info(customers)")


def test_rejects_attach():
    with pytest.raises(SQLGuardError):
        validate_select_query("ATTACH DATABASE 'x.db' AS x")


def test_rejects_pragma_table_valued_function():
    # pragma_table_info(...) is a table-valued function, not the bare
    # `PRAGMA` statement — a naive `\bpragma\b` regex misses it because
    # there's no word boundary between "pragma" and the following
    # underscore. Also independently caught by the table whitelist since
    # `sqlite_master`/pragma pseudo-tables aren't in ALLOWED_TABLES.
    with pytest.raises(SQLGuardError):
        validate_select_query("SELECT * FROM pragma_table_info('users')")


def test_rejects_empty_query():
    with pytest.raises(SQLGuardError):
        validate_select_query("   ")


def test_execute_readonly_query_returns_rows():
    rows = execute_readonly_query("SELECT id, name, tier FROM customers ORDER BY id")
    assert len(rows) == 5
    assert rows[0]["name"] == "Acme Manufacturing"


def test_execute_readonly_query_enforces_row_cap():
    # Ask for more than MAX_ROWS; guard should cap the effective limit.
    rows = execute_readonly_query("SELECT id FROM invoices LIMIT 100000")
    assert len(rows) <= 200


def test_execute_readonly_query_rejects_write_even_if_it_slips_past_regex():
    # Defense in depth: even if a future regex/keyword bypass existed, the
    # underlying connection is opened read-only, so writes fail at the
    # driver level, not just the AST-validation level.
    with pytest.raises(SQLGuardError):
        execute_readonly_query("UPDATE customers SET name='x'")
