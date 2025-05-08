# test/test_init_db.py

import pytest
from unittest import mock
import psycopg2
from psycopg2 import OperationalError, ProgrammingError, DatabaseError # Import specific exceptions
import sys
import os

# Add the parent directory to sys.path if init_db.py is in the root
# Adjust the path if init_db.py is in a different location relative to the test file
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.join(current_dir, '..')
# Check if the parent directory is already in sys.path to avoid duplicates
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Now import the functions and config from init_db.py
# Assuming init_db.py is in the root of your project, one level up from test/
try:
    import init_db
except ImportError:
    # If the above import fails, it might be because pytest is run from the root
    # in which case init_db is directly importable.
    # This handles both scenarios depending on the exact command/setup.
    print("Warning: Could not import init_db directly. Trying absolute import.")
    pass # Let pytest handle the import if it's run from the root

# Define a fixture to mock the database connection and cursor
@pytest.fixture
def mock_db(monkeypatch):
    """Mocks psycopg2.connect and the resulting connection/cursor."""
    mock_connect = mock.MagicMock(spec=psycopg2.connect)
    mock_conn = mock.MagicMock(spec=psycopg2.extensions.connection)
    mock_cursor = mock.MagicMock(spec=psycopg2.extensions.cursor)

    # Configure mock_connect to return mock_conn
    mock_connect.return_value = mock_conn

    # Configure mock_conn.cursor() to return a context manager that yields mock_cursor
    # The standard way to mock 'with conn.cursor() as cur:' is to mock conn.cursor()
    # and configure its return value's __enter__ method.
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    # Also configure __exit__ for the context manager
    mock_conn.cursor.return_value.__exit__.return_value = False # Indicate no exception is handled

    # Patch psycopg2.connect in the init_db module
    # The target string must match how psycopg2 is imported *within* init_db.py
    # Since init_db.py does 'import psycopg2', the target is 'init_db.psycopg2.connect'
    monkeypatch.setattr('init_db.psycopg2.connect', mock_connect)

    yield {
        "connect": mock_connect,
        "conn": mock_conn,
        "cursor": mock_cursor,
        # Pass the actual DB_CONFIG for assertions if needed
        "db_config": init_db.DB_CONFIG
    }

    # No need for explicit cleanup with monkeypatch as it's handled by pytest


# Mock the insert_test_data function to isolate create_tables logic
# We will write separate tests for insert_test_data
@pytest.fixture
def mock_insert_test_data(monkeypatch):
    mock_insert = mock.MagicMock()
    monkeypatch.setattr('init_db.insert_test_data', mock_insert)
    yield mock_insert


# --- Tests for create_tables ---

