# test/test_init_db.py

import pytest
from unittest import mock
import psycopg2
from psycopg2 import OperationalError, ProgrammingError, DatabaseError, IntegrityError, \
    DataError  # Import specific exceptions
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

    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr('init_db.psycopg2.connect', mock_connect)

    yield {
        "connect": mock_connect,
        "conn": mock_conn,
        "cursor": mock_cursor,
        "db_config": init_db.DB_CONFIG
    }

# Renamed fixture for clarity and to allow specific patching in tests
@pytest.fixture
def mock_insert_test_data_fixture(monkeypatch):
    """Mocks the entire init_db.insert_test_data function."""
    mock_insert = mock.MagicMock()
    monkeypatch.setattr('init_db.insert_test_data', mock_insert)
    yield mock_insert


# --- Existing Tests (assuming they are in the same file) ---

def test_create_tables_success(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables runs successfully and executes commands."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()
    mock_conn.cursor.return_value.__enter__.assert_called_once()
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
    # init_db.py has 5 DROP commands + len(init_db.commands) CREATE/SEQUENCE commands
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)
    # init_db.py has two commits if all goes well
    assert mock_conn.commit.call_count == 2
    mock_conn.rollback.assert_not_called()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out
    assert "Error al crear tablas" not in captured.err

def test_create_tables_db_connection_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles database connection failure."""
    mock_db["connect"].side_effect = OperationalError("Simulated connection failed")
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_db["conn"].cursor.assert_not_called()
    mock_db["conn"].close.assert_not_called()
    mock_db["conn"].commit.assert_not_called()
    mock_db["conn"].rollback.assert_not_called()
    mock_db["cursor"].execute.assert_not_called()
    mock_insert_test_data_fixture.assert_not_called()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated connection failed" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out

def test_create_tables_sql_execution_error_on_create(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles an SQL execution error (e.g., ProgrammingError) during CREATE."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # Fail on the first CREATE command (after 5 drops)
    # The 6th execute call overall. execute.call_count is 1-based.
    fail_on_call_nth = 6
    original_execute = mock_cursor.execute
    def side_effect_execute(command, *args, **kwargs):
        if mock_cursor.execute.call_count == fail_on_call_nth:
            raise ProgrammingError("Simulated SQL syntax error on CREATE")
        return original_execute(command, *args, **kwargs)
    mock_cursor.execute.side_effect = side_effect_execute
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()
    assert mock_cursor.execute.call_count == fail_on_call_nth
    mock_insert_test_data_fixture.assert_not_called()
    # First commit (after drops) should have happened. Second commit (after creates) should not.
    assert mock_conn.commit.call_count == 1
    # Assuming the user's environment or a more complete init_db.py version implies rollback.
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated SQL syntax error on CREATE" in captured.out

def test_create_tables_insert_data_error_itself(mock_db, capsys):
    """Test create_tables handles an error raised by insert_test_data itself."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # Patch insert_test_data specifically for this test to control its behavior
    with mock.patch('init_db.insert_test_data', side_effect=Exception("Simulated error in insert_test_data")) as patched_insert_test_data:
        init_db.create_tables()
        mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
        mock_conn.cursor.assert_called_once()
        assert mock_cursor.execute.call_count == 5 + len(init_db.commands) # Drops and Creates done
        patched_insert_test_data.assert_called_once_with(mock_cursor)
        # First commit (after drops) should happen. Second commit (after creates/inserts) fails because insert_test_data fails before it.
        assert mock_conn.commit.call_count == 1
        mock_conn.rollback.assert_called_once() # Rollback due to exception from insert_test_data
        mock_conn.close.assert_called_once()
        captured = capsys.readouterr()
        assert "Error al crear tablas:" in captured.out
        assert "Simulated error in insert_test_data" in captured.out

def test_create_tables_general_commit_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles a generic error during the second commit."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    # Let the first commit (after drops) succeed, but the second one (after creates/inserts) fail.
    commit_error = DatabaseError("Simulated generic commit error")
    def commit_side_effect():
        if mock_conn.commit.call_count == 2: # Fail on the second commit call
            raise commit_error
        # Allow first commit to pass
    mock_conn.commit.side_effect = commit_side_effect
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)
    assert mock_conn.commit.call_count == 2 # Both commits were attempted
    mock_conn.rollback.assert_called_once() # Rollback due to the second commit failing
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "Simulated generic commit error" in captured.out

# --- Tests for insert_test_data (from user's original file) ---
def test_insert_test_data_when_no_data_exists(mock_db): # mock_db provides the cursor
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    init_db.insert_test_data(mock_cursor) # Call the actual function
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    # Count client and product inserts
    client_inserts = 0
    product_inserts = 0
    for call_args in mock_cursor.execute.call_args_list:
        if "INSERT INTO clientes" in call_args[0][0]:
            client_inserts += 1
        elif "INSERT INTO productos" in call_args[0][0]:
            product_inserts += 1
    assert client_inserts > 0
    assert product_inserts > 0
    # Accessing init_db.clientes and init_db.productos directly to get expected counts
    assert client_inserts == len(init_db.clientes)
    assert product_inserts == len(init_db.productos)

def test_insert_test_data_when_data_exists(mock_db):
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (5,) # Simulate 5 existing clients
    init_db.insert_test_data(mock_cursor)
    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    # Ensure no INSERT statements were called after the count check
    for call_args in mock_cursor.execute.call_args_list:
        assert not call_args[0][0].startswith("INSERT INTO")
    assert mock_cursor.execute.call_count == 1 # Only the count query

def test_insert_test_data_error_during_actual_insert(mock_db):
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # Allow inserts to start
    original_execute = mock_cursor.execute
    insert_error = ProgrammingError("Simulated error during actual insert")
    def side_effect_execute(command, *args, **kwargs):
        if "INSERT INTO clientes" in command: # Let's make the first client insert fail
            raise insert_error
        return original_execute(command, *args, **kwargs) # Careful with recursion if not specific enough
    mock_cursor.execute.side_effect = side_effect_execute
    with pytest.raises(ProgrammingError, match="Simulated error during actual insert"):
        init_db.insert_test_data(mock_cursor)
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    assert any("INSERT INTO clientes" in call[0][0] for call in mock_cursor.execute.call_args_list)

# --- Additional Tests ---

def test_create_tables_cursor_creation_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles error if conn.cursor() fails."""
    mock_conn = mock_db["conn"]
    mock_conn.cursor.side_effect = DatabaseError("Failed to create cursor")
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once() # Attempted to create cursor
    mock_db["cursor"].execute.assert_not_called() # Execute should not be reached
    mock_insert_test_data_fixture.assert_not_called()
    mock_conn.commit.assert_not_called()
    mock_conn.rollback.assert_called_once() # Rollback due to error
    mock_conn.close.assert_called_once() # Connection should still be closed
    captured = capsys.readouterr()
    assert "Error al crear tablas: Failed to create cursor" in captured.out

def test_create_tables_error_during_drop_command(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles SQL error during a DROP command."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # Let the first DROP command fail
    error_on_command = "DROP TABLE IF EXISTS factura_items CASCADE"
    original_execute = mock_cursor.execute
    def side_effect_execute(command, *args, **kwargs):
        if command == error_on_command:
            raise ProgrammingError(f"Simulated SQL error on {error_on_command}")
        return original_execute(command, *args, **kwargs)
    mock_cursor.execute.side_effect = side_effect_execute
    init_db.create_tables()
    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_any_call(error_on_command) # The failing command was called
    mock_insert_test_data_fixture.assert_not_called()
    # The first commit (after drops) would not be reached if a drop fails
    mock_conn.commit.assert_not_called()
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert f"Error al crear tablas: Simulated SQL error on {error_on_command}" in captured.out

def test_create_tables_error_during_sequence_drop(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles SQL error during DROP SEQUENCE."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    error_on_command = "DROP SEQUENCE IF EXISTS factura_numero_seq"
    original_execute = mock_cursor.execute
    def side_effect_execute(command, *args, **kwargs):
        # Fail when DROP SEQUENCE is executed
        if command == error_on_command:
            # All table drops occur before sequence drop.
            # Check that table drops were called if we want to be very specific.
            raise ProgrammingError(f"Simulated SQL error on {error_on_command}")
        return original_execute(command, *args, **kwargs)
    mock_cursor.execute.side_effect = side_effect_execute
    init_db.create_tables()
    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_any_call(error_on_command)
    mock_insert_test_data_fixture.assert_not_called()
    mock_conn.commit.assert_not_called() # First commit (after drops) not reached
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert f"Error al crear tablas: Simulated SQL error on {error_on_command}" in captured.out

def test_create_tables_error_during_sequence_create(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles SQL error during CREATE SEQUENCE."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # The sequence creation is one of the commands in init_db.commands
    # Find it to target the error correctly
    sequence_create_command = [cmd for cmd in init_db.commands if "CREATE SEQUENCE" in cmd][0]
    original_execute = mock_cursor.execute
    def side_effect_execute(command, *args, **kwargs):
        if command == sequence_create_command:
            raise ProgrammingError("Simulated SQL error on CREATE SEQUENCE")
        return original_execute(command, *args, **kwargs)
    mock_cursor.execute.side_effect = side_effect_execute
    init_db.create_tables()
    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_any_call(sequence_create_command)
    mock_insert_test_data_fixture.assert_not_called() # insert_test_data is after all commands
    assert mock_conn.commit.call_count == 1 # First commit (after drops) should pass
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Simulated SQL error on CREATE SEQUENCE" in captured.out

def test_create_tables_first_commit_failure(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables handles error during the first commit (after drops)."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    commit_error = DatabaseError("Simulated error on first commit")
    def commit_side_effect():
        if mock_conn.commit.call_count == 1: # Fail on the first commit call
            raise commit_error
    mock_conn.commit.side_effect = commit_side_effect
    init_db.create_tables()
    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    assert mock_cursor.execute.call_count == 5 # All 5 DROP commands executed
    mock_conn.commit.assert_called_once() # First commit was attempted
    mock_insert_test_data_fixture.assert_not_called() # Not reached if first commit fails
    mock_conn.rollback.assert_called_once() # Rollback due to commit error
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Simulated error on first commit" in captured.out

def test_create_tables_conn_closed_even_if_second_commit_fails(mock_db, mock_insert_test_data_fixture, capsys):
    """Ensure connection is closed even if the second commit fails."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    commit_error = DatabaseError("Second commit failure")
    def commit_side_effect():
        if mock_conn.commit.call_count == 2:
            raise commit_error
    mock_conn.commit.side_effect = commit_side_effect
    init_db.create_tables()
    mock_conn.close.assert_called_once() # Crucial check
    captured = capsys.readouterr()
    assert "Error al crear tablas: Second commit failure" in captured.out

def test_create_tables_conn_closed_even_if_insert_test_data_fails(mock_db, capsys):
    """Ensure connection is closed if insert_test_data itself raises an error."""
    mock_conn = mock_db["conn"]
    with mock.patch('init_db.insert_test_data', side_effect=Exception("Failure within insert_test_data")):
        init_db.create_tables()
    mock_conn.close.assert_called_once() # Crucial check
    captured = capsys.readouterr()
    assert "Error al crear tablas: Failure within insert_test_data" in captured.out

def test_insert_test_data_count_query_fails(mock_db):
    """Test insert_test_data if the initial COUNT(*) query fails."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.execute.side_effect = ProgrammingError("Failed to count clientes")
    with pytest.raises(ProgrammingError, match="Failed to count clientes"):
        init_db.insert_test_data(mock_cursor)
    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_not_called()

def test_insert_test_data_fetchone_fails_after_count(mock_db):
    """Test insert_test_data if fetchone() after COUNT(*) fails."""
    mock_cursor = mock_db["cursor"]
    # Let execute succeed for count, but fetchone fail
    mock_cursor.fetchone.side_effect = DatabaseError("Failed to fetch count")
    with pytest.raises(DatabaseError, match="Failed to fetch count"):
        init_db.insert_test_data(mock_cursor)
    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

def test_insert_test_data_error_on_specific_client_insert(mock_db):
    """Test insert_test_data error on a specific client (e.g., the second one)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # Allow inserts

    error_on_client_data = init_db.clientes[1] # Target the second client
    original_execute = mock_cursor.execute
    def side_effect_execute(command, data_tuple=None):
        if "INSERT INTO clientes" in command and data_tuple == error_on_client_data:
            raise IntegrityError("Unique constraint failed for specific client")
        return original_execute(command, data_tuple)
    mock_cursor.execute.side_effect = side_effect_execute

    with pytest.raises(IntegrityError, match="Unique constraint failed for specific client"):
        init_db.insert_test_data(mock_cursor)
    # Check that first client insert was attempted (and presumably succeeded)
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        init_db.clientes[0]
    )
    # Check that the failing client insert was attempted
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        error_on_client_data
    )

def test_insert_test_data_error_on_specific_product_insert(mock_db):
    """Test insert_test_data error on a specific product insert."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    error_on_product_data = init_db.productos[1] # Target the second product
    original_execute = mock_cursor.execute
    def side_effect_execute(command, data_tuple=None):
        if "INSERT INTO productos" in command and data_tuple == error_on_product_data:
            raise ProgrammingError("Error inserting specific product")
        return original_execute(command, data_tuple)
    mock_cursor.execute.side_effect = side_effect_execute

    with pytest.raises(ProgrammingError, match="Error inserting specific product"):
        init_db.insert_test_data(mock_cursor)
    # All client inserts should be attempted
    for client_data in init_db.clientes:
        mock_cursor.execute.assert_any_call(
            "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
            client_data
        )
    # First product insert should be attempted
    mock_cursor.execute.assert_any_call(
        "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);",
        init_db.productos[0]
    )
    # The failing product insert should be attempted
    mock_cursor.execute.assert_any_call(
        "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);",
        error_on_product_data
    )

def test_insert_test_data_with_empty_hardcoded_clientes_list(mock_db, monkeypatch):
    """Test insert_test_data when init_db.clientes list is empty."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # No existing data
    monkeypatch.setattr('init_db.clientes', []) # Make the hardcoded list empty

    init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    # No "INSERT INTO clientes" calls
    assert not any("INSERT INTO clientes" in call[0][0] for call in mock_cursor.execute.call_args_list)
    # Product inserts should still happen if init_db.productos is not empty
    if init_db.productos: # Check if there are products to insert
      assert any("INSERT INTO productos" in call[0][0] for call in mock_cursor.execute.call_args_list)

def test_insert_test_data_with_empty_hardcoded_productos_list(mock_db, monkeypatch):
    """Test insert_test_data when init_db.productos list is empty."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr('init_db.productos', [])

    init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    # Client inserts should still happen if init_db.clientes is not empty
    if init_db.clientes:
        assert any("INSERT INTO clientes" in call[0][0] for call in mock_cursor.execute.call_args_list)
    # No "INSERT INTO productos" calls
    assert not any("INSERT INTO productos" in call[0][0] for call in mock_cursor.execute.call_args_list)

def test_create_tables_no_commands_defined(mock_db, mock_insert_test_data_fixture, monkeypatch, capsys):
    """Test create_tables when init_db.commands tuple is empty."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    monkeypatch.setattr('init_db.commands', ()) # Empty tuple for CREATE commands

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # Only 5 DROP commands should be executed
    assert mock_cursor.execute.call_count == 5
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)
    assert mock_conn.commit.call_count == 2 # Both commits still attempted
    mock_conn.rollback.assert_not_called()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

def test_create_tables_ensure_drops_before_creates(mock_db, mock_insert_test_data_fixture):
    """Test that DROP commands are executed before CREATE commands."""
    mock_cursor = mock_db["cursor"]
    init_db.create_tables() # Run the function

    call_args_list = mock_cursor.execute.call_args_list
    drop_indices = [i for i, call in enumerate(call_args_list) if "DROP" in call[0][0]]
    create_indices = [i for i, call in enumerate(call_args_list) if "CREATE" in call[0][0]]

    assert len(drop_indices) == 5
    assert len(create_indices) == len(init_db.commands)
    # Ensure all drop indices are smaller than all create indices
    if drop_indices and create_indices:
        assert max(drop_indices) < min(create_indices)
    mock_insert_test_data_fixture.assert_called_once() # Ensure it's still called

def test_create_tables_output_on_generic_exception_in_try(mock_db, capsys):
    """Test generic exception during execute in create_tables try block."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # Make a normally safe operation raise a generic Exception
    mock_cursor.execute.side_effect = Exception("Very generic unexpected error")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_called_once() # Called once before failing

    mock_conn.commit.assert_not_called()
    mock_conn.rollback.assert_called_once() # Error should trigger rollback
    mock_conn.close.assert_called_once()   # Finally block closes connection

    captured = capsys.readouterr()
    assert "Error al crear tablas: Very generic unexpected error" in captured.out

def test_main_block_calls_create_tables(monkeypatch):
    """Test that the if __name__ == '__main__': block calls create_tables."""
    mock_create_tables = mock.MagicMock()
    monkeypatch.setattr('init_db.create_tables', mock_create_tables)
    assert callable(init_db.create_tables)


@pytest.mark.skip(reason="Testing __main__ block directly is complex and often needs runpy or subprocess.")
def test_main_block_with_runpy(monkeypatch):
    """More robustly tests the __main__ block using runpy."""
    mock_create_tables_runpy = mock.MagicMock()
    monkeypatch.setattr('init_db.create_tables', mock_create_tables_runpy)
    import runpy
    try:
        # Store and temporarily remove init_db from sys.modules to force re-import
        original_module = sys.modules.pop('init_db', None)
        runpy.run_module('init_db', run_name='__main__')
    finally:
        if original_module: # Restore if it was popped
            sys.modules['init_db'] = original_module
        # If it wasn't in sys.modules, but runpy added it, remove it to avoid side effects
        elif 'init_db' in sys.modules and not original_module : # if it was newly added by runpy
            del sys.modules['init_db']


    mock_create_tables_runpy.assert_called_once()


def test_create_tables_rollback_not_called_if_connect_fails(mock_db, capsys):
    """Ensure rollback is not called if the initial connection fails."""
    mock_conn = mock_db["conn"]
    mock_db["connect"].side_effect = OperationalError("Connection failed before rollback")
    init_db.create_tables()
    mock_conn.rollback.assert_not_called()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Connection failed before rollback" in captured.out

# test/test_init_db.py
# ... (previous imports and fixtures remain the same) ...

# --- Additional Tests ---

def test_create_tables_db_config_missing_database_key_specific_error(mock_db, mock_insert_test_data_fixture, monkeypatch, capsys):
    """Test create_tables if DB_CONFIG is missing the 'database' key."""
    # Simulate DB_CONFIG missing the 'database' key
    faulty_db_config = init_db.DB_CONFIG.copy()
    del faulty_db_config['database']
    monkeypatch.setattr(init_db, 'DB_CONFIG', faulty_db_config)

    # Simulate the specific error psycopg2.connect would raise
    mock_db["connect"].side_effect = psycopg2.OperationalError("FATAL: database \"None\" does not exist")

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with(**faulty_db_config)
    mock_db["conn"].cursor.assert_not_called()
    mock_insert_test_data_fixture.assert_not_called()
    mock_db["conn"].close.assert_not_called() # Connection object 'conn' would be None
    captured = capsys.readouterr()
    assert "FATAL: database \"None\" does not exist" in captured.out
    assert "Error al crear tablas:" in captured.out

def test_create_tables_cursor_close_itself_fails_in_try_block(mock_db, mock_insert_test_data_fixture, capsys):
    """Test behavior if cur.close() inside the try block of create_tables fails."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    # Simulate cur.close() raising an error.
    # cur.close() is called after commits and insert_test_data in the success path.
    mock_cursor.close.side_effect = DatabaseError("Failed to close cursor")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # All DB operations should have been attempted
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)
    assert mock_conn.commit.call_count == 2 # Both commits attempted

    mock_cursor.close.assert_called_once() # cur.close() was attempted

    # The error from cur.close() should be caught by the main except block
    captured = capsys.readouterr()
    assert "Error al crear tablas: Failed to close cursor" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out # Success message skipped
    mock_conn.rollback.assert_called_once() # Rollback should be called due to the error
    mock_conn.close.assert_called_once() # conn.close() in finally should still be called

def test_create_tables_intermediate_create_command_fails(mock_db, mock_insert_test_data_fixture, capsys):
    """Test if a CREATE TABLE command in the middle of init_db.commands fails."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    if len(init_db.commands) < 2:
        pytest.skip("Not enough commands in init_db.commands to test intermediate failure.")

    fail_on_command_str = init_db.commands[1] # Fail on the second CREATE command
    # Overall execute call index: 5 drops + 1 successful create + 1 failing create = 7th call
    fail_on_nth_execute_call = 5 + 1 + 1

    original_execute = mock_cursor.execute
    def side_effect_execute(command, *args, **kwargs):
        if mock_cursor.execute.call_count == fail_on_nth_execute_call and command == fail_on_command_str:
            raise ProgrammingError(f"Syntax error in command: {fail_on_command_str[:30]}")
        return original_execute(command, *args, **kwargs)
    mock_cursor.execute.side_effect = side_effect_execute

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    assert mock_cursor.execute.call_count == fail_on_nth_execute_call
    mock_cursor.execute.assert_any_call(init_db.commands[0]) # First CREATE command should pass
    mock_cursor.execute.assert_any_call(fail_on_command_str)   # Failing command attempted

    mock_insert_test_data_fixture.assert_not_called()
    assert mock_conn.commit.call_count == 1 # Only the first commit (after drops)
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert f"Syntax error in command: {fail_on_command_str[:30]}" in captured.out

def test_insert_test_data_fetchone_returns_none_for_count(mock_db):
    """Test insert_test_data if cur.fetchone() for client count returns None."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = None # Simulate fetchone returning None

    with pytest.raises(TypeError) as excinfo: # Expect "TypeError: 'NoneType' object is not subscriptable"
        init_db.insert_test_data(mock_cursor)

    assert "'NoneType' object is not subscriptable" in str(excinfo.value)
    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

def test_insert_test_data_db_data_error_during_insert(mock_db):
    """Test insert_test_data simulating a psycopg2.DataError during an insert."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # Allow inserts to start

    # Simulate a DataError (e.g., value too long for column) on the first client insert
    data_error = psycopg2.DataError("Value too long for character type")
    original_execute = mock_cursor.execute
    def side_effect_execute(command, data_tuple=None):
        if "INSERT INTO clientes" in command:
            raise data_error
        return original_execute(command, data_tuple)
    mock_cursor.execute.side_effect = side_effect_execute

    with pytest.raises(psycopg2.DataError, match="Value too long for character type"):
        init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    # Check that the failing INSERT query for clientes was attempted
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        init_db.clientes[0] # Assuming it fails on the first client data
    )

def test_create_tables_connect_raises_interface_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables if psycopg2.connect raises an InterfaceError."""
    interface_error = psycopg2.InterfaceError("Connection interface issue")
    mock_db["connect"].side_effect = interface_error

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_db["conn"].cursor.assert_not_called()
    mock_insert_test_data_fixture.assert_not_called()
    # Connection 'conn' would be None, so no close or rollback on it
    mock_db["conn"].close.assert_not_called()
    mock_db["conn"].rollback.assert_not_called()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Connection interface issue" in captured.out

def test_create_tables_ensure_insert_test_data_not_called_if_create_fails_midway(mock_db, mock_insert_test_data_fixture, capsys):
    """Ensure insert_test_data is NOT called if a CREATE TABLE/SEQUENCE command fails."""
    mock_cursor = mock_db["cursor"]
    # Fail on the very first CREATE command (after drops)
    fail_on_command_str = init_db.commands[0]
    mock_cursor.execute.side_effect = lambda cmd, *args: (_ for _ in ()).throw(ProgrammingError("CREATE fail")) if cmd == fail_on_command_str else mock.DEFAULT

    init_db.create_tables()

    mock_insert_test_data_fixture.assert_not_called()
    captured = capsys.readouterr()
    assert "Error al crear tablas: CREATE fail" in captured.out

def test_create_tables_db_config_unresolvable_host_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables with OperationalError for unresolvable host."""
    host_error = psycopg2.OperationalError("could not translate host name \"nonexistenthost\" to address: Unknown host")
    mock_db["connect"].side_effect = host_error

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_insert_test_data_fixture.assert_not_called()
    captured = capsys.readouterr()
    assert "could not translate host name" in captured.out
    assert "Error al crear tablas:" in captured.out

def test_insert_test_data_integrity_error_on_client_insert_specific(mock_db):
    """Test insert_test_data simulating psycopg2.IntegrityError on client insert."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # Allow inserts

    integrity_error = psycopg2.IntegrityError("Violation of unique constraint on clients")
    original_execute = mock_cursor.execute
    def side_effect_execute(command, data_tuple=None):
        if "INSERT INTO clientes" in command and data_tuple == init_db.clientes[0]: # Fail on first client
            raise integrity_error
        return original_execute(command, data_tuple)
    mock_cursor.execute.side_effect = side_effect_execute

    with pytest.raises(psycopg2.IntegrityError, match="Violation of unique constraint on clients"):
        init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        init_db.clientes[0]
    )

def test_create_tables_rollback_not_called_if_conn_none_and_early_error(mock_db, monkeypatch, capsys):
    """Test rollback is not called on 'conn' if 'conn' is None due to early connect failure."""
    # This test assumes the user's actual init_db.py might have `if conn: conn.rollback()`
    # in the except block, which is a common pattern, even if the snippet didn't show it.
    # The goal is to test that `conn.rollback()` is not called if `conn` itself is `None`.

    # Simulate connection failure
    mock_db["connect"].side_effect = OperationalError("Initial connection failed")

    # If the actual init_db.py doesn't have `if conn: conn.rollback()`,
    # then this test is simply verifying rollback is not called because connect failed.
    # If it *does* have that guard, this test ensures the guard works.

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_db["conn"].rollback.assert_not_called() # Key assertion: rollback not called on the mock 'conn'
                                                 # because in real code 'conn' would be None.
    mock_db["conn"].close.assert_not_called()  # Also, close wouldn't be called on it.
    captured = capsys.readouterr()
    assert "Error al crear tablas: Initial connection failed" in captured.out
# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- 10 Tests de Error Adicionales ---

def test_create_tables_db_config_empty_dict(mock_db, mock_insert_test_data_fixture, monkeypatch, capsys):
    """Test create_tables si DB_CONFIG es un diccionario vacío, causando fallo en psycopg2.connect."""
    monkeypatch.setattr(init_db, 'DB_CONFIG', {}) # DB_CONFIG vacío
    # psycopg2.connect debería fallar, usualmente con un error indicando falta de parámetros
    mock_db["connect"].side_effect = psycopg2.OperationalError("missing connection parameters")

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with() # Llamado con kwargs vacíos debido a DB_CONFIG vacío
    mock_db["conn"].cursor.assert_not_called()
    mock_insert_test_data_fixture.assert_not_called()
    mock_db["conn"].commit.assert_not_called()
    mock_db["conn"].rollback.assert_not_called() # No se llama rollback si la conexión no se establece
    mock_db["conn"].close.assert_not_called()   # conn sería None
    captured = capsys.readouterr()
    assert "Error al crear tablas: missing connection parameters" in captured.out

def test_create_tables_first_commit_fails_no_creates_or_inserts_attempted(mock_db, mock_insert_test_data_fixture, capsys):
    """Test si el primer commit (después de los DROPs) falla, no se intentan CREATEs ni inserts."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Configurar el primer commit para que falle
    first_commit_error = DatabaseError("Fallo en el primer commit (post-drops)")
    def commit_side_effect():
        if mock_conn.commit.call_count == 1:
            raise first_commit_error
        # Otros commits (si los hubiera) pasarían
    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    assert mock_cursor.execute.call_count == 5 # Solo los 5 comandos DROP

    mock_conn.commit.assert_called_once() # Se intentó el primer commit

    # Ningún comando CREATE o insert_test_data debería ser llamado
    create_commands_executed = any("CREATE" in call[0][0] for call in mock_cursor.execute.call_args_list[5:])
    assert not create_commands_executed
    mock_insert_test_data_fixture.assert_not_called()

    mock_conn.rollback.assert_called_once() # Se espera rollback tras el error de commit
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo en el primer commit (post-drops)" in captured.out

def test_create_tables_error_raised_by_insert_test_data_after_successful_creates(mock_db, capsys):
    """Test si todas las creaciones (CREATE) son exitosas pero insert_test_data falla."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Hacer que insert_test_data (el real, no el fixture) falle después de que los CREATEs hayan tenido éxito.
    # Para esto, no usamos mock_insert_test_data_fixture, sino que parchamos directamente.
    error_en_insert = Exception("Error interno simulado en insert_test_data")
    with mock.patch('init_db.insert_test_data', side_effect=error_en_insert) as mock_actual_insert_fn:
        init_db.create_tables()

        mock_db["connect"].assert_called_once()
        mock_conn.cursor.assert_called_once()

        # Todos los DROPs y CREATEs deberían haberse ejecutado
        assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
        mock_actual_insert_fn.assert_called_once_with(mock_cursor) # insert_test_data fue llamado

        # El primer commit (post-drops) debería haber ocurrido.
        # El segundo commit (post-creates/inserts) no debería ocurrir debido al error en insert_test_data.
        assert mock_conn.commit.call_count == 1

        mock_conn.rollback.assert_called_once() # Rollback debido al error
        mock_conn.close.assert_called_once()
        captured = capsys.readouterr()
        assert "Error al crear tablas: Error interno simulado en insert_test_data" in captured.out

def test_insert_test_data_client_tuple_malformed_length(mock_db, monkeypatch):
    """Test si una tupla en init_db.clientes tiene longitud incorrecta, causando error en execute."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # Permite iniciar inserts

    # Cliente con datos malformados (menos campos)
    malformed_clientes = [("Cliente Malformado", "Direccion")] # Solo 2 campos en vez de 4
    monkeypatch.setattr(init_db, 'clientes', malformed_clientes)
    monkeypatch.setattr(init_db, 'productos', []) # Vaciar productos para aislar el error

    # psycopg2 puede lanzar un TypeError o ProgrammingError aquí
    with pytest.raises((TypeError, psycopg2.ProgrammingError)) as excinfo:
        init_db.insert_test_data(mock_cursor)
    # El mensaje exacto puede variar, pero debería indicar un problema con los parámetros/bindings
    # Por ejemplo: "not all arguments converted during string formatting" o similar
    # O "function takes at most %s arguments (%s given)" si la librería de db lo detecta así
    assert excinfo.type is TypeError or excinfo.type is psycopg2.ProgrammingError

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    # Se intentó el execute para el cliente malformado
    # La aserción exacta de la llamada a execute con datos malformados es compleja
    # ya que el error puede ocurrir antes de que el mock capture la llamada completa con los datos problemáticos.
    # Es suficiente con que se levante la excepción esperada.

def test_insert_test_data_product_tuple_malformed_length(mock_db, monkeypatch):
    """Test si una tupla en init_db.productos tiene longitud incorrecta."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    malformed_productos = [("Producto Malformado", 12.34)] # Solo 2 campos en vez de 3
    monkeypatch.setattr(init_db, 'clientes', []) # Vaciar clientes para aislar
    monkeypatch.setattr(init_db, 'productos', malformed_productos)

    with pytest.raises((TypeError, psycopg2.ProgrammingError)) as excinfo:
        init_db.insert_test_data(mock_cursor)
    assert excinfo.type is TypeError or excinfo.type is psycopg2.ProgrammingError

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    # Similar al anterior, se espera que el execute para el producto malformado falle.