def test_create_tables_success(mock_db, mock_insert_test_data, capsys):
    """Test create_tables runs successfully and executes commands."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    # Call the function under test
    init_db.create_tables()

    # Assertions for successful execution flow

    # 1. Check database connection was attempted with correct config
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])

    # 2. Check cursor was obtained via context manager
    mock_conn.cursor.assert_called_once()
    mock_conn.cursor.return_value.__enter__.assert_called_once()

    # 3. Check DROP and CREATE commands were executed
    executed_commands = [call[0][0] for call in mock_cursor.execute.call_args_list]
    assert "DROP TABLE IF EXISTS factura_items CASCADE" in executed_commands
    assert "DROP TABLE IF EXISTS facturas CASCADE" in executed_commands
    assert "DROP TABLE IF EXISTS productos CASCADE" in executed_commands
    assert "DROP TABLE IF EXISTS clientes CASCADE" in executed_commands
    assert "DROP SEQUENCE IF EXISTS factura_numero_seq" in executed_commands
    assert any("CREATE TABLE IF NOT EXISTS clientes" in cmd for cmd in executed_commands)
    assert any("CREATE TABLE IF NOT EXISTS productos" in cmd for cmd in executed_commands)
    assert any("CREATE TABLE IF NOT EXISTS facturas" in cmd for cmd in executed_commands)
    assert any("CREATE TABLE IF NOT EXISTS factura_items" in cmd for cmd in executed_commands)
    assert any("CREATE SEQUENCE IF NOT EXISTS factura_numero_seq" in cmd for cmd in executed_commands)
    assert mock_cursor.execute.call_count == len(init_db.commands) + 5 # 5 drops + N creates

    # 4. Check insert_test_data was called
    mock_insert_test_data.assert_called_once_with(mock_cursor)

    # 5. Check commit was called
    mock_conn.commit.assert_called_once()

    # 6. Check rollback was NOT called
    mock_conn.rollback.assert_not_called()

    # 7. Check connection was closed in finally block
    mock_conn.close.assert_called_once()

    # 8. Check success message was printed
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out
    assert "Error al crear tablas" not in captured.err # Ensure no error message


def test_create_tables_db_connection_error(mock_db, mock_insert_test_data, capsys):
    """Test create_tables handles database connection failure."""
    # Configure the connect mock to raise an error
    mock_db["connect"].side_effect = OperationalError("Simulated connection failed")

    # Call the function
    init_db.create_tables()

    # Assertions for connection failure
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])

    # Check that no cursor or connection methods were called after connect failed
    mock_db["conn"].cursor.assert_not_called()
    mock_db["conn"].close.assert_not_called()
    mock_db["conn"].commit.assert_not_called()
    mock_db["conn"].rollback.assert_not_called()
    mock_db["cursor"].execute.assert_not_called()
    mock_insert_test_data.assert_not_called()

    # Check error message was printed
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated connection failed" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out


def test_create_tables_sql_execution_error(mock_db, mock_insert_test_data, capsys):
    """Test create_tables handles an SQL execution error (e.g., ProgrammingError)."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    # Configure the cursor's execute method to raise an error after some successful calls
    # Let's make the first CREATE TABLE command fail (after the drops)
    error_command_index = 5 # Index 5 corresponds to the first CREATE TABLE command (after 5 drops)
    original_execute = mock_cursor.execute

    def side_effect_execute(command, *args, **kwargs):
        if mock_cursor.execute.call_count == error_command_index:
            raise ProgrammingError("Simulated SQL syntax error")
        # Call the original mock execute for other commands
        return original_execute(command, *args, **kwargs)

    mock_cursor.execute.side_effect = side_effect_execute

    # Call the function
    init_db.create_tables()

    # Assertions for SQL execution error

    # Check connection and cursor were obtained
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()

    # Check execute was called up to the point of failure
    assert mock_cursor.execute.call_count == error_command_index

    # Check insert_test_data was NOT called (because the error happened before)
    mock_insert_test_data.assert_not_called()

    # Check commit was NOT called
    mock_conn.commit.assert_not_called()

    # Check rollback was called (psycopg2 default behavior or explicit in error handler)
    mock_conn.rollback.assert_called_once()

    # Check connection was closed in finally block
    mock_conn.close.assert_called_once()

    # Check error message was printed
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated SQL syntax error" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out