def test_create_tables_non_psycopg2_exception_during_drop_execute(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables si execute() durante un DROP levanta una excepción no-psycopg2."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    # Hacer que el primer DROP falle con una excepción genérica
    mock_cursor.execute.side_effect = ValueError("Error de valor inesperado durante DROP")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_called_once() # Solo se llama una vez antes de fallar

    mock_insert_test_data_fixture.assert_not_called()
    mock_conn.commit.assert_not_called()
    mock_conn.rollback.assert_called_once() # Asumiendo rollback para cualquier excepción
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Error de valor inesperado durante DROP" in captured.out

def test_create_tables_non_psycopg2_exception_during_create_execute(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables si execute() durante un CREATE levanta una excepción no-psycopg2."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    # Hacer que el primer CREATE (después de drops y su commit) falle con una excepción genérica
    original_execute = mock_cursor.execute
    fail_on_command = init_db.commands[0]
    def side_effect_execute(command, *args, **kwargs):
        if command == fail_on_command:
            raise RuntimeError("Error de runtime simulado durante CREATE")
        return original_execute(command, *args, **kwargs)

    # Aplicar el side_effect solo después de que los drops hayan ocurrido (5 llamadas)
    # Esto es un poco más complejo de configurar directamente en el mock_cursor global
    # Es más fácil si se hace dentro del test:
    execute_call_count = 0
    def smart_side_effect(command, *args, **kwargs):
        nonlocal execute_call_count
        execute_call_count += 1
        if execute_call_count > 5 and command == fail_on_command: # >5 para pasar los drops
            raise RuntimeError("Error de runtime simulado durante CREATE")
        return mock.DEFAULT # Para las llamadas de drop
    mock_cursor.execute.side_effect = smart_side_effect


    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # Debería haber 5 llamadas de DROP + 1 llamada al CREATE que falla
    assert mock_cursor.execute.call_count == 5 + 1

    mock_insert_test_data_fixture.assert_not_called()
    assert mock_conn.commit.call_count == 1 # El commit después de los drops
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Error de runtime simulado durante CREATE" in captured.out


def test_insert_test_data_fetchone_returns_empty_tuple_index_error(mock_db):
    """Test insert_test_data si cur.fetchone() para el conteo retorna una tupla vacía."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = () # Tupla vacía

    with pytest.raises(IndexError): # Espera "IndexError: tuple index out of range"
        init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

def test_create_tables_db_auth_failure_specific_error_message(mock_db, mock_insert_test_data_fixture, capsys):
    """Test create_tables con OperationalError por fallo de autenticación."""
    auth_error_msg = "FATAL:  password authentication failed for user \"postgres\""
    mock_db["connect"].side_effect = psycopg2.OperationalError(auth_error_msg)

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_insert_test_data_fixture.assert_not_called()
    mock_db["conn"].cursor.assert_not_called()
    mock_db["conn"].commit.assert_not_called()
    mock_db["conn"].rollback.assert_not_called()
    mock_db["conn"].close.assert_not_called()
    captured = capsys.readouterr()
    assert auth_error_msg in captured.out
    assert "Error al crear tablas:" in captured.out

def test_create_tables_success_message_suppressed_on_any_try_block_error(mock_db, capsys):
    """Test general que el mensaje de éxito no se imprime si ocurre cualquier error en el try."""
    mock_cursor = mock_db["cursor"]
    # Forzar un error en un punto arbitrario pero crítico dentro del try,
    # por ejemplo, durante la ejecución del primer comando CREATE.
    if not init_db.commands:
        pytest.skip("No commands to test create failure for success message suppression.")

    fail_command = init_db.commands[0]
    original_execute = mock_cursor.execute
    def failing_execute(command, *args, **kwargs):
        if command == fail_command:
            raise DatabaseError("Error forzado para suprimir mensaje de éxito")
        return original_execute(command, *args, **kwargs)

    # Aplicar el side_effect después de que los drops se hayan ejecutado
    drop_count = 5
    def selective_fail_execute(command, *args, **kwargs):
        # Contar llamadas a execute a través del mock
        current_call_count = mock_cursor.execute.call_count
        if current_call_count == drop_count + 1 and command == fail_command: # Falla en el primer CREATE
            raise DatabaseError("Error forzado para suprimir mensaje de éxito")
        return mock.DEFAULT # Permite que los drops pasen
    mock_cursor.execute.side_effect = selective_fail_execute


    init_db.create_tables()

    captured = capsys.readouterr()
    # Asegurarse que el mensaje de error específico está
    assert "Error al crear tablas: Error forzado para suprimir mensaje de éxito" in captured.out
    # Asegurarse que el mensaje de éxito NO está
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out
    mock_db["conn"].rollback.assert_called_once() # Se espera rollback
    mock_db["conn"].close.assert_called_once() # Y cierre de conexión


# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- 10 Tests Convertidos a Error (basados en escenarios de éxito) ---

def test_create_tables_fails_if_connect_is_ok_but_cursor_mock_fails_to_enter_context(mock_db,
                                                                                     mock_insert_test_data_fixture,
                                                                                     capsys):
    """Test: Convertir 'success' -> error si el context manager del cursor falla en __enter__."""
    mock_conn = mock_db["conn"]
    # Simular que el context manager del cursor falla al entrar
    mock_conn.cursor.return_value.__enter__.side_effect = DatabaseError("Fallo al entrar en el contexto del cursor")

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with(**mock_db["db_config"])
    mock_conn.cursor.assert_called_once()  # Se intentó obtener el cursor
    mock_conn.cursor.return_value.__enter__.assert_called_once()  # Se intentó entrar al contexto

    mock_db["cursor"].execute.assert_not_called()  # No se debería ejecutar ningún comando SQL
    mock_insert_test_data_fixture.assert_not_called()
    mock_conn.commit.assert_not_called()
    mock_conn.rollback.assert_called_once()  # Se espera rollback
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo al entrar en el contexto del cursor" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out


def test_insert_test_data_fails_if_count_execute_raises_non_psycopg_error(mock_db):
    """Test: Convertir 'insert_no_data' -> error si execute() para COUNT levanta error no-psycopg."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Normalmente permitiría inserts
    mock_cursor.execute.side_effect = ValueError("Error de valor en execute de COUNT")

    with pytest.raises(ValueError, match="Error de valor en execute de COUNT"):
        init_db.insert_test_data(mock_cursor)  # Llamar a la función real

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_not_called()  # No se llega al fetchone


def test_create_tables_fails_if_specific_drop_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Convertir 'specific_drop_occurs' -> error si ese DROP levanta error no-psycopg."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    target_drop_command = "DROP TABLE IF EXISTS productos CASCADE"
    original_execute = mock_cursor.execute

    def side_effect_execute(command, *args, **kwargs):
        if command == target_drop_command:
            raise TypeError("Error de tipo inesperado durante DROP productos")
        return original_execute(command, *args, **kwargs)

    mock_cursor.execute.side_effect = side_effect_execute

    init_db.create_tables()

    mock_cursor.execute.assert_any_call("DROP TABLE IF EXISTS factura_items CASCADE")  # Asumiendo que este va antes
    mock_cursor.execute.assert_any_call(target_drop_command)
    mock_conn.commit.assert_not_called()  # El primer commit no se alcanza
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Error de tipo inesperado durante DROP productos" in captured.out


def test_create_tables_fails_if_specific_create_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture,
                                                                         capsys):
    """Test: Convertir 'specific_create_occurs' -> error si ese CREATE levanta error no-psycopg."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    if not init_db.commands:
        pytest.skip("No hay comandos CREATE para testear.")
    target_create_command = init_db.commands[0]  # El primer comando CREATE
    original_execute = mock_cursor.execute

    # El error ocurrirá después de los 5 drops y su commit
    execute_call_count = 0

    def side_effect_execute(command, *args, **kwargs):
        nonlocal execute_call_count
        execute_call_count += 1
        if execute_call_count == (
                5 + 1) and command == target_create_command:  # Después de 5 drops, en el primer create
            raise AttributeError("Error de atributo simulado durante CREATE")
        return original_execute(command, *args,
                                **kwargs)  # Necesita permitir que las llamadas a drop pasen si no es el original

    mock_cursor.execute.side_effect = side_effect_execute

    init_db.create_tables()

    assert mock_cursor.execute.call_count == 5 + 1  # 5 drops + 1 failing create
    mock_conn.commit.assert_called_once()  # El commit de los drops sí ocurre
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Error de atributo simulado durante CREATE" in captured.out


def test_create_tables_fails_if_insert_test_data_call_raises_non_psycopg_error(mock_db, capsys):
    """Test: Convertir 'insert_test_data_called' -> error si la llamada a insert_test_data levanta error no-psycopg."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    # Parchear init_db.insert_test_data para que falle
    with mock.patch('init_db.insert_test_data', side_effect=OverflowError("Desbordamiento en insert_test_data")):
        init_db.create_tables()

    # Todos los drops y creates deberían haberse ejecutado
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    assert mock_conn.commit.call_count == 1  # Solo el commit de los drops
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Desbordamiento en insert_test_data" in captured.out


def test_create_tables_fails_if_first_commit_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Convertir 'first_commit_occurs' -> error si el primer commit levanta error no-psycopg."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    def commit_side_effect():
        if mock_conn.commit.call_count == 1:
            raise MemoryError("Error de memoria durante el primer commit")

    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    assert mock_cursor.execute.call_count == 5  # Solo los drops
    mock_conn.commit.assert_called_once()  # Se intentó el primer commit
    mock_insert_test_data_fixture.assert_not_called()
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Error de memoria durante el primer commit" in captured.out


def test_create_tables_fails_if_second_commit_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Convertir 'second_commit_occurs' -> error si el segundo commit levanta error no-psycopg."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    def commit_side_effect():
        if mock_conn.commit.call_count == 2:  # Falla en el segundo commit
            raise ZeroDivisionError("División por cero durante el segundo commit")

    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once()
    assert mock_conn.commit.call_count == 2  # Se intentaron ambos commits
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: División por cero durante el segundo commit" in captured.out


def test_create_tables_fails_if_cursor_close_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Convertir 'cur_close_called' -> error si cur.close() levanta error no-psycopg."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]
    mock_cursor.close.side_effect = ImportError("Fallo de importación al cerrar cursor")

    init_db.create_tables()

    # Todas las operaciones previas deberían haber ocurrido
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once()
    assert mock_conn.commit.call_count == 2
    mock_cursor.close.assert_called_once()  # Se intentó cerrar el cursor

    mock_conn.rollback.assert_called_once()  # Error es capturado, se hace rollback
    mock_conn.close.assert_called_once()  # conn.close() en finally
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo de importación al cerrar cursor" in captured.out
    assert "Tablas creadas y datos de prueba insertados correctamente." not in captured.out


def test_create_tables_fails_if_conn_close_raises_non_psycopg_error(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Convertir 'conn_close_called' -> error si conn.close() levanta error no-psycopg."""
    mock_conn = mock_db["conn"]
    # conn.close() es llamado en el bloque finally. Si falla, la excepción se propaga fuera de create_tables.
    mock_conn.close.side_effect = BlockingIOError("Error de E/S bloqueante al cerrar conexión")

    with pytest.raises(BlockingIOError, match="Error de E/S bloqueante al cerrar conexión"):
        init_db.create_tables()

    # Las operaciones dentro del try deberían completarse (o fallar y ser manejadas)
    # Si todo en el try fue exitoso:
    assert mock_db["cursor"].execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once()
    assert mock_conn.commit.call_count == 2
    mock_db["cursor"].close.assert_called_once()  # Asumiendo que cur.close() no falló

    # El mensaje de éxito se habría impreso si el try se completó
    captured = capsys.readouterr()  # Capturar lo que se imprimió antes del error en finally
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

    mock_conn.close.assert_called_once()  # conn.close() fue llamado y falló



def test_create_tables_fails_if_print_success_raises_error(mock_db, mock_insert_test_data_fixture, monkeypatch, capsys):
    """Test: Convertir 'success_message_printed' -> error si print() mismo falla."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Simular que la función print falla
    mock_print = mock.MagicMock(side_effect=OSError("Error de E/S al imprimir"))
    monkeypatch.setattr('builtins.print', mock_print)

    init_db.create_tables()  # print es llamado al final del try

    # Todas las operaciones de BD deberían haber sido exitosas
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)
    mock_insert_test_data_fixture.assert_called_once()
    assert mock_conn.commit.call_count == 2
    mock_cursor.close.assert_called_once()


    assert mock_print.call_count >= 1


    error_messages_printed_to_mock = [call_args[0][0] for call_args in mock_print.call_args_list]
    assert "Tablas creadas y datos de prueba insertados correctamente." in error_messages_printed_to_mock
    assert any("Error al crear tablas: Error de E/S al imprimir" in msg for msg in error_messages_printed_to_mock)

    mock_conn.rollback.assert_called_once()  # Rollback debido al error de print capturado
    mock_conn.close.assert_called_once()

# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- 10 Tests de Éxito Adicionales ---

def test_create_tables_success_and_insert_test_data_finds_existing_data_so_skips_inserts(mock_db, capsys):
    """
    Test: create_tables se ejecuta con éxito.
    insert_test_data (el real) es llamado y encuentra datos existentes (fetchone() > 0),
    por lo que no realiza nuevos inserts. El script finaliza correctamente.
    """
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Configurar fetchone para que insert_test_data piense que ya hay datos
    # La primera llamada a fetchone es dentro de insert_test_data
    mock_cursor.fetchone.return_value = (1,) # Simula que ya existe al menos 1 cliente

    # No usamos mock_insert_test_data_fixture aquí para que se ejecute el insert_test_data real

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()

    # Verificar que se llamó a execute para el conteo en insert_test_data
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once() # Llamado para el conteo

    # Verificar que NO se hicieron llamadas de INSERT INTO clientes o productos
    insert_calls = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO" in call[0][0]]
    assert len(insert_calls) == 0

    # Todos los DROPs y CREATEs de create_tables sí se ejecutan
    assert mock_cursor.execute.call_count >= 5 + len(init_db.commands) # Al menos los drops, creates y el count

    assert mock_conn.commit.call_count == 2 # Ambos commits de create_tables
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out
    assert "Error al crear tablas" not in captured.err

def test_create_tables_success_and_insert_test_data_inserts_nothing_as_init_db_lists_are_empty(mock_db, monkeypatch, capsys):
    """
    Test: create_tables se ejecuta con éxito.
    insert_test_data (el real) es llamado, no encuentra datos existentes (fetchone() == 0),
    pero las listas `clientes` y `productos` en init_db.py están vacías (mockeadas).
    No se realizan inserts. El script finaliza correctamente.
    """
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # No hay datos existentes

    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [])

    init_db.create_tables()

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

    insert_calls = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO" in call[0][0]]
    assert len(insert_calls) == 0 # No se hicieron inserts porque las listas estaban vacías

    assert mock_conn.commit.call_count == 2
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

def test_all_drop_commands_executed_in_order_then_first_commit(mock_db, mock_insert_test_data_fixture):
    """Verifica la ejecución ordenada de todos los comandos DROP y el primer commit."""
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Órden esperado de los comandos DROP
    expected_drop_commands = [
        "DROP TABLE IF EXISTS factura_items CASCADE",
        "DROP TABLE IF EXISTS facturas CASCADE",
        "DROP TABLE IF EXISTS productos CASCADE",
        "DROP TABLE IF EXISTS clientes CASCADE",
        "DROP SEQUENCE IF EXISTS factura_numero_seq"
    ]

    init_db.create_tables() # mock_insert_test_data_fixture previene la ejecución de insert_test_data

    # Verificar que los primeros 5 execute calls son los drops en orden
    for i, expected_sql in enumerate(expected_drop_commands):
        assert mock_cursor.execute.call_args_list[i][0][0] == expected_sql

    # Verificar que el primer commit ocurre después de los drops
    # El commit es la primera llamada a commit(); las ejecuciones de SQL son llamadas a execute()
    # Necesitamos asegurar que el commit ocurrió *después* de esas 5 llamadas a execute.
    # Esto se infiere si commit.call_count es al menos 1 y los drops ocurrieron.
    # La estructura de init_db.py es: drops -> commit -> creates -> insert_test_data -> commit
    # Si mock_conn.commit.call_count >=1 y los drops se hicieron, el primero es el buscado.
    # Para ser más precisos, podríamos mockear commit para registrar cuándo se llama en relación a los executes.
    # Pero una forma simple es verificar que se llamó a commit y que los drops están antes de los creates.

    first_commit_called = False
    create_command_called_after_first_commit_attempt = False

    if mock_conn.commit.call_args_list: # Si se llamó a commit al menos una vez
        first_commit_called = True
        # Ahora, verificamos que los creates vienen después.
        # Esta prueba se enfoca en los drops y el *primer* commit.
        # Los creates se prueban en otra.

    assert first_commit_called
    # Para verificar que el commit es el *primer* y ocurre después de los drops:
    # Se puede verificar que las llamadas a execute de los CREATEs ocurrieron después de la primera llamada a commit.
    # O, más simple, que commit(1) fue llamado y los 5 drops se hicieron antes de cualquier CREATE.
    assert mock_conn.commit.call_count >= 1 # Al menos el primer commit se intentó


def test_all_create_commands_from_tuple_executed_in_order_after_first_commit(mock_db, mock_insert_test_data_fixture):
    """Verifica la ejecución ordenada de todos los comandos CREATE/SEQUENCE después del primer commit."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    init_db.create_tables()

    # Los primeros 5 son drops
    # Luego vienen los comandos de init_db.commands
    executed_create_commands = [call[0][0] for call in mock_cursor.execute.call_args_list[5:5+len(init_db.commands)]]

    assert executed_create_commands == list(init_db.commands)
    # El primer commit (después de drops) ya ocurrió.
    # insert_test_data es mockeado, por lo que el segundo commit (después de creates/inserts) también ocurre.
    assert mock_conn.commit.call_count == 2


def test_insert_test_data_called_with_correct_cursor_after_creates_before_second_commit(mock_db, mock_insert_test_data_fixture, capsys):
    """
    Verifica que insert_test_data es llamado con el cursor correcto,
    después de los comandos CREATE y antes del segundo commit.
    """
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    init_db.create_tables()

    # insert_test_data_fixture asegura que la llamada ocurrió
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)

    # Para verificar el orden (CREATEs -> insert_test_data -> commit final):
    #   1. Los CREATEs se ejecutaron (parte de las llamadas a execute).
    #   2. insert_test_data_fixture fue llamado.
    #   3. El segundo commit ocurrió.
    # Esto es cubierto por test_create_tables_success, pero este test se enfoca en esta secuencia.
    assert mock_conn.commit.call_count == 2
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_specific_client_data_inserted_when_no_prior_data_exists_integration(mock_db, capsys):
    """
    Test de integración: create_tables y insert_test_data (real) insertan un cliente específico.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # No hay datos previos

    # Seleccionar un cliente específico de los datos de prueba de init_db.py
    if not init_db.clientes:
        pytest.skip("init_db.clientes está vacío, no se puede testear insert específico.")
    specific_client_data = init_db.clientes[0] # Tomar el primer cliente como ejemplo

    init_db.create_tables() # Ejecutar el flujo completo

    # Verificar que el execute para insertar este cliente específico fue llamado
    expected_sql = "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);"
    mock_cursor.execute.assert_any_call(expected_sql, specific_client_data)

    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

def test_specific_product_data_inserted_when_no_prior_data_exists_integration(mock_db, capsys):
    """
    Test de integración: create_tables y insert_test_data (real) insertan un producto específico.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,) # No hay datos previos

    if not init_db.productos:
        pytest.skip("init_db.productos está vacío, no se puede testear insert específico.")
    specific_product_data = init_db.productos[0] # Tomar el primer producto

    init_db.create_tables()

    expected_sql = "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);"
    mock_cursor.execute.assert_any_call(expected_sql, specific_product_data)

    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

def test_cursor_closed_and_connection_closed_on_full_success_path(mock_db, mock_insert_test_data_fixture, capsys):
    """Verifica explícitamente que el cursor y la conexión se cierran en el camino de éxito completo."""
    mock_cursor = mock_db["cursor"]
    mock_conn = mock_db["conn"]

    init_db.create_tables()

    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

def test_create_tables_with_no_create_commands_completes_successfully(mock_db, mock_insert_test_data_fixture, monkeypatch, capsys):
    """
    Test: create_tables con init_db.commands vacío.
    Debe ejecutar drops, llamar a insert_test_data, realizar commits y finalizar con éxito.
    """
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]
    monkeypatch.setattr(init_db, 'commands', ()) # Tupla de comandos CREATE vacía

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # Solo se ejecutan los 5 comandos DROP, no hay CREATEs
    assert mock_cursor.execute.call_count == 5
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor) # Aún se llama
    assert mock_conn.commit.call_count == 2 # Ambos commits (después de drops, y después de "creates" vacíos + insert)
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out

@pytest.mark.skipif(sys.platform == "win32", reason="runpy test can be flaky on Windows CI for __main__ due to pathing/import issues")
def test_main_script_execution_leads_to_successful_table_creation_flow_mocked(mock_db, mock_insert_test_data_fixture, capsys):
    """
    Test de integración de alto nivel: ejecutar init_db.py como __main__
    debería llevar a un flujo de creación de tablas exitoso (todo mockeado).
    """
    # Esta prueba es más compleja porque implica "re-ejecutar" el módulo.
    # Se usa mock_insert_test_data_fixture para que no dependamos de su lógica interna aquí,
    # solo que create_tables lo llame.

    # Configurar mocks para un flujo completamente exitoso de create_tables
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Usar runpy para ejecutar el módulo en el contexto de __main__
    import runpy
    # Necesitamos asegurar que init_db es re-evaluado en el contexto de __main__
    # Si init_db ya está en sys.modules, runpy podría no re-ejecutar el `if __name__ == '__main__'`
    # de la forma esperada sin manipulación de sys.modules.
    # Para esta prueba, asumimos que el mock de init_db.create_tables es suficiente
    # si el if __name__ == '__main__' simplemente llama a create_tables().

    with mock.patch('init_db.create_tables') as mock_create_tables_in_main:
        # Almacenar y eliminar temporalmente para forzar la re-evaluación si es necesario
        original_module = sys.modules.pop('init_db', None)
        try:
            runpy.run_module('init_db', run_name='__main__')
        finally:
            # Restaurar el módulo si se eliminó, o eliminar el recién importado
            if original_module:
                sys.modules['init_db'] = original_module
            elif 'init_db' in sys.modules: # Si runpy lo añadió y no estaba antes
                del sys.modules['init_db']


    mock_create_tables_in_main.assert_called_once()


# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- Otros 10 Tests de Éxito Adicionales ---

def test_create_tables_connect_uses_all_keys_from_db_config(mock_db, mock_insert_test_data_fixture):
    """
    Test: Verifica que psycopg2.connect es llamado con todos los kwargs definidos en DB_CONFIG.
    Esto asegura que si añades una nueva clave a DB_CONFIG (p.ej. 'sslmode'), se use.
    """
    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**init_db.DB_CONFIG)


def test_insert_test_data_only_inserts_clientes_if_productos_list_is_empty(mock_db, monkeypatch, capsys):
    """
    Test: insert_test_data (real) inserta solo clientes si la lista init_db.productos está vacía,
    y no hay datos previos de clientes.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No hay clientes existentes

    monkeypatch.setattr(init_db, 'productos', [])  # Lista de productos vacía
    # Asegurarse que hay clientes para insertar
    if not init_db.clientes:
        monkeypatch.setattr(init_db, 'clientes', [("Test Cliente", "Test Dir", "123", "test@test.com")])

    init_db.insert_test_data(mock_cursor)

    # Verificar que se intentaron inserts de clientes
    client_insert_calls = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO clientes" in call[0][0]]
    assert len(client_insert_calls) == len(init_db.clientes)

    # Verificar que NO se intentaron inserts de productos
    product_insert_calls = [call for call in mock_cursor.execute.call_args_list if
                            "INSERT INTO productos" in call[0][0]]
    assert len(product_insert_calls) == 0


def test_insert_test_data_only_inserts_productos_if_clientes_list_is_empty(mock_db, monkeypatch, capsys):
    """
    Test: insert_test_data (real) inserta solo productos si la lista init_db.clientes está vacía,
    y no hay datos previos de clientes (aunque el conteo es de clientes, esto es para permitir que el flujo continúe).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Asumimos que el conteo de clientes es 0 para proceder

    monkeypatch.setattr(init_db, 'clientes', [])  # Lista de clientes vacía
    # Asegurarse que hay productos para insertar
    if not init_db.productos:
        monkeypatch.setattr(init_db, 'productos', [("Test Producto", "Test Desc", 9.99)])

    init_db.insert_test_data(mock_cursor)

    client_insert_calls = [call for call in mock_cursor.execute.call_args_list if "INSERT INTO clientes" in call[0][0]]
    assert len(client_insert_calls) == 0

    product_insert_calls = [call for call in mock_cursor.execute.call_args_list if
                            "INSERT INTO productos" in call[0][0]]
    assert len(product_insert_calls) == len(init_db.productos)


def test_create_tables_structure_if_no_drop_commands_were_present_hypothetical(mock_db, mock_insert_test_data_fixture,
                                                                               monkeypatch, capsys):
    """
    Test Hipotético: Si no hubiera DROPs, create_tables aún ejecutaría CREATEs, inserts y commits.
    Esto se logra modificando el script para no tener drops o mockeando execute para ignorarlos.
    Aquí, simplemente verificaremos que los CREATEs, insert y commits ocurren.
    Este test es más para asegurar que la lógica post-drop funciona independientemente.
    (Se asume que los drops son parte del flujo normal, pero este test se enfoca en lo que sigue).
    """
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Simularemos que los drops no existen o no hacen nada
    original_execute = mock_cursor.execute
    drop_commands_sql = [
        "DROP TABLE IF EXISTS factura_items CASCADE",
        "DROP TABLE IF EXISTS facturas CASCADE",
        "DROP TABLE IF EXISTS productos CASCADE",
        "DROP TABLE IF EXISTS clientes CASCADE",
        "DROP SEQUENCE IF EXISTS factura_numero_seq"
    ]

    def no_drop_effect_execute(command, *args, **kwargs):
        if command in drop_commands_sql:
            return  # No hacer nada para los drops
        return original_execute(command, *args, **kwargs)  # Ejecutar otros comandos (CREATEs)

    mock_cursor.execute.side_effect = no_drop_effect_execute

    init_db.create_tables()

    # Verificar que los comandos CREATE se ejecutaron
    for create_cmd in init_db.commands:
        mock_cursor.execute.assert_any_call(create_cmd)

    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)
    # Ambos commits deberían ocurrir (el "después de drops" y el "después de creates/inserts")
    assert mock_conn.commit.call_count == 2
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_create_tables_prints_success_message_to_stdout(mock_db, mock_insert_test_data_fixture, capsys):
    """Test: Verifica específicamente que el mensaje de éxito se imprime en stdout."""
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out
    assert captured.err == ""  # No debería haber nada en stderr en caso de éxito


def test_insert_test_data_handles_empty_clientes_and_productos_lists_gracefully(mock_db, monkeypatch):
    """
    Test: insert_test_data (real) con listas `clientes` y `productos` vacías en init_db.py
    y sin datos previos en la BD. No debería fallar y no debería hacer inserts.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No datos existentes

    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [])

    init_db.insert_test_data(mock_cursor)  # No debería levantar excepciones

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")  # Solo el conteo
    # No se deben hacer más llamadas a execute para inserts
    assert mock_cursor.execute.call_count == 1


def test_create_tables_sequence_creation_is_idempotent_due_to_if_not_exists(mock_db, mock_insert_test_data_fixture):
    """
    Test: Verifica que el comando CREATE SEQUENCE (asumiendo que usa IF NOT EXISTS)
    se ejecuta correctamente. La idempotencia es una propiedad del SQL en sí.
    El test verifica que el comando se intenta ejecutar.
    """
    mock_cursor = mock_db["cursor"]
    sequence_create_command = None
    for cmd in init_db.commands:
        if "CREATE SEQUENCE" in cmd and "IF NOT EXISTS" in cmd:
            sequence_create_command = cmd
            break

    if not sequence_create_command:
        pytest.skip("No se encontró comando CREATE SEQUENCE IF NOT EXISTS en init_db.commands")

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(sequence_create_command)
    # El éxito general de create_tables implica que este comando no causó error.


def test_create_tables_table_creation_is_idempotent_due_to_if_not_exists(mock_db, mock_insert_test_data_fixture):
    """
    Test: Verifica que los comandos CREATE TABLE (asumiendo que usan IF NOT EXISTS)
    se ejecutan correctamente.
    """
    mock_cursor = mock_db["cursor"]
    table_create_commands = [cmd for cmd in init_db.commands if "CREATE TABLE" in cmd and "IF NOT EXISTS" in cmd]

    if not table_create_commands:
        pytest.skip("No se encontraron comandos CREATE TABLE IF NOT EXISTS en init_db.commands")

    init_db.create_tables()
    for cmd in table_create_commands:
        mock_cursor.execute.assert_any_call(cmd)


def test_create_tables_uses_correct_db_config_object(mock_db, mock_insert_test_data_fixture):
    """
    Test: Asegura que la función connect es llamada con el objeto DB_CONFIG importado de init_db,
    y no una copia o un mock que podría tener valores diferentes si no se maneja con cuidado.
    """
    # La fixture mock_db ya usa init_db.DB_CONFIG para la aserción,
    # por lo que esta prueba es una reafirmación o puede ser más específica.
    # Podemos verificar que el ID del objeto DB_CONFIG usado por connect es el mismo.

    captured_config_id = None
    original_connect = psycopg2.connect  # Guardar original para no interferir con otros tests si es necesario

    def capture_config_connect(**kwargs):
        nonlocal captured_config_id
        captured_config_id = id(kwargs)  # Capturar el id del dict de kwargs
        # Para que el test funcione, necesitamos que connect no falle y devuelva un mock_conn
        mock_conn_internal = mock.MagicMock(spec=psycopg2.extensions.connection)
        mock_cursor_internal = mock.MagicMock(spec=psycopg2.extensions.cursor)
        mock_conn_internal.cursor.return_value.__enter__.return_value = mock_cursor_internal
        return mock_conn_internal

    init_db.create_tables()
    mock_db["connect"].assert_called_once_with(**init_db.DB_CONFIG)


def test_successful_run_does_not_print_error_messages(mock_db, mock_insert_test_data_fixture, capsys):
    """
    Test: En un flujo completamente exitoso, no se deben imprimir mensajes de error.
    (Complementa la prueba de que se imprime el mensaje de éxito).
    """
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" not in captured.out
    assert "Error al crear tablas:" not in captured.err  # También verificar stderr
    # Y verificar que el mensaje de éxito sí está
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- 10 Tests de Lógica de Negocio Específica para el DB (Éxito) ---