def test_create_tables_insert_data_error(mock_db, capsys):
    """Test create_tables handles an error during the insert_test_data call."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    # Mock insert_test_data to raise an error
    mock_insert = mock.MagicMock(side_effect=Exception("Simulated insert data error"))
    mock.patch('init_db.insert_test_data', mock_insert).start() # Patch specifically for this test

    # Configure the mock cursor's fetchone for the initial COUNT(*) in insert_test_data
    # to ensure insert_test_data proceeds to the part that errors.
    mock_cursor.fetchone.return_value = (0,)

    # Call the function
    init_db.create_tables()

    # Assertions

    # Check connection, cursor, and all CREATE/DROP executes completed successfully
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()
    # We can roughly check execute call count is at least the number of CREATE/DROP commands
    assert mock_cursor.execute.call_count >= len(init_db.commands) + 5

    # Check insert_test_data was called
    mock_insert.assert_called_once_with(mock_cursor)

    # Check commit was NOT called
    mock_conn.commit.assert_not_called()

    # Check rollback was called
    mock_conn.rollback.assert_called_once()

    # Check connection was closed
    mock_conn.close.assert_called_once()

    # Check error message
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated insert data error" in captured.out


def test_create_tables_commit_error(mock_db, mock_insert_test_data, capsys):
    """Test create_tables handles an error during commit."""
    mock_conn = mock_db["conn"]

    # Configure commit to raise an error
    mock_conn.commit.side_effect = DatabaseError("Simulated commit error")

    # Call the function
    init_db.create_tables()

    # Assertions

    # Check connection, cursor, and executes completed successfully up to commit
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()
    assert mock_db["cursor"].execute.call_count >= len(init_db.commands) + 5 # Assumes all creates/drops/inserts execute before commit

    # Check insert_test_data was called
    mock_insert_test_data.assert_called_once()

    # Check commit was called (and failed)
    mock_conn.commit.assert_called_once()

    # Check rollback was called (often implied or explicitly handled after commit failure)
    # psycopg2 might not call rollback automatically if commit fails, depending on the error state.
    # If your actual error handling needed rollback here, you'd add it.
    # Based on the code, rollback isn't explicitly in the except block that catches commit error.
    # Let's assert it's NOT called based on the provided code.
    mock_conn.rollback.assert_not_called()


    # Check connection was closed in finally block
    mock_conn.close.assert_called_once()

    # Check error message
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated commit error" in captured.out


# --- Tests for insert_test_data ---

def test_insert_test_data_when_no_data_exists(mock_db):
    """Test insert_test_data inserts data when no clients exist."""
    mock_cursor = mock_db["cursor"]

    # Configure fetchone for the initial COUNT(*) query to return 0
    mock_cursor.fetchone.return_value = (0,)

    # Call the function under test (pass the mock cursor)
    init_db.insert_test_data(mock_cursor)

    # Assertions

    # Check the initial COUNT(*) query was executed
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once() # fetchone is only called for the count

    # Check INSERT queries were executed for clients
    assert mock_cursor.execute.call_count >= len(init_db.insert_test_data.__code__.co_consts[11]) # Rough check: number of client inserts + 1 for count
    assert any("INSERT INTO clientes" in call[0][0] for call in mock_cursor.execute.call_args_list)

    # Check INSERT queries were executed for products
    assert any("INSERT INTO productos" in call[0][0] for call in mock_cursor.execute.call_args_list)

    # Verify specific insert calls (optional, but more precise)
    client_inserts = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO clientes" in call[0][0]]
    assert len(client_inserts) == len(init_db.insert_test_data.__code__.co_consts[11]) # Check correct number of client inserts
    # You could add assertions to check the arguments passed to these calls

    product_inserts = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO productos" in call[0][0]]
    assert len(product_inserts) == len(init_db.insert_test_data.__code__.co_consts[12]) # Check correct number of product inserts


def test_insert_test_data_when_data_exists(mock_db):
    """Test insert_test_data does NOT insert data when clients already exist."""
    mock_cursor = mock_db["cursor"]

    # Configure fetchone for the initial COUNT(*) query to return > 0
    mock_cursor.fetchone.return_value = (5,) # Simulate 5 existing clients

    # Call the function under test
    init_db.insert_test_data(mock_cursor)

    # Assertions

    # Check the initial COUNT(*) query was executed
    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

    # Check that NO INSERT queries were executed
    execute_calls = [call[0][0] for call in mock_cursor.execute.call_args_list]
    assert "INSERT INTO clientes" not in execute_calls
    assert "INSERT INTO productos" not in execute_calls
    # Ensure no other execute calls happened beyond the initial count
    assert len(execute_calls) == 1


def test_insert_test_data_error_during_insert(mock_db):
    """Test insert_test_data handles an error during an insert query."""
    mock_cursor = mock_db["cursor"]

    # Configure fetchone for the initial COUNT(*) to allow inserts
    mock_cursor.fetchone.return_value = (0,)

    # Configure execute to raise an error after the count check but during inserts
    original_execute = mock_cursor.execute
    insert_error = ProgrammingError("Simulated insert error")

    def side_effect_execute(command, *args, **kwargs):
        if "INSERT INTO" in command:
             # Make the error happen on the first insert attempt
             raise insert_error
        # Allow the initial COUNT(*) query to run normally
        return original_execute(command, *args, **kwargs)

    mock_cursor.execute.side_effect = side_effect_execute

    # Call the function under test
    # insert_test_data itself doesn't have try...except, so it will re-raise the error
    with pytest.raises(ProgrammingError, match="Simulated insert error"):
        init_db.insert_test_data(mock_cursor)

    # Assertions
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    # Check that the failing INSERT query was attempted
    assert any("INSERT INTO clientes" in call[0][0] for call in mock_cursor.execute.call_args_list)
    # No need to check commit/rollback/close here, as those are handled by the caller (create_tables)


# --- Test for the __main__ block (Optional) ---
# Testing the if __name__ == '__main__': block directly is slightly more advanced.
# It usually involves patching the function being called within the block
# and simulating the script's execution environment.
# For this example, we trust that the standard Python entry point works
# and focus on testing the functions create_tables and insert_test_data themselves.

# Example (requires more complex patching setup if init_db is not a simple script):
# def test_main_calls_create_tables(monkeypatch):
#     mock_create = mock.MagicMock()
#     monkeypatch.setattr('init_db.create_tables', mock_create)
#
#     # Simulate running the script directly by setting __name__
#     original_name = init_db.__name__
#     init_db.__name__ = '__main__'
#
#     # Trigger the code in the if __name__ block
#     try:
#         # Need to re-import or somehow trigger the top-level execution
#         # This part is tricky and depends on the exact setup.
#         # Often involves mocking sys.modules and importing the module again.
#         # A simpler approach is sometimes to refactor the script into a main() function
#         # and call main() in the test.
#         pass # Placeholder
#     finally:
#         init_db.__name__ = original_name # Restore original name
#
#     # mock_create.assert_called_once() # Assert create_tables was called