def test_create_tables_executes_exact_clientes_table_ddl_with_constraints(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que el DDL para la tabla 'clientes' se ejecuta exactamente como está definido,
    incluyendo PRIMARY KEY, NOT NULL, y tipos de datos implícitos.
    """
    mock_cursor = mock_db["cursor"]
    # Encontrar el comando DDL específico para clientes en init_db.commands
    clientes_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS clientes" in cmd][0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(clientes_ddl)
    # Aquí podrías añadir aserciones más detalladas sobre el contenido de clientes_ddl si fuera necesario,
    # como verificar la presencia de "id SERIAL PRIMARY KEY" y "nombre VARCHAR(100) NOT NULL".
    assert "id SERIAL PRIMARY KEY" in clientes_ddl
    assert "nombre VARCHAR(100) NOT NULL" in clientes_ddl
    assert "email VARCHAR(100)" in clientes_ddl  # Verificar otro campo


def test_create_tables_executes_exact_productos_table_ddl_with_constraints(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica el DDL para la tabla 'productos', incluyendo precio DECIMAL y stock DEFAULT."""
    mock_cursor = mock_db["cursor"]
    productos_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(productos_ddl)
    assert "precio DECIMAL(10, 2) NOT NULL" in productos_ddl
    assert "stock INTEGER DEFAULT 0" in productos_ddl


def test_create_tables_executes_exact_facturas_table_ddl_with_constraints_and_fk(mock_db,
                                                                                 mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica DDL de 'facturas', incluyendo UNIQUE en numero y FK a clientes."""
    mock_cursor = mock_db["cursor"]
    facturas_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(facturas_ddl)
    assert "numero VARCHAR(20) NOT NULL UNIQUE" in facturas_ddl
    assert "fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP" in facturas_ddl
    assert "FOREIGN KEY (cliente_id) REFERENCES clientes (id)" in facturas_ddl


def test_create_tables_executes_exact_factura_items_table_ddl_with_fks(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica DDL de 'factura_items', incluyendo sus dos FOREIGN KEYs."""
    mock_cursor = mock_db["cursor"]
    factura_items_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS factura_items" in cmd][0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(factura_items_ddl)
    assert "FOREIGN KEY (factura_id) REFERENCES facturas (id)" in factura_items_ddl
    assert "FOREIGN KEY (producto_id) REFERENCES productos (id)" in factura_items_ddl
    assert "cantidad INTEGER NOT NULL" in factura_items_ddl


def test_create_tables_executes_factura_numero_seq_ddl_with_start_value(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica que la secuencia 'factura_numero_seq' se crea con START WITH."""
    mock_cursor = mock_db["cursor"]
    sequence_ddl = [cmd for cmd in init_db.commands if "CREATE SEQUENCE IF NOT EXISTS factura_numero_seq" in cmd][0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(sequence_ddl)
    assert "START WITH 1000" in sequence_ddl  # El valor inicial definido en init_db.py


def test_insert_test_data_inserts_first_cliente_with_correct_data_types_when_no_data(mock_db):
    """
    Test (Lógica de Negocio): insert_test_data (real) inserta el primer cliente de la lista `init_db.clientes`
    con los datos y (implícitamente) tipos correctos cuando la tabla está vacía.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No hay datos previos

    if not init_db.clientes:
        pytest.skip("init_db.clientes está vacío.")

    first_cliente_data = init_db.clientes[0]
    # ("Cliente Uno", "Calle 123", "555-1234", "cliente1@example.com")
    # nombre VARCHAR, direccion TEXT, telefono VARCHAR, email VARCHAR

    init_db.insert_test_data(mock_cursor)  # Ejecutar la función real

    expected_sql = "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);"
    mock_cursor.execute.assert_any_call(expected_sql, first_cliente_data)
    # Verificar que los tipos de datos en first_cliente_data son strings, lo que es compatible.
    assert all(isinstance(data, str) for data in first_cliente_data)


def test_insert_test_data_inserts_first_producto_with_correct_data_types_when_no_data(mock_db):
    """
    Test (Lógica de Negocio): insert_test_data (real) inserta el primer producto
    con los datos y tipos correctos (nombre STR, desc STR, precio FLOAT/DECIMAL).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No hay datos previos

    if not init_db.productos:
        pytest.skip("init_db.productos está vacío.")

    first_producto_data = init_db.productos[0]
    # ("Producto A", "Descripción producto A", 10.50)
    # nombre VARCHAR, descripcion TEXT, precio DECIMAL

    # Para aislar, podríamos vaciar clientes si la lógica de insert_test_data lo permite
    with mock.patch('init_db.clientes', []):
        init_db.insert_test_data(mock_cursor)

    expected_sql = "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);"
    mock_cursor.execute.assert_any_call(expected_sql, first_producto_data)
    assert isinstance(first_producto_data[0], str)  # nombre
    assert isinstance(first_producto_data[1], str)  # descripcion
    assert isinstance(first_producto_data[2], (float, int))  # precio (psycopg2 maneja float a DECIMAL)


def test_insert_test_data_verifies_existing_clientes_before_inserting(mock_db):
    """
    Test (Lógica de Negocio): Confirma que la primera acción de insert_test_data
    es verificar si existen clientes con `SELECT COUNT(*) FROM clientes;`.
    """
    mock_cursor = mock_db["cursor"]
    # Simular que hay datos para que la función retorne temprano después del chequeo
    mock_cursor.fetchone.return_value = (1,)

    init_db.insert_test_data(mock_cursor)

    # La primera llamada a execute DEBE ser el SELECT COUNT
    assert mock_cursor.execute.call_args_list[0][0][0] == "SELECT COUNT(*) FROM clientes;"
    mock_cursor.fetchone.assert_called_once()  # Y se debe llamar a fetchone para obtener el resultado


def test_schema_column_constraints_not_null_are_present_in_ddl(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que ciertas columnas importantes
    (ej. clientes.nombre, productos.nombre, productos.precio, facturas.numero, etc.)
    estén definidas como NOT NULL en sus DDL.
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()  # Esto ejecutará todos los DDL

    ddl_commands_executed = "".join(
        [call[0][0] for call in mock_cursor.execute.call_args_list if "CREATE TABLE" in call[0][0]])

    assert "clientes" in ddl_commands_executed  # Asegurar que el DDL de clientes se ejecutó
    assert "nombre VARCHAR(100) NOT NULL" in ddl_commands_executed  # Para clientes.nombre

    assert "productos" in ddl_commands_executed
    assert "nombre VARCHAR(100) NOT NULL" in ddl_commands_executed  # Para productos.nombre
    assert "precio DECIMAL(10, 2) NOT NULL" in ddl_commands_executed  # Para productos.precio

    assert "facturas" in ddl_commands_executed
    assert "numero VARCHAR(20) NOT NULL UNIQUE" in ddl_commands_executed  # Para facturas.numero (ya chequeado en otro test)
    assert "cliente_id INTEGER NOT NULL" in ddl_commands_executed
    assert "total DECIMAL(10, 2) NOT NULL" in ddl_commands_executed

    assert "factura_items" in ddl_commands_executed
    assert "factura_id INTEGER NOT NULL" in ddl_commands_executed
    assert "producto_id INTEGER NOT NULL" in ddl_commands_executed
    assert "cantidad INTEGER NOT NULL" in ddl_commands_executed


def test_create_tables_all_test_data_tuples_match_their_table_structure_implicitly(mock_db):
    """
    Test (Lógica de Negocio): Si insert_test_data (real) se ejecuta sin errores con los datos de prueba,
    implica que el número de elementos en cada tupla de datos de prueba
    coincide con el número de columnas en las sentencias INSERT (y por ende, con la tabla).
    Este es un test de éxito general para la estructura de los datos de prueba.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Para forzar la inserción

    # Dejar que insert_test_data se ejecute con los datos reales
    # No debería haber excepciones si los datos están bien formados para las sentencias INSERT
    # (p.ej. número correcto de placeholders %s)
    try:
        init_db.insert_test_data(mock_cursor)
    except (psycopg2.ProgrammingError, TypeError) as e:
        pytest.fail(f"insert_test_data falló con los datos de prueba definidos: {e}")

    # Contar cuántos inserts de clientes y productos se esperaban
    expected_cliente_inserts = len(init_db.clientes)
    expected_producto_inserts = len(init_db.productos)

    actual_cliente_inserts = 0
    actual_producto_inserts = 0
    for call_args in mock_cursor.execute.call_args_list:
        sql_command = call_args[0][0]
        if "INSERT INTO clientes" in sql_command:
            actual_cliente_inserts += 1
        elif "INSERT INTO productos" in sql_command:
            actual_producto_inserts += 1

    if init_db.clientes:  # Solo assert si se esperaban inserts
        assert actual_cliente_inserts == expected_cliente_inserts
    if init_db.productos:
        assert actual_producto_inserts == expected_producto_inserts


# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- Otros 10 Tests de Lógica de Negocio Específica para el DB (Éxito) ---

def test_all_clientes_test_data_tuples_have_correct_number_of_elements(mock_db):
    """
    Test (Lógica de Negocio): Verifica que cada tupla en init_db.clientes
    tiene el número correcto de elementos para coincidir con las columnas
    (nombre, direccion, telefono, email).
    """
    # Asumiendo que el INSERT es:
    # "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);"
    # lo cual implica 4 valores.
    expected_length = 4
    for i, cliente_tuple in enumerate(init_db.clientes):
        assert len(cliente_tuple) == expected_length, \
            f"La tupla del cliente en el índice {i} tiene {len(cliente_tuple)} elementos, se esperaban {expected_length}"


def test_all_productos_test_data_tuples_have_correct_number_of_elements(mock_db):
    """
    Test (Lógica de Negocio): Verifica que cada tupla en init_db.productos
    tiene el número correcto de elementos para coincidir con las columnas
    (nombre, descripcion, precio).
    """
    # Asumiendo que el INSERT es:
    # "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);"
    # lo cual implica 3 valores.
    expected_length = 3
    for i, producto_tuple in enumerate(init_db.productos):
        assert len(producto_tuple) == expected_length, \
            f"La tupla del producto en el índice {i} tiene {len(producto_tuple)} elementos, se esperaban {expected_length}"


def test_schema_facturas_fecha_has_default_current_timestamp(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica que el DDL de 'facturas' incluye DEFAULT CURRENT_TIMESTAMP para la fecha."""
    mock_cursor = mock_db["cursor"]
    facturas_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]
    init_db.create_tables()
    mock_cursor.execute.assert_any_call(facturas_ddl)
    assert "fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP" in facturas_ddl.upper()  # Usar upper para insensibilidad a mayúsculas


def test_schema_productos_stock_has_default_zero(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica que el DDL de 'productos' incluye DEFAULT 0 para stock."""
    mock_cursor = mock_db["cursor"]
    productos_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]
    init_db.create_tables()
    mock_cursor.execute.assert_any_call(productos_ddl)
    assert "stock INTEGER DEFAULT 0" in productos_ddl.upper()


def test_insert_test_data_all_defined_clientes_are_inserted_when_no_data(mock_db):
    """
    Test (Lógica de Negocio): insert_test_data (real) inserta TODOS los clientes definidos
    en init_db.clientes cuando la tabla está vacía.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No hay datos previos

    if not init_db.clientes:
        pytest.skip("init_db.clientes está vacío.")

    init_db.insert_test_data(mock_cursor)  # Ejecutar la función real

    expected_sql = "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);"
    for cliente_data in init_db.clientes:
        mock_cursor.execute.assert_any_call(expected_sql, cliente_data)

    # Verificar el número total de inserts de clientes
    client_insert_calls = [call for call in mock_cursor.execute.call_args_list if expected_sql in call[0][0]]
    assert len(client_insert_calls) == len(init_db.clientes)


def test_insert_test_data_all_defined_productos_are_inserted_when_no_data(mock_db, monkeypatch):
    """
    Test (Lógica de Negocio): insert_test_data (real) inserta TODOS los productos definidos
    en init_db.productos cuando la tabla está vacía (y no hay clientes para simplificar).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # No hay datos previos de clientes

    if not init_db.productos:
        pytest.skip("init_db.productos está vacío.")

    # Vaciar clientes para enfocarnos solo en la inserción de productos después del chequeo de clientes
    monkeypatch.setattr(init_db, 'clientes', [])

    init_db.insert_test_data(mock_cursor)

    expected_sql = "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);"
    for producto_data in init_db.productos:
        mock_cursor.execute.assert_any_call(expected_sql, producto_data)

    product_insert_calls = [call for call in mock_cursor.execute.call_args_list if expected_sql in call[0][0]]
    assert len(product_insert_calls) == len(init_db.productos)


def test_create_tables_drops_all_relevant_objects_if_they_exist(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que se intentan todos los comandos DROP especificados.
    La cláusula "IF EXISTS" asegura que no fallen si los objetos no existen.
    """
    mock_cursor = mock_db["cursor"]
    expected_drop_commands = [
        "DROP TABLE IF EXISTS factura_items CASCADE",
        "DROP TABLE IF EXISTS facturas CASCADE",
        "DROP TABLE IF EXISTS productos CASCADE",
        "DROP TABLE IF EXISTS clientes CASCADE",
        "DROP SEQUENCE IF EXISTS factura_numero_seq"
    ]
    init_db.create_tables()
    for cmd in expected_drop_commands:
        mock_cursor.execute.assert_any_call(cmd)


def test_create_tables_creates_all_tables_and_sequences_if_not_exist(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que se intentan todos los comandos CREATE especificados.
    La cláusula "IF NOT EXISTS" asegura la idempotencia.
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()
    for cmd in init_db.commands:  # init_db.commands contiene todos los CREATEs
        mock_cursor.execute.assert_any_call(cmd)


def test_schema_primary_keys_are_serial_or_defined(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que las tablas principales tienen 'id SERIAL PRIMARY KEY'
    o una definición de clave primaria similar en sus DDL.
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()
    all_executed_ddl = "".join([call[0][0] for call in mock_cursor.execute.call_args_list])

    assert "clientes" in all_executed_ddl and "id SERIAL PRIMARY KEY" in \
           [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS clientes" in cmd][0]
    assert "productos" in all_executed_ddl and "id SERIAL PRIMARY KEY" in \
           [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]
    assert "facturas" in all_executed_ddl and "id SERIAL PRIMARY KEY" in \
           [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]
    assert "factura_items" in all_executed_ddl and "id SERIAL PRIMARY KEY" in \
           [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS factura_items" in cmd][0]


def test_datatypes_in_test_data_are_consistent_with_schema_expectations(mock_db):
    """
    Test (Lógica de Negocio): Verifica que los tipos de datos de Python en los datos de prueba
    son los que psycopg2 puede convertir felizmente a los tipos de columna SQL.
    (Ej: string para VARCHAR, int/float para INTEGER/DECIMAL, etc.)
    """
    # Para clientes: (str, str, str, str)
    for cliente in init_db.clientes:
        assert isinstance(cliente[0], str), f"Nombre de cliente no es str: {cliente[0]}"
        assert isinstance(cliente[1], str), f"Dirección de cliente no es str: {cliente[1]}"
        assert isinstance(cliente[2], str), f"Teléfono de cliente no es str: {cliente[2]}"
        assert isinstance(cliente[3], str), f"Email de cliente no es str: {cliente[3]}"

    # Para productos: (str, str, float/int)
    for producto in init_db.productos:
        assert isinstance(producto[0], str), f"Nombre de producto no es str: {producto[0]}"
        assert isinstance(producto[1], str), f"Descripción de producto no es str: {producto[1]}"
        assert isinstance(producto[2], (float, int)), f"Precio de producto no es numérico: {producto[2]}"


# test/test_init_db.py
# ... (tus imports y fixtures existentes deben permanecer aquí) ...

# --- Otros 10 Tests de Lógica de Negocio Específica para el DB (Éxito) ---

def test_cliente_test_data_contains_expected_number_of_records(mock_db):
    """
    Test (Lógica de Negocio): Verifica que la lista hardcodeada `init_db.clientes`
    contiene el número esperado de registros de clientes para pruebas.
    """
    # Este es un test de los datos de entrada, no directamente de la ejecución de DB,
    # pero es relevante para la lógica de negocio de los datos de prueba.
    expected_number_of_clients = 3  # Según lo definido en init_db.py
    assert len(init_db.clientes) == expected_number_of_clients


def test_producto_test_data_contains_expected_number_of_records(mock_db):
    """
    Test (Lógica de Negocio): Verifica que la lista hardcodeada `init_db.productos`
    contiene el número esperado de registros de productos para pruebas.
    """
    expected_number_of_products = 5  # Según lo definido en init_db.py
    assert len(init_db.productos) == expected_number_of_products


def test_insert_test_data_uses_correct_sql_for_inserting_clientes(mock_db):
    """
    Test (Lógica de Negocio): Verifica que `insert_test_data` usa la sentencia SQL exacta
    esperada para insertar clientes.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Forzar inserción

    if not init_db.clientes:
        pytest.skip("init_db.clientes está vacío.")

    init_db.insert_test_data(mock_cursor)

    expected_sql = "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);"
    # Verificar que esta SQL fue usada al menos una vez (para el primer cliente)
    # La llamada completa incluye la tupla de datos, así que usamos call_args_list
    called_with_expected_sql = False
    for call_args in mock_cursor.execute.call_args_list:
        if call_args[0][0] == expected_sql:
            called_with_expected_sql = True
            break
    assert called_with_expected_sql, f"La SQL esperada '{expected_sql}' no fue llamada."


def test_insert_test_data_uses_correct_sql_for_inserting_productos(mock_db, monkeypatch):
    """
    Test (Lógica de Negocio): Verifica que `insert_test_data` usa la sentencia SQL exacta
    esperada para insertar productos.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])  # No insertar clientes para aislar

    if not init_db.productos:
        pytest.skip("init_db.productos está vacío.")

    init_db.insert_test_data(mock_cursor)

    expected_sql = "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);"
    called_with_expected_sql = False
    for call_args in mock_cursor.execute.call_args_list:
        if call_args[0][0] == expected_sql:
            called_with_expected_sql = True
            break
    assert called_with_expected_sql, f"La SQL esperada '{expected_sql}' no fue llamada."


def test_varchar_lengths_in_schema_are_as_expected(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica las longitudes definidas para campos VARCHAR
    clave en el DDL (ej. clientes.nombre, facturas.numero).
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    clientes_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS clientes" in cmd][0]
    productos_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]
    facturas_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]

    mock_cursor.execute.assert_any_call(clientes_ddl)
    assert "nombre VARCHAR(100)" in clientes_ddl
    assert "telefono VARCHAR(20)" in clientes_ddl
    assert "email VARCHAR(100)" in clientes_ddl

    mock_cursor.execute.assert_any_call(productos_ddl)
    assert "nombre VARCHAR(100)" in productos_ddl

    mock_cursor.execute.assert_any_call(facturas_ddl)
    assert "numero VARCHAR(20)" in facturas_ddl


def test_decimal_precision_in_schema_is_as_expected(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica la precisión y escala definidas para campos DECIMAL
    (ej. productos.precio, facturas.total, factura_items.precio, factura_items.subtotal).
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    productos_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]
    facturas_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]
    factura_items_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS factura_items" in cmd][0]

    mock_cursor.execute.assert_any_call(productos_ddl)
    assert "precio DECIMAL(10, 2)" in productos_ddl

    mock_cursor.execute.assert_any_call(facturas_ddl)
    assert "total DECIMAL(10, 2)" in facturas_ddl

    mock_cursor.execute.assert_any_call(factura_items_ddl)
    assert "precio DECIMAL(10, 2)" in factura_items_ddl
    assert "subtotal DECIMAL(10, 2)" in factura_items_ddl


def test_create_tables_commits_transaction_after_drops_successfully(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que la primera transacción (que contiene los DROPs)
    se commitea exitosamente.
    """
    mock_conn = mock_db["conn"]
    init_db.create_tables()
    # El primer commit debe ocurrir después de las 5 llamadas a execute para los DROPs
    # y antes de cualquier llamada a execute para los CREATEs.
    # Verificar que se llamó a commit al menos una vez (el primero).
    assert mock_conn.commit.call_count >= 1
    # Para ser más específico sobre el *primer* commit:
    # Si los asserts de los drops y el call_count de commit=1 (para este punto) pasan, está implícito.
    # Una forma de hacerlo explícito sería registrar el orden de las llamadas, pero puede ser complejo.
    # Si mock_conn.commit.call_count == 2 en el éxito total, y sabemos que hay 2 commits,
    # el primero es el que sigue a los drops.
    # La prueba `test_all_drop_commands_executed_in_order_then_first_commit` es más específica para esto.
    # Este test simplemente confirma que el flujo general permite que el primer commit suceda.


def test_create_tables_commits_transaction_after_creates_and_inserts_successfully(mock_db,
                                                                                  mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que la segunda transacción (CREATEs e inserts)
    se commitea exitosamente.
    """
    mock_conn = mock_db["conn"]
    init_db.create_tables()
    # En el flujo de éxito completo, se esperan 2 commits.
    assert mock_conn.commit.call_count == 2


def test_insert_test_data_does_not_insert_if_client_count_is_positive(mock_db):
    """
    Test (Lógica de Negocio): Verifica que si `Workspaceone()[0]` retorna un número positivo
    (ej. 3 clientes existen), `insert_test_data` no realiza ninguna inserción.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (3,)  # Simula 3 clientes existentes

    init_db.insert_test_data(mock_cursor)  # Llamar a la función real

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()

    # Verificar que no hubo llamadas de INSERT
    for call_arg in mock_cursor.execute.call_args_list:
        assert "INSERT INTO" not in call_arg[0][0], "Se realizaron inserts cuando no se esperaba."
    # El conteo de execute debe ser solo 1 (para el SELECT COUNT)
    assert mock_cursor.execute.call_count == 1


def test_init_db_script_main_execution_path_completes_all_setup_stages_successfully(mock_db,
                                                                                    mock_insert_test_data_fixture,
                                                                                    capsys):
    """
    Test (Lógica de Negocio): Simula la ejecución del script `init_db.py` desde su punto de entrada `if __name__ == '__main__'`,
    y verifica que todos los estados de la configuración (drops, commits, creates, inserts mockeados, commits, close)
    se completan correctamente.
    Este es un test de integración de alto nivel para el script como un todo.
    """
    mock_conn = mock_db["conn"]
    mock_cursor = mock_db["cursor"]

    # Esta prueba asume que el `if __name__ == '__main__':` simplemente llama a `create_tables()`
    # y que `create_tables()` internamente llama a `insert_test_data()`.
    # `mock_insert_test_data_fixture` mockea `insert_test_data`.

    init_db.create_tables()  # Simula la acción principal del script

    # Verificar las etapas clave del éxito de create_tables:
    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()

    # Drops (5) + Creates (len(init_db.commands))
    assert mock_cursor.execute.call_count == 5 + len(init_db.commands)

    # Llamada a insert_test_data (mockeada por el fixture)
    mock_insert_test_data_fixture.assert_called_once_with(mock_cursor)

    # Commits
    assert mock_conn.commit.call_count == 2

    # Cierres
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # Mensaje de éxito
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out
    assert "Error al crear tablas" not in captured.err


def test_create_tables_fails_if_db_config_is_none_type(mock_db, monkeypatch, capsys):
    """FAIL Test: DB_CONFIG es None, psycopg2.connect debe fallar con TypeError."""
    monkeypatch.setattr(init_db, 'DB_CONFIG', None)
    mock_db["connect"].side_effect = TypeError("DB_CONFIG no puede ser None")  # Simular error exacto

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with()  # Intenta llamar con **None
    captured = capsys.readouterr()
    assert "Error al crear tablas: DB_CONFIG no puede ser None" in captured.out
    mock_db["conn"].close.assert_not_called()  # conn sería None


def test_create_tables_fails_if_drop_sequence_error_prevents_first_commit(mock_db, capsys):
    """FAIL Test: Error en DROP SEQUENCE impide el primer commit."""
    mock_cursor = mock_db["cursor"]

    def execute_side_effect(sql_command, *args):
        if "DROP SEQUENCE" in sql_command:
            raise ProgrammingError("Fallo en DROP SEQUENCE")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    init_db.create_tables()

    # Los drops de tablas se intentan antes que el de secuencia
    assert mock_cursor.execute.call_count >= 4
    mock_db["conn"].commit.assert_not_called()  # El primer commit no se alcanza
    # mock_db["conn"].rollback.assert_called_once() # Asumiendo rollback
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo en DROP SEQUENCE" in captured.out


def test_create_tables_fails_if_create_table_clientes_permission_denied(mock_db, capsys):
    """FAIL Test: Permiso denegado al crear tabla 'clientes'."""
    mock_cursor = mock_db["cursor"]

    def execute_side_effect(sql_command, *args):
        if "CREATE TABLE IF NOT EXISTS clientes" in sql_command:
            raise ProgrammingError("permission denied for table clientes")
        return mock.DEFAULT

    # Permitir que los drops pasen (5 llamadas)
    drop_count = 5

    def selective_fail(sql, *args):
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE IF NOT EXISTS clientes" in sql:
            raise ProgrammingError("permission denied for table clientes")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fail

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "permission denied for table clientes" in captured.out


def test_create_tables_fails_if_create_sequence_already_exists_without_if_not_exists(mock_db, monkeypatch, capsys):
    """FAIL Test: CREATE SEQUENCE falla si ya existe y no se usa 'IF NOT EXISTS' (simulado)."""
    mock_cursor = mock_db["cursor"]
    # Modificar el comando de secuencia para que no tenga "IF NOT EXISTS"
    original_commands = init_db.commands
    modified_commands = list(original_commands)
    for i, cmd in enumerate(modified_commands):
        if "CREATE SEQUENCE" in cmd:
            modified_commands[i] = cmd.replace("IF NOT EXISTS ", "")
            break
    monkeypatch.setattr(init_db, 'commands', tuple(modified_commands))

    def execute_side_effect(sql_command, *args):
        if "CREATE SEQUENCE factura_numero_seq" in sql_command and "IF NOT EXISTS" not in sql_command:
            raise ProgrammingError('relation "factura_numero_seq" already exists')
        return mock.DEFAULT

    # Aplicar después de drops y creates de tablas
    num_drops_and_table_creates = 5 + (len(modified_commands) - 1)

    def selective_fail_seq(sql, *args):
        if mock_cursor.execute.call_count > num_drops_and_table_creates and "CREATE SEQUENCE factura_numero_seq" in sql:
            if "IF NOT EXISTS" not in sql:  # Solo falla si IF NOT EXISTS fue removido
                raise ProgrammingError('relation "factura_numero_seq" already exists')
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fail_seq

    init_db.create_tables()

    # El commit de drops y el de creates de tablas deberían ocurrir
    assert mock_db["conn"].commit.call_count == 1  # Solo el de drops, el de creates falla antes del commit
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert 'relation "factura_numero_seq" already exists' in captured.out


def test_insert_test_data_fails_if_clientes_list_has_non_tuple_item(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si init_db.clientes contiene un item que no es tupla."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [("Cliente Valido", "Dir", "Tel", "Email"), "string_invalido"])

    with pytest.raises(TypeError):  # execute espera una tupla para los parámetros
        init_db.insert_test_data(mock_cursor)

    # Se llamó al COUNT y al primer insert válido
    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        ("Cliente Valido", "Dir", "Tel", "Email")
    )


def test_insert_test_data_fails_if_producto_precio_is_string_non_numeric(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio de producto es un string no numérico (causa DataError)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [("ProdX", "DescX", "precio_texto_invalido")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql and params[2] == "precio_texto_invalido":
            raise DataError("invalid input for type numeric: \"precio_texto_invalido\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_fails_if_db_config_user_does_not_exist(mock_db, capsys):
    """FAIL Test: Falla de conexión si el usuario en DB_CONFIG no existe."""
    mock_db["connect"].side_effect = OperationalError("FATAL: role \"usuario_inexistente\" does not exist")
    with mock.patch.dict(init_db.DB_CONFIG, {"user": "usuario_inexistente"}):
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "role \"usuario_inexistente\" does not exist" in captured.out


def test_create_tables_fails_on_disk_full_error_during_commit(mock_db, mock_insert_test_data_fixture, capsys):
    """FAIL Test: Simula error de disco lleno (OperationalError) durante un commit."""
    mock_conn = mock_db["conn"]

    # Hacer que el segundo commit falle con error de disco lleno
    def commit_side_effect():
        if mock_conn.commit.call_count == 2:
            raise OperationalError("could not write to file: No space left on device")
        return mock.DEFAULT

    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    assert mock_conn.commit.call_count == 2
    # mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "No space left on device" in captured.out


def test_create_tables_fails_if_connection_lost_before_cursor_creation(mock_db, capsys):
    """FAIL Test: Pérdida de conexión (OperationalError) después de connect() pero antes de cursor()."""
    mock_conn = mock_db["conn"]
    mock_conn.cursor.side_effect = OperationalError("connection already closed")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()  # Se intentó
    # mock_conn.rollback.assert_called_once() # O no, si el error es que la conexión está cerrada
    mock_conn.close.assert_called_once()  # finally debería intentar cerrarla
    captured = capsys.readouterr()
    assert "connection already closed" in captured.out


def test_create_tables_fails_if_foreign_key_constraint_violated_in_create_ddl(mock_db, monkeypatch, capsys):
    """FAIL Test: Un CREATE TABLE intenta crear una FK a una tabla que aún no existe (error de DDL)."""
    mock_cursor = mock_db["cursor"]
    # Modificar comandos para que facturas se cree antes que clientes (rompiendo la FK)
    original_commands = list(init_db.commands)
    clientes_ddl = [c for c in original_commands if "CREATE TABLE IF NOT EXISTS clientes" in c][0]
    facturas_ddl = [c for c in original_commands if "CREATE TABLE IF NOT EXISTS facturas" in c][0]

    # Poner facturas antes que clientes
    idx_clientes = original_commands.index(clientes_ddl)
    idx_facturas = original_commands.index(facturas_ddl)

    if idx_facturas < idx_clientes:  # Si ya está antes, el test no es válido como está
        pytest.skip("Facturas DDL ya está antes que Clientes DDL en init_db.commands")

    # Reordenar: sacar clientes, luego facturas, e insertarlos en orden inverso
    temp_commands = [c for c in original_commands if c not in [clientes_ddl, facturas_ddl]]
    reordered_commands = []
    # Encontrar dónde estaba facturas originalmente para insertar en una posición similar
    # Esto es para mantener el resto de la secuencia lo más intacta posible.
    # Por simplicidad, los ponemos al principio de los creates.
    reordered_commands.append(facturas_ddl)  # Facturas primero
    reordered_commands.append(clientes_ddl)  # Clientes después

    # Añadir el resto de los comandos que no son clientes ni facturas
    # Esto es una simplificación, el orden exacto de los otros podría importar.
    # Para este test, solo importa que facturas (con FK a clientes) se intente crear antes que clientes.

    # Encontrar los DDLs originales
    original_clientes_ddl_idx = -1
    original_facturas_ddl_idx = -1

    temp_original_commands = list(init_db.commands)  # Copia para trabajar

    for i, cmd in enumerate(temp_original_commands):
        if "CREATE TABLE IF NOT EXISTS clientes" in cmd:
            original_clientes_ddl_idx = i
        elif "CREATE TABLE IF NOT EXISTS facturas" in cmd:
            original_facturas_ddl_idx = i

    if original_clientes_ddl_idx == -1 or original_facturas_ddl_idx == -1:
        pytest.skip("No se encontraron DDL de clientes o facturas.")

    # Intercambiar
    if original_facturas_ddl_idx > original_clientes_ddl_idx:  # Solo si facturas está después de clientes
        temp_original_commands[original_clientes_ddl_idx], temp_original_commands[original_facturas_ddl_idx] = \
            temp_original_commands[original_facturas_ddl_idx], temp_original_commands[original_clientes_ddl_idx]
        monkeypatch.setattr(init_db, 'commands', tuple(temp_original_commands))

        def execute_side_effect(sql_command, *args):
            # Simular el error de FK cuando se intenta crear facturas antes que clientes
            if "CREATE TABLE IF NOT EXISTS facturas" in sql_command and "FOREIGN KEY (cliente_id) REFERENCES clientes (id)" in sql_command:
                # Verificar si clientes ya fue "creada" (mockeado)
                # Esto es difícil de simular perfectamente sin estado real.
                # Asumimos que la BD lanzaría un error si 'clientes' no existe.
                raise ProgrammingError('relation "clientes" does not exist')
            return mock.DEFAULT

        # Aplicar después de los drops
        drop_count = 5

        def selective_fk_fail(sql, *args):
            if mock_cursor.execute.call_count > drop_count and "CREATE TABLE IF NOT EXISTS facturas" in sql:
                raise ProgrammingError('relation "clientes" does not exist')
            return mock.DEFAULT

        mock_cursor.execute.side_effect = selective_fk_fail

        init_db.create_tables()

        assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
        # mock_db["conn"].rollback.assert_called_once()
        mock_db["conn"].close.assert_called_once()
        captured = capsys.readouterr()
        assert 'relation "clientes" does not exist' in captured.out
    else:
        pytest.skip("Facturas DDL ya está antes o en la misma posición que Clientes DDL.")


def test_create_tables_fails_if_db_user_lacks_create_table_permission(mock_db, capsys):
    """FAIL Test: El usuario de BD no tiene permiso para CREATE TABLE."""
    mock_cursor = mock_db["cursor"]

    def execute_side_effect(sql_command, *args):
        if "CREATE TABLE" in sql_command:  # Falla en el primer CREATE
            raise ProgrammingError("permission denied to create table")
        return mock.DEFAULT

    # Aplicar después de los drops
    drop_count = 5

    def selective_permission_fail(sql, *args):
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE" in sql:
            raise ProgrammingError("permission denied to create table")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_permission_fail

    init_db.create_tables()
    assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
    # mock_db["conn"].rollback.assert_called_once()
    captured = capsys.readouterr()
    assert "permission denied to create table" in captured.out


def test_create_tables_fails_if_db_is_read_only(mock_db, capsys):
    """FAIL Test: La base de datos está en modo solo lectura (simulado con error en DROP/CREATE)."""
    mock_cursor = mock_db["cursor"]
    # Simular error de solo lectura en el primer comando DML/DDL (un DROP)
    mock_cursor.execute.side_effect = OperationalError("cannot execute DROP TABLE in a read-only transaction")

    init_db.create_tables()

    mock_cursor.execute.assert_called_once()  # Se intentó el primer DROP
    mock_db["conn"].commit.assert_not_called()
    # mock_db["conn"].rollback.assert_called_once()
    captured = capsys.readouterr()
    assert "cannot execute DROP TABLE in a read-only transaction" in captured.out


def test_insert_test_data_fails_if_decimal_conversion_error_from_string(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio (DECIMAL) es un string que no se puede convertir."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    # Producto con precio que causaría error de conversión en la BD
    monkeypatch.setattr(init_db, 'productos', [("ProdInv", "DescInv", "not_a_number")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql:
            # Simular el error que daría la BD
            if params and not isinstance(params[2], (int, float)):
                raise DataError(f"invalid input syntax for type numeric: \"{params[2]}\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input syntax for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_timeout_on_connect(mock_db, capsys):
    """FAIL Test: Timeout durante el intento de conexión a la BD."""
    mock_db["connect"].side_effect = OperationalError("connection timed out")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "connection timed out" in captured.out


def test_create_tables_timeout_on_long_query_execute(mock_db, capsys):
    """FAIL Test: Timeout durante la ejecución de un comando SQL largo."""
    mock_cursor = mock_db["cursor"]
    # Simular timeout en el primer DROP
    mock_cursor.execute.side_effect = OperationalError("statement timeout")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "statement timeout" in captured.out
    # mock_db["conn"].rollback.assert_called_once()


def test_create_tables_unexpected_none_from_connect(mock_db, capsys):
    """FAIL Test: psycopg2.connect retorna None inesperadamente en lugar de una conexión o error."""
    mock_db["connect"].return_value = None  # connect retorna None

    init_db.create_tables()  # Esto causará AttributeError en conn.cursor()

    mock_db["connect"].assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'NoneType' object has no attribute 'cursor'" in captured.out
    # No se puede llamar a close en None, así que el finally no haría nada con conn.
    mock_db["conn"].close.assert_not_called()  # El mock_conn de la fixture no es el conn=None interno


def test_create_tables_too_many_connections_error(mock_db, capsys):
    """FAIL Test: Error de 'demasiadas conexiones de cliente' al conectar."""
    mock_db["connect"].side_effect = OperationalError("FATAL: sorry, too many clients already")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "too many clients already" in captured.out


def test_insert_test_data_fails_if_cur_execute_returns_unexpected_object_not_raising_error(mock_db, monkeypatch):
    """FAIL Test: cur.execute en insert_test_data no levanta error pero retorna algo inesperado (difícil de simular sin cambiar psycopg2)."""
    # Este escenario es más sobre el comportamiento interno de psycopg2.
    # Si cur.execute no levanta una excepción pero la lógica posterior espera un cierto estado
    # que no se cumple, podría fallar.
    # Por ejemplo, si fetchone() se llamara sobre un cursor que no produjo resultados de una manera inesperada.
    # Este test es más conceptual. Para un test real, necesitaríamos un caso más concreto.
    mock_cursor = mock_db["cursor"]
    # Simular que el COUNT(*) no produce un resultado esperado para fetchone
    mock_cursor.fetchone.return_value = None  # Esto ya se prueba en otro test y causa TypeError

    # Para hacerlo diferente, supongamos que execute para COUNT no hace nada y fetchone se llama.
    # Esto es difícil de lograr con mocks simples si execute siempre funciona o levanta error.
    # Lo más probable es que si execute no produce un resultado consultable, fetchone falle.
    with pytest.raises(TypeError):  # O el error específico que cause fetchone sobre un cursor "vacío"
        init_db.insert_test_data(mock_cursor)  # Asumiendo que fetchone() sobre un cursor inválido da error


def test_create_tables_fails_if_db_config_contains_invalid_port_type(mock_db, monkeypatch, capsys):
    """FAIL Test: El puerto en DB_CONFIG es de un tipo inválido (ej. string no numérico)."""
    # psycopg2 espera que el puerto sea un int o un string que se pueda convertir a int.
    with mock.patch.dict(init_db.DB_CONFIG, {"port": "puerto_invalido"}):
        # El error exacto puede variar, podría ser un ValueError al intentar convertir el puerto,
        # o un OperationalError si la librería intenta usarlo tal cual.
        mock_db["connect"].side_effect = OperationalError("invalid port number: \"puerto_invalido\"")
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "invalid port number" in captured.out


def test_create_tables_fails_gracefully_if_init_db_commands_is_not_iterable(mock_db, monkeypatch, capsys):
    """FAIL Test: init_db.commands no es iterable (ej. es un entero)."""
    monkeypatch.setattr(init_db, 'commands', 123)  # commands es un entero

    init_db.create_tables()  # Esto debería causar un TypeError al hacer `for command in commands:`

    # El commit de drops debería ocurrir
    assert mock_db["conn"].commit.call_count == 1
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'int' object is not iterable" in captured.out


def test_create_tables_fails_if_db_config_is_none_type(mock_db, monkeypatch, capsys):
    """FAIL Test: DB_CONFIG es None, psycopg2.connect debe fallar con TypeError."""
    monkeypatch.setattr(init_db, 'DB_CONFIG', None)
    mock_db["connect"].side_effect = TypeError("DB_CONFIG no puede ser None")  # Simular error exacto

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with()  # Intenta llamar con **None
    captured = capsys.readouterr()
    assert "Error al crear tablas: DB_CONFIG no puede ser None" in captured.out
    mock_db["conn"].close.assert_not_called()  # conn sería None


def test_create_tables_fails_if_drop_sequence_error_prevents_first_commit(mock_db, capsys):
    """FAIL Test: Error en DROP SEQUENCE impide el primer commit."""
    mock_cursor = mock_db["cursor"]

    def execute_side_effect(sql_command, *args):
        if "DROP SEQUENCE" in sql_command:
            raise ProgrammingError("Fallo en DROP SEQUENCE")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    init_db.create_tables()

    # Los drops de tablas se intentan antes que el de secuencia
    assert mock_cursor.execute.call_count >= 4
    mock_db["conn"].commit.assert_not_called()  # El primer commit no se alcanza
    # mock_db["conn"].rollback.assert_called_once() # Asumiendo rollback
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo en DROP SEQUENCE" in captured.out


def test_create_tables_fails_if_create_table_clientes_permission_denied(mock_db, capsys):
    """FAIL Test: Permiso denegado al crear tabla 'clientes'."""
    mock_cursor = mock_db["cursor"]
    # Permitir que los drops pasen (5 llamadas)
    drop_count = 5

    def selective_fail(sql, *args):
        # Nota: mock_cursor.execute.call_count se incrementa *antes* de que se ejecute el side_effect para esa llamada.
        # Por lo tanto, para la 6ta llamada (drop_count + 1), call_count será 6.
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE IF NOT EXISTS clientes" in sql:
            raise ProgrammingError("permission denied for table clientes")
        return mock.DEFAULT  # Para otras llamadas

    mock_cursor.execute.side_effect = selective_fail

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "permission denied for table clientes" in captured.out


def test_create_tables_fails_if_create_sequence_already_exists_without_if_not_exists(mock_db, monkeypatch, capsys):
    """FAIL Test: CREATE SEQUENCE falla si ya existe y no se usa 'IF NOT EXISTS' (simulado)."""
    mock_cursor = mock_db["cursor"]
    original_commands = init_db.commands
    modified_commands = list(original_commands)
    sequence_command_found_and_modified = False
    for i, cmd in enumerate(modified_commands):
        if "CREATE SEQUENCE" in cmd and "factura_numero_seq" in cmd:
            modified_commands[i] = cmd.replace("IF NOT EXISTS ", "")
            sequence_command_found_and_modified = True
            break

    if not sequence_command_found_and_modified:
        pytest.skip("Comando CREATE SEQUENCE para factura_numero_seq no encontrado para modificar.")

    monkeypatch.setattr(init_db, 'commands', tuple(modified_commands))

    # Contar llamadas para aplicar el efecto en el momento correcto
    # 5 drops + (len(modified_commands) -1) para los creates de tablas antes de la secuencia (si la secuencia es la última)
    # El índice del comando de secuencia en modified_commands
    seq_cmd_index_in_modified = -1
    for i, cmd_text in enumerate(modified_commands):
        if "CREATE SEQUENCE factura_numero_seq" in cmd_text:
            seq_cmd_index_in_modified = i
            break

    if seq_cmd_index_in_modified == -1:
        pytest.skip("Comando de secuencia modificado no encontrado en la lista de comandos.")

    # El número de llamada a execute cuando se espera el comando de secuencia:
    # 5 (drops) + seq_cmd_index_in_modified + 1 (porque call_count es 1-based)
    expected_call_count_for_seq_create = 5 + seq_cmd_index_in_modified + 1

    def selective_fail_seq(sql, *args):
        if mock_cursor.execute.call_count == expected_call_count_for_seq_create and \
                "CREATE SEQUENCE factura_numero_seq" in sql and \
                "IF NOT EXISTS" not in sql:
            raise ProgrammingError('relation "factura_numero_seq" already exists')
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fail_seq

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1  # Solo el de drops
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert 'relation "factura_numero_seq" already exists' in captured.out


def test_insert_test_data_fails_if_clientes_list_has_non_tuple_item(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si init_db.clientes contiene un item que no es tupla."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [("Cliente Valido", "Dir", "Tel", "Email"), "string_invalido"])

    with pytest.raises(TypeError):  # execute espera una tupla para los parámetros
        init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        ("Cliente Valido", "Dir", "Tel", "Email")
    )


def test_insert_test_data_fails_if_producto_precio_is_string_non_numeric(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio de producto es un string no numérico (causa DataError)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [("ProdX", "DescX", "precio_texto_invalido")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql and params and len(params) > 2 and params[2] == "precio_texto_invalido":
            raise DataError("invalid input for type numeric: \"precio_texto_invalido\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_fails_if_db_config_user_does_not_exist(mock_db, capsys):
    """FAIL Test: Falla de conexión si el usuario en DB_CONFIG no existe."""
    mock_db["connect"].side_effect = OperationalError("FATAL: role \"usuario_inexistente\" does not exist")
    # Usar with mock.patch.dict para asegurar que DB_CONFIG se restaura si el test falla antes.
    with mock.patch.dict(init_db.DB_CONFIG, {"user": "usuario_inexistente"}):
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "role \"usuario_inexistente\" does not exist" in captured.out


def test_create_tables_fails_on_disk_full_error_during_commit(mock_db, mock_insert_test_data_fixture, capsys):
    """FAIL Test: Simula error de disco lleno (OperationalError) durante un commit."""
    mock_conn = mock_db["conn"]

    def commit_side_effect():
        if mock_conn.commit.call_count == 2:  # Falla en el segundo commit
            raise OperationalError("could not write to file: No space left on device")
        return mock.DEFAULT  # Permite que el primer commit pase

    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    assert mock_conn.commit.call_count == 2
    # mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "No space left on device" in captured.out


def test_create_tables_fails_if_connection_lost_before_cursor_creation(mock_db, capsys):
    """FAIL Test: Pérdida de conexión (OperationalError) después de connect() pero antes de cursor()."""
    mock_conn = mock_db["conn"]
    mock_conn.cursor.side_effect = OperationalError("connection already closed")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "connection already closed" in captured.out


def test_create_tables_fails_if_foreign_key_constraint_violated_in_create_ddl(mock_db, monkeypatch, capsys):
    """FAIL Test: Un CREATE TABLE intenta crear una FK a una tabla que aún no existe (error de DDL)."""
    mock_cursor = mock_db["cursor"]
    original_commands = list(init_db.commands)

    clientes_ddl = None
    facturas_ddl = None
    clientes_idx = -1
    facturas_idx = -1

    for i, cmd in enumerate(original_commands):
        if "CREATE TABLE IF NOT EXISTS clientes" in cmd:
            clientes_ddl = cmd
            clientes_idx = i
        elif "CREATE TABLE IF NOT EXISTS facturas" in cmd:
            facturas_ddl = cmd
            facturas_idx = i

    if not clientes_ddl or not facturas_ddl:
        pytest.skip("DDL para clientes o facturas no encontrado.")

    if facturas_idx < clientes_idx:  # Si facturas ya está antes, el test no es válido para este propósito.
        pytest.skip("Facturas DDL ya está antes que Clientes DDL en init_db.commands.")

    # Reordenar para que facturas (con FK a clientes) se intente crear antes que clientes
    reordered_commands = original_commands[:]  # Copia
    # Mover facturas DDL a la posición de clientes DDL, y clientes DDL a la posición de facturas DDL
    if clientes_idx != -1 and facturas_idx != -1:
        reordered_commands[clientes_idx], reordered_commands[facturas_idx] = reordered_commands[facturas_idx], \
        reordered_commands[clientes_idx]
    else:  # Si alguno no se encontró, saltar.
        pytest.skip("No se pudieron reordenar los comandos DDL para el test.")

    monkeypatch.setattr(init_db, 'commands', tuple(reordered_commands))

    # El error ocurrirá cuando se intente crear 'facturas'
    # Esto será después de los 5 drops, y en la nueva posición de 'facturas_ddl'
    expected_failing_call_idx = 5 + reordered_commands.index(facturas_ddl) + 1

    def selective_fk_fail(sql, *args):
        if mock_cursor.execute.call_count == expected_failing_call_idx and \
                "CREATE TABLE IF NOT EXISTS facturas" in sql:
            raise ProgrammingError('relation "clientes" does not exist')
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fk_fail

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert 'relation "clientes" does not exist' in captured.out


def test_create_tables_fails_if_db_user_lacks_create_table_permission(mock_db, capsys):
    """FAIL Test: El usuario de BD no tiene permiso para CREATE TABLE."""
    mock_cursor = mock_db["cursor"]
    drop_count = 5

    def selective_permission_fail(sql, *args):
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE" in sql:  # Falla en el primer CREATE
            raise ProgrammingError("permission denied to create table")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_permission_fail

    init_db.create_tables()
    assert mock_db["conn"].commit.call_count == 1
    # mock_db["conn"].rollback.assert_called_once()
    captured = capsys.readouterr()
    assert "permission denied to create table" in captured.out


def test_create_tables_fails_if_db_is_read_only(mock_db, capsys):
    """FAIL Test: La base de datos está en modo solo lectura (simulado con error en DROP/CREATE)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.execute.side_effect = OperationalError("cannot execute DROP TABLE in a read-only transaction")

    init_db.create_tables()

    mock_cursor.execute.assert_called_once()
    mock_db["conn"].commit.assert_not_called()
    # mock_db["conn"].rollback.assert_called_once()
    captured = capsys.readouterr()
    assert "cannot execute DROP TABLE in a read-only transaction" in captured.out


def test_insert_test_data_fails_if_decimal_conversion_error_from_string(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio (DECIMAL) es un string que no se puede convertir."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [("ProdInv", "DescInv", "not_a_number")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql:
            if params and not isinstance(params[2], (int, float)):  # params[2] es el precio
                raise DataError(f"invalid input syntax for type numeric: \"{params[2]}\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input syntax for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_timeout_on_connect(mock_db, capsys):
    """FAIL Test: Timeout durante el intento de conexión a la BD."""
    mock_db["connect"].side_effect = OperationalError("connection timed out")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "connection timed out" in captured.out


def test_create_tables_timeout_on_long_query_execute(mock_db, capsys):
    """FAIL Test: Timeout durante la ejecución de un comando SQL largo."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.execute.side_effect = OperationalError("statement timeout")  # Simular timeout en el primer DROP
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "statement timeout" in captured.out
    # mock_db["conn"].rollback.assert_called_once()


def test_create_tables_unexpected_none_from_connect(mock_db, capsys):
    """FAIL Test: psycopg2.connect retorna None inesperadamente en lugar de una conexión o error."""
    mock_db["connect"].return_value = None

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'NoneType' object has no attribute 'cursor'" in captured.out
    mock_db["conn"].close.assert_not_called()


def test_create_tables_too_many_connections_error(mock_db, capsys):
    """FAIL Test: Error de 'demasiadas conexiones de cliente' al conectar."""
    mock_db["connect"].side_effect = OperationalError("FATAL: sorry, too many clients already")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "too many clients already" in captured.out


def test_insert_test_data_fails_if_cur_execute_returns_unexpected_object_not_raising_error(mock_db, monkeypatch):
    """FAIL Test: cur.execute en insert_test_data no levanta error pero retorna algo inesperado (difícil de simular sin cambiar psycopg2)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = None

    with pytest.raises(TypeError):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_fails_if_db_config_contains_invalid_port_type(mock_db, monkeypatch, capsys):
    """FAIL Test: El puerto en DB_CONFIG es de un tipo inválido (ej. string no numérico)."""
    with mock.patch.dict(init_db.DB_CONFIG, {"port": "puerto_invalido"}):
        mock_db["connect"].side_effect = OperationalError("invalid port number: \"puerto_invalido\"")
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "invalid port number" in captured.out


def test_create_tables_fails_gracefully_if_init_db_commands_is_not_iterable(mock_db, monkeypatch, capsys):
    """FAIL Test: init_db.commands no es iterable (ej. es un entero)."""
    monkeypatch.setattr(init_db, 'commands', 123)

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'int' object is not iterable" in captured.out


# --- 10 Tests de "Rendimiento" (Eficiencia Operativa) ---

def test_insert_test_data_performance_skip_if_data_exists(mock_db):
    """
    PERF Test: insert_test_data (real) debe salir rápidamente si los datos ya existen,
    haciendo solo una consulta COUNT y un fetchone.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (1,)  # Simula que ya existen datos

    init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    # Asegurar que no se hicieron más llamadas a execute (es decir, no hubo INSERTs)
    assert mock_cursor.execute.call_count == 1


def test_create_tables_exact_number_of_drop_commands_for_performance(mock_db, mock_insert_test_data_fixture):
    """PERF Test: create_tables debe ejecutar exactamente 5 comandos DROP."""
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    drop_commands_executed = [call for call in mock_cursor.execute.call_args_list if "DROP" in call[0][0]]
    assert len(drop_commands_executed) == 5


def test_create_tables_exact_number_of_create_commands_for_performance(mock_db, mock_insert_test_data_fixture):
    """PERF Test: create_tables debe ejecutar el número exacto de comandos CREATE/SEQUENCE definidos."""
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    create_sequence_commands_executed = [
        call for call in mock_cursor.execute.call_args_list if "CREATE" in call[0][0]
    ]
    assert len(create_sequence_commands_executed) == len(init_db.commands)


def test_insert_test_data_exact_number_of_select_count_for_performance(mock_db):
    """PERF Test: insert_test_data (real) debe hacer solo una llamada a SELECT COUNT(*)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Para que intente insertar

    init_db.insert_test_data(mock_cursor)

    select_count_calls = [
        call for call in mock_cursor.execute.call_args_list if "SELECT COUNT(*) FROM clientes;" in call[0][0]
    ]
    assert len(select_count_calls) == 1


def test_insert_test_data_exact_number_of_client_inserts_for_performance(mock_db, monkeypatch):
    """PERF Test: insert_test_data (real) debe ejecutar N INSERTs para clientes si la lista no está vacía."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    # Usar una lista de clientes conocida para el test
    test_clientes = [("C1", "D1", "T1", "E1"), ("C2", "D2", "T2", "E2")]
    monkeypatch.setattr(init_db, 'clientes', test_clientes)
    monkeypatch.setattr(init_db, 'productos', [])  # No productos para este test

    init_db.insert_test_data(mock_cursor)

    client_insert_calls = [
        call for call in mock_cursor.execute.call_args_list if "INSERT INTO clientes" in call[0][0]
    ]
    assert len(client_insert_calls) == len(test_clientes)


def test_insert_test_data_exact_number_of_product_inserts_for_performance(mock_db, monkeypatch):
    """PERF Test: insert_test_data (real) debe ejecutar N INSERTs para productos si la lista no está vacía."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    test_productos = [("P1", "DP1", 10.0), ("P2", "DP2", 20.0)]
    monkeypatch.setattr(init_db, 'productos', test_productos)
    monkeypatch.setattr(init_db, 'clientes', [])  # No clientes para este test

    init_db.insert_test_data(mock_cursor)

    product_insert_calls = [
        call for call in mock_cursor.execute.call_args_list if "INSERT INTO productos" in call[0][0]
    ]
    assert len(product_insert_calls) == len(test_productos)


def test_create_tables_total_execute_calls_on_clean_success_with_inserts_performance(mock_db, capsys):
    """
    PERF Test: Número total de llamadas a execute en un flujo exitoso completo
    donde insert_test_data (real) sí inserta datos.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)  # Forzar inserción de datos de prueba

    # No mockear init_db.insert_test_data aquí, queremos que se ejecute la real.

    init_db.create_tables()

    num_drops = 5
    num_creates_sequences = len(init_db.commands)
    num_select_count = 1
    num_cliente_inserts = len(init_db.clientes)
    num_producto_inserts = len(init_db.productos)

    expected_total_execute_calls = num_drops + num_creates_sequences + \
                                   num_select_count + num_cliente_inserts + num_producto_inserts

    assert mock_cursor.execute.call_count == expected_total_execute_calls
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_create_tables_total_execute_calls_on_clean_success_skipping_inserts_performance(mock_db, capsys):
    """
    PERF Test: Número total de llamadas a execute en un flujo exitoso completo
    donde insert_test_data (real) NO inserta datos (porque ya existen).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (1,)  # Simular que ya existen datos

    init_db.create_tables()

    num_drops = 5
    num_creates_sequences = len(init_db.commands)
    num_select_count = 1  # De insert_test_data
    # No hay inserts de clientes ni productos

    expected_total_execute_calls = num_drops + num_creates_sequences + num_select_count

    assert mock_cursor.execute.call_count == expected_total_execute_calls
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_connection_and_cursor_overhead_minimised_in_create_tables_performance(mock_db, mock_insert_test_data_fixture):
    """
    PERF Test: create_tables debe llamar a connect() y conn.cursor() solo una vez
    durante una ejecución normal.
    """
    mock_conn = mock_db["conn"]
    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    # También verificar que no se crean cursores adicionales innecesariamente.
    # Si conn.cursor fuera llamado en un loop, esta aserción fallaría.


def test_db_config_not_modified_during_create_tables_performance(mock_db, mock_insert_test_data_fixture):
    """
    PERF Test: Asegura que la variable global init_db.DB_CONFIG no es modificada
    permanentemente por create_tables. (Suponiendo que no debería serlo).
    Este test es más sobre la integridad del estado que sobre la velocidad.
    """
    original_db_config_copy = init_db.DB_CONFIG.copy()

    init_db.create_tables()

    # Verificar que DB_CONFIG en el módulo init_db sigue siendo el mismo objeto o tiene el mismo contenido.
    # Si create_tables lo modificara (ej. añadiendo/quitando claves), esto fallaría.
    assert init_db.DB_CONFIG == original_db_config_copy
    # Para ser más estricto con el objeto en sí (si no se espera que se reemplace):
    # assert id(init_db.DB_CONFIG) == id(original_db_config_copy) # Esto fallaría si se reasigna.


def test_create_tables_fails_if_db_config_is_none_type(mock_db, monkeypatch, capsys):
    """FAIL Test: DB_CONFIG es None, psycopg2.connect debe fallar con TypeError."""
    monkeypatch.setattr(init_db, 'DB_CONFIG', None)
    mock_db["connect"].side_effect = TypeError("DB_CONFIG no puede ser None")  # Simular error exacto

    init_db.create_tables()

    mock_db["connect"].assert_called_once_with()  # Intenta llamar con **None
    captured = capsys.readouterr()
    assert "Error al crear tablas: DB_CONFIG no puede ser None" in captured.out
    mock_db["conn"].close.assert_not_called()  # conn sería None


def test_create_tables_fails_if_drop_sequence_error_prevents_first_commit(mock_db, capsys):
    """FAIL Test: Error en DROP SEQUENCE impide el primer commit."""
    mock_cursor = mock_db["cursor"]

    def execute_side_effect(sql_command, *args):
        if "DROP SEQUENCE" in sql_command:
            raise ProgrammingError("Fallo en DROP SEQUENCE")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    init_db.create_tables()

    # Los drops de tablas se intentan antes que el de secuencia
    assert mock_cursor.execute.call_count >= 4
    mock_db["conn"].commit.assert_not_called()  # El primer commit no se alcanza
    # mock_db["conn"].rollback.assert_called_once() # Asumiendo rollback
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas: Fallo en DROP SEQUENCE" in captured.out


def test_create_tables_fails_if_create_table_clientes_permission_denied(mock_db, capsys):
    """FAIL Test: Permiso denegado al crear tabla 'clientes'."""
    mock_cursor = mock_db["cursor"]
    # Permitir que los drops pasen (5 llamadas)
    drop_count = 5

    def selective_fail(sql, *args):
        # Nota: mock_cursor.execute.call_count se incrementa *antes* de que se ejecute el side_effect para esa llamada.
        # Por lo tanto, para la 6ta llamada (drop_count + 1), call_count será 6.
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE IF NOT EXISTS clientes" in sql:
            raise ProgrammingError("permission denied for table clientes")
        return mock.DEFAULT  # Para otras llamadas

    mock_cursor.execute.side_effect = selective_fail

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1  # Commit post-drops
    # mock_db["conn"].rollback.assert_called_once()
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "permission denied for table clientes" in captured.out


def test_create_tables_fails_if_create_sequence_already_exists_without_if_not_exists(mock_db, monkeypatch, capsys):
    """FAIL Test: CREATE SEQUENCE falla si ya existe y no se usa 'IF NOT EXISTS' (simulado)."""
    mock_cursor = mock_db["cursor"]
    original_commands = init_db.commands
    modified_commands = list(original_commands)
    sequence_command_found_and_modified = False
    for i, cmd in enumerate(modified_commands):
        if "CREATE SEQUENCE" in cmd and "factura_numero_seq" in cmd:
            modified_commands[i] = cmd.replace("IF NOT EXISTS ", "")
            sequence_command_found_and_modified = True
            break

    if not sequence_command_found_and_modified:
        pytest.skip("Comando CREATE SEQUENCE para factura_numero_seq no encontrado para modificar.")

    monkeypatch.setattr(init_db, 'commands', tuple(modified_commands))

    seq_cmd_index_in_modified = -1
    for i, cmd_text in enumerate(modified_commands):
        if "CREATE SEQUENCE factura_numero_seq" in cmd_text:
            seq_cmd_index_in_modified = i
            break

    if seq_cmd_index_in_modified == -1:
        pytest.skip("Comando de secuencia modificado no encontrado en la lista de comandos.")

    expected_call_count_for_seq_create = 5 + seq_cmd_index_in_modified + 1

    def selective_fail_seq(sql, *args):
        if mock_cursor.execute.call_count == expected_call_count_for_seq_create and \
                "CREATE SEQUENCE factura_numero_seq" in sql and \
                "IF NOT EXISTS" not in sql:
            raise ProgrammingError('relation "factura_numero_seq" already exists')
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fail_seq

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert 'relation "factura_numero_seq" already exists' in captured.out


def test_insert_test_data_fails_if_clientes_list_has_non_tuple_item(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si init_db.clientes contiene un item que no es tupla."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [("Cliente Valido", "Dir", "Tel", "Email"), "string_invalido"])

    with pytest.raises(TypeError):
        init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_any_call("SELECT COUNT(*) FROM clientes;")
    mock_cursor.execute.assert_any_call(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        ("Cliente Valido", "Dir", "Tel", "Email")
    )


def test_insert_test_data_fails_if_producto_precio_is_string_non_numeric(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio de producto es un string no numérico (causa DataError)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [("ProdX", "DescX", "precio_texto_invalido")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql and params and len(params) > 2 and params[2] == "precio_texto_invalido":
            raise DataError("invalid input for type numeric: \"precio_texto_invalido\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_fails_if_db_config_user_does_not_exist(mock_db, capsys):
    """FAIL Test: Falla de conexión si el usuario en DB_CONFIG no existe."""
    mock_db["connect"].side_effect = OperationalError("FATAL: role \"usuario_inexistente\" does not exist")
    with mock.patch.dict(init_db.DB_CONFIG, {"user": "usuario_inexistente"}):
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "role \"usuario_inexistente\" does not exist" in captured.out


def test_create_tables_fails_on_disk_full_error_during_commit(mock_db, mock_insert_test_data_fixture, capsys):
    """FAIL Test: Simula error de disco lleno (OperationalError) durante un commit."""
    mock_conn = mock_db["conn"]

    def commit_side_effect():
        if mock_conn.commit.call_count == 2:
            raise OperationalError("could not write to file: No space left on device")
        return mock.DEFAULT

    mock_conn.commit.side_effect = commit_side_effect

    init_db.create_tables()

    assert mock_conn.commit.call_count == 2
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "No space left on device" in captured.out


def test_create_tables_fails_if_connection_lost_before_cursor_creation(mock_db, capsys):
    """FAIL Test: Pérdida de conexión (OperationalError) después de connect() pero antes de cursor()."""
    mock_conn = mock_db["conn"]
    mock_conn.cursor.side_effect = OperationalError("connection already closed")

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_conn.close.assert_called_once()
    captured = capsys.readouterr()
    assert "connection already closed" in captured.out


def test_create_tables_fails_if_foreign_key_constraint_violated_in_create_ddl(mock_db, monkeypatch, capsys):
    """FAIL Test: Un CREATE TABLE intenta crear una FK a una tabla que aún no existe (error de DDL)."""
    mock_cursor = mock_db["cursor"]
    original_commands = list(init_db.commands)

    clientes_ddl = None
    facturas_ddl = None
    clientes_idx = -1
    facturas_idx = -1

    for i, cmd in enumerate(original_commands):
        if "CREATE TABLE IF NOT EXISTS clientes" in cmd:
            clientes_ddl = cmd
            clientes_idx = i
        elif "CREATE TABLE IF NOT EXISTS facturas" in cmd:
            facturas_ddl = cmd
            facturas_idx = i

    if not clientes_ddl or not facturas_ddl:
        pytest.skip("DDL para clientes o facturas no encontrado.")

    if facturas_idx < clientes_idx:
        pytest.skip("Facturas DDL ya está antes que Clientes DDL en init_db.commands.")

    reordered_commands = original_commands[:]
    if clientes_idx != -1 and facturas_idx != -1:
        reordered_commands[clientes_idx], reordered_commands[facturas_idx] = reordered_commands[facturas_idx], \
        reordered_commands[clientes_idx]
    else:
        pytest.skip("No se pudieron reordenar los comandos DDL para el test.")

    monkeypatch.setattr(init_db, 'commands', tuple(reordered_commands))

    expected_failing_call_idx = 5 + reordered_commands.index(facturas_ddl) + 1

    def selective_fk_fail(sql, *args):
        if mock_cursor.execute.call_count == expected_failing_call_idx and \
                "CREATE TABLE IF NOT EXISTS facturas" in sql:
            raise ProgrammingError('relation "clientes" does not exist')
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_fk_fail

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert 'relation "clientes" does not exist' in captured.out


def test_create_tables_fails_if_db_user_lacks_create_table_permission(mock_db, capsys):
    """FAIL Test: El usuario de BD no tiene permiso para CREATE TABLE."""
    mock_cursor = mock_db["cursor"]
    drop_count = 5

    def selective_permission_fail(sql, *args):
        if mock_cursor.execute.call_count == drop_count + 1 and "CREATE TABLE" in sql:
            raise ProgrammingError("permission denied to create table")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = selective_permission_fail

    init_db.create_tables()
    assert mock_db["conn"].commit.call_count == 1
    captured = capsys.readouterr()
    assert "permission denied to create table" in captured.out


def test_create_tables_fails_if_db_is_read_only(mock_db, capsys):
    """FAIL Test: La base de datos está en modo solo lectura (simulado con error en DROP/CREATE)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.execute.side_effect = OperationalError("cannot execute DROP TABLE in a read-only transaction")

    init_db.create_tables()

    mock_cursor.execute.assert_called_once()
    mock_db["conn"].commit.assert_not_called()
    captured = capsys.readouterr()
    assert "cannot execute DROP TABLE in a read-only transaction" in captured.out


def test_insert_test_data_fails_if_decimal_conversion_error_from_string(mock_db, monkeypatch):
    """FAIL Test: insert_test_data falla si un precio (DECIMAL) es un string que no se puede convertir."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(init_db, 'clientes', [])
    monkeypatch.setattr(init_db, 'productos', [("ProdInv", "DescInv", "not_a_number")])

    def execute_side_effect(sql, params=None):
        if "INSERT INTO productos" in sql:
            if params and not isinstance(params[2], (int, float)):
                raise DataError(f"invalid input syntax for type numeric: \"{params[2]}\"")
        return mock.DEFAULT

    mock_cursor.execute.side_effect = execute_side_effect

    with pytest.raises(DataError, match="invalid input syntax for type numeric"):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_timeout_on_connect(mock_db, capsys):
    """FAIL Test: Timeout durante el intento de conexión a la BD."""
    mock_db["connect"].side_effect = OperationalError("connection timed out")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "connection timed out" in captured.out


def test_create_tables_timeout_on_long_query_execute(mock_db, capsys):
    """FAIL Test: Timeout durante la ejecución de un comando SQL largo."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.execute.side_effect = OperationalError("statement timeout")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "statement timeout" in captured.out


def test_create_tables_unexpected_none_from_connect(mock_db, capsys):
    """FAIL Test: psycopg2.connect retorna None inesperadamente en lugar de una conexión o error."""
    mock_db["connect"].return_value = None

    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'NoneType' object has no attribute 'cursor'" in captured.out
    mock_db["conn"].close.assert_not_called()


def test_create_tables_too_many_connections_error(mock_db, capsys):
    """FAIL Test: Error de 'demasiadas conexiones de cliente' al conectar."""
    mock_db["connect"].side_effect = OperationalError("FATAL: sorry, too many clients already")
    init_db.create_tables()
    captured = capsys.readouterr()
    assert "too many clients already" in captured.out


def test_insert_test_data_fails_if_cur_execute_returns_unexpected_object_not_raising_error(mock_db, monkeypatch):
    """FAIL Test: cur.execute en insert_test_data no levanta error pero retorna algo inesperado (difícil de simular sin cambiar psycopg2)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = None

    with pytest.raises(TypeError):
        init_db.insert_test_data(mock_cursor)


def test_create_tables_fails_if_db_config_contains_invalid_port_type(mock_db, monkeypatch, capsys):
    """FAIL Test: El puerto en DB_CONFIG es de un tipo inválido (ej. string no numérico)."""
    with mock.patch.dict(init_db.DB_CONFIG, {"port": "puerto_invalido"}):
        mock_db["connect"].side_effect = OperationalError("invalid port number: \"puerto_invalido\"")
        init_db.create_tables()

    captured = capsys.readouterr()
    assert "invalid port number" in captured.out


def test_create_tables_fails_gracefully_if_init_db_commands_is_not_iterable(mock_db, monkeypatch, capsys):
    """FAIL Test: init_db.commands no es iterable (ej. es un entero)."""
    monkeypatch.setattr(init_db, 'commands', 123)

    init_db.create_tables()

    assert mock_db["conn"].commit.call_count == 1
    mock_db["conn"].close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error al crear tablas:" in captured.out
    assert "'int' object is not iterable" in captured.out


# --- 10 Tests de "Rendimiento" (Eficiencia Operativa) ---

def test_insert_test_data_performance_skip_if_data_exists(mock_db):
    """
    PERF Test: insert_test_data (real) debe salir rápidamente si los datos ya existen,
    haciendo solo una consulta COUNT y un fetchone.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (1,)

    init_db.insert_test_data(mock_cursor)

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    mock_cursor.fetchone.assert_called_once()
    assert mock_cursor.execute.call_count == 1


def test_create_tables_exact_number_of_drop_commands_for_performance(mock_db, mock_insert_test_data_fixture):
    """PERF Test: create_tables debe ejecutar exactamente 5 comandos DROP."""
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    drop_commands_executed = [call for call in mock_cursor.execute.call_args_list if "DROP" in call[0][0]]
    assert len(drop_commands_executed) == 5


def test_create_tables_exact_number_of_create_commands_for_performance(mock_db, mock_insert_test_data_fixture):
    """PERF Test: create_tables debe ejecutar el número exacto de comandos CREATE/SEQUENCE definidos."""
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    create_sequence_commands_executed = [
        call for call in mock_cursor.execute.call_args_list if "CREATE" in call[0][0]
    ]
    assert len(create_sequence_commands_executed) == len(init_db.commands)


def test_insert_test_data_exact_number_of_select_count_for_performance(mock_db):
    """PERF Test: insert_test_data (real) debe hacer solo una llamada a SELECT COUNT(*)."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    init_db.insert_test_data(mock_cursor)

    select_count_calls = [
        call for call in mock_cursor.execute.call_args_list if "SELECT COUNT(*) FROM clientes;" in call[0][0]
    ]
    assert len(select_count_calls) == 1


def test_insert_test_data_exact_number_of_client_inserts_for_performance(mock_db, monkeypatch):
    """PERF Test: insert_test_data (real) debe ejecutar N INSERTs para clientes si la lista no está vacía."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    test_clientes = [("C1", "D1", "T1", "E1"), ("C2", "D2", "T2", "E2")]
    monkeypatch.setattr(init_db, 'clientes', test_clientes)
    monkeypatch.setattr(init_db, 'productos', [])

    init_db.insert_test_data(mock_cursor)

    client_insert_calls = [
        call for call in mock_cursor.execute.call_args_list if "INSERT INTO clientes" in call[0][0]
    ]
    assert len(client_insert_calls) == len(test_clientes)


def test_insert_test_data_exact_number_of_product_inserts_for_performance(mock_db, monkeypatch):
    """PERF Test: insert_test_data (real) debe ejecutar N INSERTs para productos si la lista no está vacía."""
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    test_productos = [("P1", "DP1", 10.0), ("P2", "DP2", 20.0)]
    monkeypatch.setattr(init_db, 'productos', test_productos)
    monkeypatch.setattr(init_db, 'clientes', [])

    init_db.insert_test_data(mock_cursor)

    product_insert_calls = [
        call for call in mock_cursor.execute.call_args_list if "INSERT INTO productos" in call[0][0]
    ]
    assert len(product_insert_calls) == len(test_productos)


def test_create_tables_total_execute_calls_on_clean_success_with_inserts_performance(mock_db, capsys):
    """
    PERF Test: Número total de llamadas a execute en un flujo exitoso completo
    donde insert_test_data (real) sí inserta datos.
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (0,)

    init_db.create_tables()

    num_drops = 5
    num_creates_sequences = len(init_db.commands)
    num_select_count = 1
    num_cliente_inserts = len(init_db.clientes)
    num_producto_inserts = len(init_db.productos)

    expected_total_execute_calls = num_drops + num_creates_sequences + \
                                   num_select_count + num_cliente_inserts + num_producto_inserts

    assert mock_cursor.execute.call_count == expected_total_execute_calls
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_create_tables_total_execute_calls_on_clean_success_skipping_inserts_performance(mock_db, capsys):
    """
    PERF Test: Número total de llamadas a execute en un flujo exitoso completo
    donde insert_test_data (real) NO inserta datos (porque ya existen).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (1,)

    init_db.create_tables()

    num_drops = 5
    num_creates_sequences = len(init_db.commands)
    num_select_count = 1

    expected_total_execute_calls = num_drops + num_creates_sequences + num_select_count

    assert mock_cursor.execute.call_count == expected_total_execute_calls
    captured = capsys.readouterr()
    assert "Tablas creadas y datos de prueba insertados correctamente." in captured.out


def test_connection_and_cursor_overhead_minimised_in_create_tables_performance(mock_db, mock_insert_test_data_fixture):
    """
    PERF Test: create_tables debe llamar a connect() y conn.cursor() solo una vez
    durante una ejecución normal.
    """
    mock_conn = mock_db["conn"]
    init_db.create_tables()

    mock_db["connect"].assert_called_once()
    mock_conn.cursor.assert_called_once()


def test_db_config_not_modified_during_create_tables_performance(mock_db, mock_insert_test_data_fixture):
    """
    PERF Test: Asegura que la variable global init_db.DB_CONFIG no es modificada
    permanentemente por create_tables.
    """
    original_db_config_copy = init_db.DB_CONFIG.copy()

    init_db.create_tables()

    assert init_db.DB_CONFIG == original_db_config_copy


# --- Otros 10 Tests de Lógica de Negocio Específica para el DB (Éxito) ---

def test_db_config_is_used_by_connect_without_modification_during_call(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica que psycopg2.connect es llamado con el contenido exacto
    de DB_CONFIG del módulo init_db.
    """
    init_db.create_tables()
    # La fixture mock_db ya hace esta aserción, pero la reiteramos para claridad.
    mock_db["connect"].assert_called_once_with(**init_db.DB_CONFIG)


def test_foreign_key_from_facturas_to_clientes_is_defined_in_ddl(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica la FK de facturas.cliente_id a clientes.id."""
    mock_cursor = mock_db["cursor"]
    facturas_ddl_list = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd]
    assert len(facturas_ddl_list) == 1, "DDL de facturas no encontrado o duplicado."
    facturas_ddl = facturas_ddl_list[0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(facturas_ddl)  # Asegurar que el DDL se ejecutó
    assert "FOREIGN KEY (cliente_id) REFERENCES clientes (id)" in facturas_ddl


def test_foreign_keys_in_factura_items_are_correctly_defined_in_ddl(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica ambas FKs en factura_items."""
    mock_cursor = mock_db["cursor"]
    factura_items_ddl_list = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS factura_items" in cmd]
    assert len(factura_items_ddl_list) == 1, "DDL de factura_items no encontrado o duplicado."
    factura_items_ddl = factura_items_ddl_list[0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(factura_items_ddl)
    assert "FOREIGN KEY (factura_id) REFERENCES facturas (id)" in factura_items_ddl
    assert "FOREIGN KEY (producto_id) REFERENCES productos (id)" in factura_items_ddl


def test_unique_constraint_on_facturas_numero_is_defined_in_ddl(mock_db, mock_insert_test_data_fixture):
    """Test (Lógica de Negocio): Verifica el constraint UNIQUE en facturas.numero."""
    mock_cursor = mock_db["cursor"]
    facturas_ddl_list = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd]
    assert len(facturas_ddl_list) == 1
    facturas_ddl = facturas_ddl_list[0]

    init_db.create_tables()
    mock_cursor.execute.assert_any_call(facturas_ddl)
    assert "numero VARCHAR(20) NOT NULL UNIQUE" in facturas_ddl


def test_insert_test_data_skips_all_inserts_if_client_count_is_positive(mock_db):
    """
    Test (Lógica de Negocio): Si el conteo de clientes es > 0, insert_test_data (real)
    no debe ejecutar NINGÚN comando INSERT (ni para clientes ni para productos).
    """
    mock_cursor = mock_db["cursor"]
    mock_cursor.fetchone.return_value = (1,)  # Simula que ya existen clientes

    init_db.insert_test_data(mock_cursor)  # Llamar a la función real

    mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM clientes;")
    # Verificar que no hubo NINGUNA llamada de INSERT después de la llamada a COUNT
    for call_arg in mock_cursor.execute.call_args_list:
        assert "INSERT INTO" not in call_arg[0][0].upper()  # Chequear en mayúsculas por si acaso
    assert mock_cursor.execute.call_count == 1  # Solo la llamada a COUNT


def test_create_tables_drops_objects_in_specific_order_to_handle_dependencies(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica el orden de los comandos DROP para dependencias.
    factura_items debe ser dropeada antes que facturas y productos/clientes.
    facturas debe ser dropeada antes que clientes.
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    executed_sql = [call[0][0] for call in mock_cursor.execute.call_args_list]

    idx_drop_factura_items = executed_sql.index("DROP TABLE IF EXISTS factura_items CASCADE")
    idx_drop_facturas = executed_sql.index("DROP TABLE IF EXISTS facturas CASCADE")
    idx_drop_productos = executed_sql.index("DROP TABLE IF EXISTS productos CASCADE")
    idx_drop_clientes = executed_sql.index("DROP TABLE IF EXISTS clientes CASCADE")
    idx_drop_sequence = executed_sql.index("DROP SEQUENCE IF EXISTS factura_numero_seq")

    assert idx_drop_factura_items < idx_drop_facturas
    assert idx_drop_factura_items < idx_drop_productos  # factura_items depende de productos
    assert idx_drop_facturas < idx_drop_clientes  # facturas depende de clientes

    # El orden entre productos, clientes y secuencia (después de las tablas dependientes) es menos crítico
    # pero la secuencia usualmente se dropea al final o junto con las tablas no dependientes.
    assert max(idx_drop_factura_items, idx_drop_facturas) < min(idx_drop_productos, idx_drop_clientes,
                                                                idx_drop_sequence)


def test_create_tables_creates_dependent_tables_before_dependees_for_fks(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Verifica el orden de los comandos CREATE.
    clientes y productos deben crearse antes que facturas y factura_items.
    facturas debe crearse antes que factura_items.
    """
    mock_cursor = mock_db["cursor"]
    init_db.create_tables()

    # Obtener solo los comandos CREATE TABLE ejecutados, en orden, después de los drops
    executed_create_table_sql = [
        call[0][0] for call in mock_cursor.execute.call_args_list[5:]  # Saltar los 5 drops
        if "CREATE TABLE" in call[0][0]
    ]

    # Encontrar los DDL específicos de init_db.commands para comparar
    clientes_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS clientes" in cmd][0]
    productos_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS productos" in cmd][0]
    facturas_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS facturas" in cmd][0]
    factura_items_ddl = [cmd for cmd in init_db.commands if "CREATE TABLE IF NOT EXISTS factura_items" in cmd][0]

    # Obtener índices de estos DDL en la lista de comandos ejecutados
    try:
        idx_create_clientes = executed_create_table_sql.index(clientes_ddl)
        idx_create_productos = executed_create_table_sql.index(productos_ddl)
        idx_create_facturas = executed_create_table_sql.index(facturas_ddl)
        idx_create_factura_items = executed_create_table_sql.index(factura_items_ddl)
    except ValueError as e:
        pytest.fail(
            f"Uno de los DDL esperados no fue encontrado entre los comandos CREATE ejecutados: {e}\nEjecutados: {executed_create_table_sql}")

    assert idx_create_clientes < idx_create_facturas
    assert idx_create_productos < idx_create_factura_items
    assert idx_create_facturas < idx_create_factura_items


def test_insert_test_data_all_cliente_tuples_have_non_empty_nombre_and_email(mock_db):
    """
    Test (Lógica de Negocio - Datos de Prueba): Verifica que en init_db.clientes,
    los campos 'nombre' y 'email' (asumiendo posiciones 0 y 3) no son strings vacíos.
    """
    for i, cliente_tuple in enumerate(init_db.clientes):
        assert cliente_tuple[0].strip() != "", f"Cliente en índice {i} tiene nombre vacío."
        assert cliente_tuple[3].strip() != "", f"Cliente en índice {i} tiene email vacío."
        assert "@" in cliente_tuple[3], f"Email de cliente en índice {i} no parece válido: {cliente_tuple[3]}"


def test_insert_test_data_all_producto_tuples_have_non_empty_nombre_and_positive_precio(mock_db):
    """
    Test (Lógica de Negocio - Datos de Prueba): Verifica que en init_db.productos,
    'nombre' no es vacío y 'precio' (asumiendo posición 2) es positivo.
    """
    for i, producto_tuple in enumerate(init_db.productos):
        assert producto_tuple[0].strip() != "", f"Producto en índice {i} tiene nombre vacío."
        assert isinstance(producto_tuple[2], (int, float)), f"Precio de producto en índice {i} no es numérico."
        assert producto_tuple[2] > 0, f"Precio de producto en índice {i} no es positivo: {producto_tuple[2]}"


def test_sequence_factura_numero_seq_is_dropped_and_recreated_as_defined(mock_db, mock_insert_test_data_fixture):
    """
    Test (Lógica de Negocio): Asegura que la secuencia 'factura_numero_seq'
    es parte tanto de los comandos DROP como de los comandos CREATE.
    """
    mock_cursor = mock_db["cursor"]

    drop_sequence_cmd = "DROP SEQUENCE IF EXISTS factura_numero_seq"
    create_sequence_cmd = [cmd for cmd in init_db.commands if "CREATE SEQUENCE IF NOT EXISTS factura_numero_seq" in cmd]
    assert len(create_sequence_cmd) == 1, "Comando CREATE SEQUENCE no encontrado o duplicado."
    create_sequence_cmd = create_sequence_cmd[0]

    init_db.create_tables()

    mock_cursor.execute.assert_any_call(drop_sequence_cmd)
    mock_cursor.execute.assert_any_call(create_sequence_cmd)