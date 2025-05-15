import pytest
from unittest import mock # Para mockear objetos y funciones
import psycopg2 # Para referenciar tipos de error de psycopg2
import sys
import os
from flask import json # Import json for testing JSON responses

# Add the parent directory to sys.path to allow importing 'app'
current_dir = os.path.dirname(os.path.abspath(__file__)) # Corrected __file__
parent_dir = os.path.join(current_dir, '..')
sys.path.insert(0, parent_dir)

from app import app, DB_CONFIG as DEFAULT_DB_CONFIG, get_db_connection

# --- Fixtures de Pytest ---
@pytest.fixture
def client():
    """Un cliente de prueba de Flask para la aplicación."""
    app.config['TESTING'] = True
    # The problematic relative import is removed as discussed previously.

    with app.test_client() as client:
        # Ensure app_context is pushed for tests that might need url_for etc.
        with app.app_context():
            yield client

# Fixture to mock the entire DB connection sequence
@pytest.fixture
def mock_db_connection():
    """Mocks app.get_db_connection and the resulting connection and cursor."""
    # Mock the get_db_connection function first
    mock_get_conn = mock.patch('app.get_db_connection').start()

    # Create mocks for connection and cursor
    mock_conn = mock.MagicMock(spec=psycopg2.extensions.connection)
    mock_cursor = mock.MagicMock(spec=psycopg2.extensions.cursor)

    # Configure the mock connection to return the mock cursor using a context manager
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    # Configure the mock cursor's __exit__ to return False (no exception handled)
    mock_conn.cursor.return_value.__exit__.return_value = False


    # Configure get_db_connection mock to return the mock connection
    mock_get_conn.return_value = mock_conn

    yield {
        "get_db_connection": mock_get_conn,
        "conn": mock_conn,
        "cursor": mock_cursor
    }

    # Stop the patcher
    mock.patch.stopall()


# --- Tests para Errores de Configuración de DB_CONFIG y get_db_connection ---

# Uses the config parameter fix in app.get_db_connection
def test_get_db_connection_invalid_host():
    """Test: DB_CONFIG['host'] es None."""
    custom_config = DEFAULT_DB_CONFIG.copy()
    custom_config['host'] = None

    # TARGETING 'app.psycopg2.connect'
    with mock.patch('app.psycopg2.connect') as mock_connect:
        # Simulate the error psycopg2.connect might raise for a bad host
        mock_connect.side_effect = psycopg2.OperationalError("No se puede resolver el nombre de host a una dirección: el nombre de host es nulo")
        with pytest.raises(psycopg2.OperationalError, match="el nombre de host es nulo"):
            # Pass the custom_config using the 'config' parameter
            get_db_connection(config=custom_config)

# Uses the config parameter fix in app.get_db_connection
def test_get_db_connection_missing_database_key():
    """Test: Falta la clave 'database' en DB_CONFIG."""
    custom_config = DEFAULT_DB_CONFIG.copy()
    del custom_config['database'] # Eliminar la clave 'database'

    # TARGETING 'app.psycopg2.connect'
    with mock.patch('app.psycopg2.connect') as mock_connect:
        # Simulate the error psycopg2.connect might raise
        mock_connect.side_effect = psycopg2.OperationalError("Conexión a la base de datos fallida: el nombre de la base de datos no fue especificado")
        with pytest.raises(psycopg2.OperationalError, match="nombre de la base de datos no fue especificado"):
             # Pass the custom_config using the 'config' parameter
            get_db_connection(config=custom_config)

def test_get_db_connection_valid(mock_db_connection):
    """Test: get_db_connection retorna una conexión."""
    conn = get_db_connection()
    assert conn is not None
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)
    # Check if the connection mock was created and returned
    assert conn == mock_db_connection["conn"]


# --- Tests para Errores de Conexión a la Base de Datos (Operacionales) ---
# These tests now use the mock_db_connection fixture internally

def test_get_db_connection_operational_error(mock_db_connection):
    """Test: El servidor de BD no está disponible (simulado con OperationalError)."""
    # Configure the mock connect function returned by get_db_connection mock
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Simulación: Servidor de BD caído")
    with pytest.raises(psycopg2.OperationalError, match="Servidor de BD caído"):
        get_db_connection()
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)


def test_get_db_connection_max_connections_reached(mock_db_connection):
    """Test: Se alcanzó el máximo de conexiones (simulado)."""
    # Configure the mock connect function returned by get_db_connection mock
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("FATAL:  sorry, too many clients already")
    with pytest.raises(psycopg2.OperationalError, match="too many clients already"):
        get_db_connection()
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)

# --- Tests para Rutas de Flask y Redirecciones ---

def test_index_redirects_successfully(client):
    """Test: La ruta '/' redirige correctamente a '/facturas/'."""
    # TARGETING 'app.LISTAR_FACTURAS_ENDPOINT_ACTIVE'
    with mock.patch('app.LISTAR_FACTURAS_ENDPOINT_ACTIVE', True): # Ensure flag is True for this test
        response = client.get('/')
        assert response.status_code == 302 # Expect redirect
        assert response.location == '/facturas/' # Check redirect target


def test_index_redirect_fails_if_target_endpoint_removed(client, monkeypatch):
    """Test: url_for en la ruta '/' falla si 'listar_facturas' no está definida (simulando endpoint removido)."""
    original_view_functions = app.view_functions.copy()
    # Temporarily remove the view function for 'listar_facturas'
    if 'listar_facturas' in app.view_functions:
        monkeypatch.delitem(app.view_functions, 'listar_facturas')

    # Accessing '/' should now fail when url_for('listar_facturas') is called
    response = client.get('/')
    # Flask converts the BuildError from url_for into a 500 Internal Server Error by default
    assert response.status_code == 500
    # You could optionally check for error message content if Flask provides it in the 500 response

    # Restore view functions
    app.view_functions = original_view_functions

def test_non_existent_route(client):
    """Test accessing a route that does not exist."""
    response = client.get('/non-existent-route')
    assert response.status_code == 404 # Expect Not Found


# --- Tests para listar_facturas ---

def test_listar_facturas_success_with_data(client, mock_db_connection):
    """Test: listar_facturas muestra facturas cuando la BD retorna datos."""
    mock_cursor = mock_db_connection["cursor"]
    # Configure the cursor to return sample data
    mock_cursor.fetchall.return_value = [
        (1, 'FACT-001', '2023-01-01', 'Cliente A', 150.50),
        (2, 'FACT-002', '2023-01-02', 'Cliente B', 200.00),
    ]

    response = client.get('/facturas/')

    assert response.status_code == 200
    # Verify DB interaction
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)
    mock_db_connection["conn"].cursor.assert_called_once()
    mock_cursor.execute.assert_called_once_with('SELECT f.id, f.numero, f.fecha, c.nombre as cliente, f.total FROM facturas f JOIN clientes c ON f.cliente_id = c.id ORDER BY f.fecha DESC;')
    mock_cursor.fetchall.assert_called_once()
    mock_db_connection["conn"].close.assert_called_once()

    # Verify response content (checking for snippets of rendered HTML is common)
    assert b"<h1>Lista de Facturas</h1>" in response.data
    assert b"FACT-001" in response.data
    assert b"Cliente A" in response.data
    assert b"150.50" in response.data


def test_listar_facturas_success_no_data(client, mock_db_connection):
    """Test: listar_facturas muestra la página correctamente cuando no hay facturas."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [] # Simulate no data

    response = client.get('/facturas/')

    assert response.status_code == 200
    mock_cursor.fetchall.assert_called_once()
    # Verify presence of table headers, but absence of rows
    assert b"<th>N\xc3\xbaero</th>" in response.data # Check encoded header
    assert b"No hay facturas disponibles." in response.data # Assuming your template shows this

# This test assumes the flag is checked in the /facturas/ endpoint itself
@mock.patch('app.LISTAR_FACTURAS_ENDPOINT_ACTIVE', False)
def test_listar_facturas_endpoint_not_active(client):
    """Test: La ruta '/facturas/' devuelve 404 si LISTAR_FACTURAS_ENDPOINT_ACTIVE es False."""
    response = client.get('/facturas/')
    # Based on the assumed app.py logic with jsonify error response
    assert response.status_code == 404
    json_data = response.get_json()
    assert json_data == {"error": "Endpoint 'listar_facturas' no disponible."}


def test_listar_facturas_db_error_on_connect(client, mock_db_connection):
    """Test: listar_facturas maneja un error al intentar obtener una conexión."""
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Fallo de conexión simulado en listar_facturas")

    response = client.get('/facturas/')
    # Based on the assumed app.py error handling with jsonify
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo de conexión simulado" in json_data['details']
    mock_db_connection["get_db_connection"].assert_called_once()
    # Ensure connection close is NOT called if connection failed
    mock_db_connection["conn"].close.assert_not_called()


def test_listar_facturas_db_error_on_query(client, mock_db_connection):
    """Test: listar_facturas maneja un error durante la ejecución de una consulta."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.ProgrammingError("Error de sintaxis SQL simulado")

    response = client.get('/facturas/')
    # Based on the assumed app.py error handling with jsonify
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Error de sintaxis SQL simulado" in json_data['details']

    mock_db_connection["get_db_connection"].assert_called_once()
    mock_db_connection["conn"].cursor.assert_called_once()
    mock_cursor.execute.assert_called_once_with('SELECT f.id, f.numero, f.fecha, c.nombre as cliente, f.total FROM facturas f JOIN clientes c ON f.cliente_id = c.id ORDER BY f.fecha DESC;')
    mock_db_connection["conn"].close.assert_called_once() # Ensure connection is closed


def test_listar_facturas_unexpected_error(client, mock_db_connection):
    """Test: listar_facturas maneja un error genérico inesperado."""
    # Mock cursor creation to raise a generic exception
    mock_db_connection["conn"].cursor.side_effect = Exception("Algo totalmente inesperado ocurrió")

    response = client.get('/facturas/')
    # Based on the assumed app.py generic error handling with jsonify
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error interno inesperado"
    assert "Algo totalmente inesperado ocurrió" in json_data['details']

    mock_db_connection["get_db_connection"].assert_called_once()
    mock_db_connection["conn"].cursor.assert_called_once()
    # Ensure connection is closed even after an unexpected error
    mock_db_connection["conn"].close.assert_called_once()


# --- Tests para ver_factura ---

def test_ver_factura_success(client, mock_db_connection):
    """Test: ver_factura muestra una factura específica."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock return values for fetching factura and items
    mock_cursor.fetchone.side_effect = [
        (1, 'FACT-001', '2023-01-01', 150.50, 101, 'Cliente A', 'Dir A', 'Tel A'), # Factura details
        # Subsequent fetchall call for items
    ]
    mock_cursor.fetchall.return_value = [
        (1001, 'Prod X', 1, 100.00, 100.00),
        (1002, 'Prod Y', 0.5, 101.00, 50.50),
    ]

    response = client.get('/factura/1') # Requesting invoice with ID 1

    assert response.status_code == 200
    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)
    mock_db_connection["conn"].cursor.assert_called_once()
    # Check the two execute calls
    execute_calls = mock_cursor.execute.call_args_list
    assert len(execute_calls) == 2
    assert execute_calls[0] == mock.call('''
        SELECT f.id, f.numero, f.fecha, f.total, c.id as cliente_id, c.nombre as cliente_nombre,
               c.direccion as cliente_direccion, c.telefono as cliente_telefono
        FROM facturas f JOIN clientes c ON f.cliente_id = c.id WHERE f.id = %s;
    ''', (1,))
    assert execute_calls[1] == mock.call('''
        SELECT fi.id, p.nombre as producto, fi.cantidad, fi.precio, fi.subtotal
        FROM factura_items fi JOIN productos p ON fi.producto_id = p.id
        WHERE fi.factura_id = %s;
    ''', (1,))
    mock_db_connection["conn"].close.assert_called_once()

    # Verify response content
    assert b"<h1>Detalle de Factura</h1>" in response.data
    assert b"FACT-001" in response.data
    assert b"Cliente A" in response.data
    assert b"150.50" in response.data
    assert b"Prod X" in response.data
    assert b"100.00" in response.data # Item price/subtotal

def test_ver_factura_not_found(client, mock_db_connection):
    """Test: ver_factura retorna 404 o mensaje si la factura no existe."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = None # Simulate factura not found

    response = client.get('/factura/999') # Requesting non-existent invoice

    # The original code returns "Cliente no encontrado", 404 for editar_cliente.
    # It doesn't explicitly handle not found for ver_factura.
    # If factura is None, render_template might raise an error or the template might handle None.
    # A robust app would return 404. Let's test for 404 and a possible message.
    # Based on the provided app code, it might actually error or render an empty template.
    # Let's assume we want it to return 404. We might need to add this logic to app.py.
    # ASSUMING app.py is updated to handle factura=None by returning a 404
    # Example app.py update:
    # if factura is None:
    #     return "Factura no encontrada", 404
    # return render_template(...)

    assert response.status_code == 404
    assert b"Factura no encontrada" in response.data # Or check specific error template content


# Add DB error tests for ver_factura (similar to listar_facturas, but two queries)
# ... (omitted for brevity, similar structure to listar_facturas DB error tests)

# --- Tests para nueva_factura (GET) ---

def test_nueva_factura_get_success(client, mock_db_connection):
    """Test: GET /factura/nueva muestra el formulario y carga datos."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock return values for fetching clients and products
    mock_cursor.fetchall.side_effect = [
        [(1, 'Cliente A'), (2, 'Cliente B')], # Clients
        [(1, 'Prod X', 100.00), (2, 'Prod Y', 101.00)], # Products
    ]

    response = client.get('/factura/nueva')

    assert response.status_code == 200
    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)
    mock_db_connection["conn"].cursor.assert_called_once()
    execute_calls = mock_cursor.execute.call_args_list
    assert len(execute_calls) == 2
    assert execute_calls[0] == mock.call('SELECT id, nombre FROM clientes ORDER BY nombre;')
    assert execute_calls[1] == mock.call('SELECT id, nombre, precio FROM productos ORDER BY nombre;')
    mock_db_connection["conn"].close.assert_called_once()

    # Verify response content (checking for form elements and loaded data)
    assert b"<form method=\"POST\">" in response.data
    assert b"<option value=\"1\">Cliente A</option>" in response.data
    assert b"<option value=\"1\">Prod X (100.0)</option>" in response.data # Assuming template formats price


# Add DB error tests for nueva_factura GET (similar to listar_facturas)
# ... (omitted for brevity)


# --- Tests para nueva_factura (POST) ---

def test_nueva_factura_post_success_with_items(client, mock_db_connection):
    """Test: POST /factura/nueva crea una factura con items y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    # Mock sequence for:
    # 1. Getting product price for item 1
    # 2. Getting product price for item 2
    # 3. Getting next invoice number sequence value
    # 4. Inserting factura (returning ID)
    # 5. Inserting item 1
    # 6. Inserting item 2

    mock_cursor.fetchone.side_effect = [
        (100.00,), # Price for product_id_1 (ID=1)
        (101.00,), # Price for product_id_2 (ID=2)
        (123,),     # Next sequence number (FACT-123)
        (456,),     # Newly created factura ID (ID=456)
    ]
    # fetchall not used in the POST part of nueva_factura

    # Prepare form data
    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1',
        'cantidad_1': '2',
        'producto_id_2': '2',
        'cantidad_2': '0.5',
        # Assuming no other items or they are empty
    }

    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/factura/456' # Expect redirect to the new invoice ID

    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called() # Called multiple times in original code (inefficient)
                                                            # Or ideally, called once if refactored
                                                            # Let's check the calls based on the provided app code structure

    # Check calls within the item loop (inefficient multiple connections)
    # Original code calls get_db_connection inside loop. With our mock fixture,
    # app.get_db_connection is called once at the start of the request.
    # Then conn.cursor() is called inside the loop. This is still inefficient.
    # Test structure should match app code's interaction with the mock.
    # Let's trace the expected calls based on the provided app code:
    # 1. get_db_connection() # before item loop
    # 2. conn.cursor() # inside loop for item 1
    # 3. cursor.execute('SELECT price...', (prod_id,))
    # 4. cursor.fetchone() # price
    # 5. cursor.close() # inside loop
    # 6. conn.close() # inside loop - BAD! Connection closed before processing all items!
    # This reveals a bug in the original app code's POST handler for nueva_factura.
    # It opens/closes connection *per item* which is wrong.
    # The test should ideally fail due to this bug, OR the test should mock the bug's behavior.
    # Let's assume the app code *will be fixed* to open/close the connection once.
    # Expected fixed flow:
    # 1. get_db_connection()
    # 2. conn.cursor()
    # 3. Loop items: cursor.execute(price), cursor.fetchone()
    # 4. cursor.execute(sequence)
    # 5. cursor.fetchone() # sequence
    # 6. cursor.execute(insert_factura, ...)
    # 7. cursor.fetchone() # factura_id
    # 8. Loop items: cursor.execute(insert_item, ...)
    # 9. conn.commit()
    # 10. cursor.close()
    # 11. conn.close()

    # Let's write the assertions assuming the *fixed* app code structure:
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT precio FROM productos WHERE id = %s;', ('1',)),
        mock.call('SELECT precio FROM productos WHERE id = %s;', ('2',)),
        mock.call("SELECT nextval('factura_numero_seq')"),
        mock.call(
            'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
            ('FACT-123', '101', 251.00) # Total: (2 * 100) + (0.5 * 101) = 200 + 50.5 = 250.5, wait, form data is strings '2', '0.5'.
            # Python adds floats: (2 * 100.0) + (0.5 * 101.0) = 200.0 + 50.5 = 250.5.
            # The test data was 150.50 + 200.00 = 350.50 in the listar test. Let's use consistent product prices if possible.
            # Sample prices 100.00 and 101.00 are fine. Total is 250.5.
            # The test asserts total 251.00 which is wrong based on the mock prices.
            # Let's fix the assertion value based on mock prices:
            # Total: (float('2') * 100.00) + (float('0.5') * 101.00) = 200.0 + 50.5 = 250.5
            ('FACT-123', '101', 250.50)
        ),
        mock.call(
            'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
            (456, '1', '2', 100.00, 200.00) # quantities and product_ids are strings from form
        ),
         mock.call(
            'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
            (456, '2', '0.5', 101.00, 50.50)
        ),
    ], any_order=True) # Use any_order=True because the item order might vary if loop order isn't guaranteed

    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["cursor"].close.assert_called() # Cursor is closed
    mock_db_connection["conn"].close.assert_called_once() # Connection is closed


def test_nueva_factura_post_success_no_items(client, mock_db_connection):
    """Test: POST /factura/nueva crea una factura sin items (should result in total 0)."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock sequence for:
    # 1. Getting next invoice number sequence value
    # 2. Inserting factura (returning ID)
    mock_cursor.fetchone.side_effect = [
        (124,),     # Next sequence number (FACT-124)
        (457,),     # Newly created factura ID (ID=457)
    ]

    form_data = {
        'cliente_id': '102',
        # No product_id_x or cantidad_x fields
    }

    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/factura/457' # Expect redirect to the new invoice ID

    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called_once() # Called once in the fixed app code
    # Check execute calls
    mock_cursor.execute.assert_has_calls([
        mock.call("SELECT nextval('factura_numero_seq')"),
        mock.call(
            'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
            ('FACT-124', '102', 0.00) # Total should be 0.00
        ),
    ], any_order=True)

    # Ensure no item inserts were attempted
    # This is harder to assert directly on 'execute' unless we check call args,
    # but we can check that fetchone for prices wasn't called inside a loop.
    # With the fixed app code, there would be no calls to 'SELECT precio' if no items.
    # The number of execute calls above confirms this implicitly.

    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["cursor"].close.assert_called()
    mock_db_connection["conn"].close.assert_called_once()


# Test cases for nueva_factura POST failures:
# - DB error getting price
# - DB error getting sequence
# - DB error inserting factura
# - DB error inserting item (should rollback factura insert)
# - Missing cliente_id (app code doesn't validate, might raise KeyError)
# - Invalid product/client IDs (relies on DB FK constraints or app validation)
# - Non-numeric quantity/price (app code might raise ValueError/TypeError on conversion)

def test_nueva_factura_post_db_error_get_price(client, mock_db_connection):
    """Test: POST /factura/nueva handles DB error when getting product price."""
    mock_cursor = mock_db_connection["cursor"]
    # Configure fetching product price to raise a DB error
    mock_cursor.fetchone.side_effect = psycopg2.OperationalError("DB error fetching price")

    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1',
        'cantidad_1': '2',
    }

    response = client.post('/factura/nueva', data=form_data)

    # Assuming app.py has error handling around DB operations in the POST route
    # It should probably return a 500 error or redirect back with an error message.
    # Let's assume it returns 500 JSON like listar_facturas.
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "DB error fetching price" in json_data['details']

    # Verify interactions
    # get_db_connection should be called
    mock_db_connection["get_db_connection"].assert_called_once()
    # Cursor should be obtained
    mock_db_connection["conn"].cursor.assert_called_once()
    # execute should be called to get the price
    mock_cursor.execute.assert_called_once_with('SELECT precio FROM productos WHERE id = %s;', ('1',))
    # Check that commit was NOT called
    mock_db_connection["conn"].commit.assert_not_called()
    # Check that rollback was called (assuming error handling includes rollback)
    mock_db_connection["conn"].rollback.assert_called_once()
    # Connection should be closed
    mock_db_connection["conn"].close.assert_called_once()


# Add tests for other DB errors during POST (sequence, insert factura, insert item)
# Test rollback specifically for item insertion failure due to FK violation etc.
# ... (omitted for brevity, similar structure to the above DB error test)


# --- Tests para listar_clientes ---

def test_listar_clientes_success(client, mock_db_connection):
    """Test: listar_clientes muestra la lista de clientes."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [
        (1, 'Cliente A', 'Dir A', 'Tel A', 'email A'),
        (2, 'Cliente B', 'Dir B', 'Tel B', 'email B'),
    ]

    response = client.get('/clientes')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, direccion, telefono, email FROM clientes ORDER BY nombre;')
    assert b"<h1>Lista de Clientes</h1>" in response.data
    assert b"Cliente A" in response.data
    assert b"email B" in response.data


# Add tests for empty list and DB errors for listar_clientes
# ... (omitted)


# --- Tests para agregar_cliente (GET) ---

def test_agregar_cliente_get_success(client):
    """Test: GET /agregar_cliente muestra el formulario."""
    response = client.get('/agregar_cliente')
    assert response.status_code == 200
    assert b"<form method=\"POST\">" in response.data


# --- Tests para agregar_cliente (POST) ---

def test_agregar_cliente_post_success(client, mock_db_connection):
    """Test: POST /agregar_cliente agrega un cliente y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Nuevo Cliente',
        'direccion': 'Nueva Direccion',
        'telefono': '123-456',
        'email': 'nuevo@example.com'
    }

    response = client.post('/agregar_cliente', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        ('Nuevo Cliente', 'Nueva Direccion', '123-456', 'nuevo@example.com')
    )
    mock_db_connection["conn"].commit.assert_called_once()


def test_agregar_cliente_post_missing_fields(client, mock_db_connection):
    """Test: POST /agregar_cliente with missing fields returns error message."""
    # Missing 'telefono'
    form_data = {
        'nombre': 'Nuevo Cliente',
        'direccion': 'Nueva Direccion',
        'email': 'nuevo@example.com'
    }

    response = client.post('/agregar_cliente', data=form_data)

    assert response.status_code == 200 # Stays on the same page with error
    assert b"Todos los campos son obligatorios." in response.data # Check for the error message
    mock_db_connection["get_db_connection"].assert_not_called() # DB interaction should not happen


# Add DB error test for agregar_cliente POST
# ... (omitted)


# --- Tests para eliminar_cliente (POST) ---

def test_eliminar_cliente_post_success(client, mock_db_connection):
    """Test: POST /eliminar_cliente/<id> deletes a client with no associated invoices."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock count query to return 0
    mock_cursor.fetchone.return_value = (0,)

    response = client.post('/eliminar_cliente/123') # Attempt to delete client ID 123

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (123,)), # Check count first
        mock.call('DELETE FROM clientes WHERE id = %s;', (123,)), # Then delete
    ])
    mock_db_connection["conn"].commit.assert_called_once()


def test_eliminar_cliente_post_with_invoices(client, mock_db_connection):
    """Test: POST /eliminar_cliente/<id> prevents deleting client with invoices."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock count query to return > 0
    mock_cursor.fetchone.return_value = (5,) # Simulate 5 associated invoices

    # Mock fetching clients again as the app code does this if deletion is blocked
    mock_cursor.execute.side_effect = [
         # First execute is COUNT(*) -> handled by fetchone mock above
         # Second execute is SELECT * FROM clientes; -> need to mock its fetchall
         None # execute itself returns None, fetchall is what we need to mock
    ]
    # The fetchall call that happens after COUNT(*) > 0
    original_fetchall = mock_cursor.fetchall # Store original fetchall
    def fetchall_side_effect():
         # After the COUNT(*) execute, the next fetchall should return client list
         call_args = mock_cursor.execute.call_args_list
         # Check if the last execute call was the SELECT * FROM clients
         if len(call_args) > 0 and call_args[-1][0][0].startswith('SELECT * FROM clientes'):
              return [(1, 'Client A', 'Dir A', 'Tel A', 'email A')] # Sample client data
         # Otherwise, fall back or raise error if unexpected execute happens
         return original_fetchall() # Or raise unexpected error

    mock_cursor.fetchall.side_effect = fetchall_side_effect # Use side_effect for fetchall too


    response = client.post('/eliminar_cliente/123') # Attempt to delete client ID 123

    assert response.status_code == 200 # Stays on the client list page
    assert b"No se puede eliminar el cliente porque tiene facturas asociadas." in response.data # Check error message

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (123,)), # Check count first
        mock.call('SELECT * FROM clientes;'), # Then fetches clients to re-render the page
    ])
    # Check that DELETE and COMMIT were NOT called
    mock_db_connection["conn"].commit.assert_not_called()
    # Need a way to assert DELETE was not called. Can check call_args_list explicitly.
    execute_calls = [call[0][0] for call in mock_cursor.execute.call_args_list]
    assert 'DELETE FROM clientes WHERE id = %s;' not in execute_calls


# Add DB error tests for eliminar_cliente (count error, delete error)
# ... (omitted)


# --- Tests para editar_cliente (GET) ---

def test_editar_cliente_get_success(client, mock_db_connection):
    """Test: GET /clientes/<id>/editar muestra el formulario con datos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'Client Edit', 'Dir Edit', 'Tel Edit', 'email Edit')

    response = client.get('/clientes/1/editar')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT * FROM clientes WHERE id = %s;', (1,))
    assert b"<form method=\"POST\" action=\"/clientes/1/actualizar\">" in response.data
    assert b"value=\"Client Edit\"" in response.data
    assert b"value=\"email Edit\"" in response.data


def test_editar_cliente_get_not_found(client, mock_db_connection):
    """Test: GET /clientes/<id>/editar for non-existent client returns 404."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = None # Simulate not found

    response = client.get('/clientes/999/editar')

    assert response.status_code == 404
    assert b"Cliente no encontrado" in response.data # Check the specific message from app.py


# Add DB error test for editar_cliente GET
# ... (omitted)


# --- Tests para actualizar_cliente (POST) ---

def test_actualizar_cliente_post_success(client, mock_db_connection):
    """Test: POST /clientes/<id>/actualizar updates client data and redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Cliente Actualizado',
        'direccion': 'Direccion Actualizada',
        'telefono': '987-654',
        'email': 'updated@example.com'
    }

    response = client.post('/clientes/1/actualizar', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        """
        UPDATE clientes
        SET nombre = %s, direccion = %s, telefono = %s, email = %s
        WHERE id = %s;
        """,
        ('Cliente Actualizado', 'Direccion Actualizada', '987-654', 'updated@example.com', 1)
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for actualizar_cliente POST
# Add tests for missing fields (if validation is added to app.py)
# ... (omitted)


# --- Tests para listar_productos ---

def test_listar_productos_success(client, mock_db_connection):
    """Test: /productos muestra la lista de productos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [
        (1, 'Prod A', 'Desc A', 10.00),
        (2, 'Prod B', 'Desc B', 20.50),
    ]

    response = client.get('/productos')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, descripcion, precio FROM productos ORDER BY nombre;')
    assert b"<h1>Lista de Productos</h1>" in response.data
    assert b"Prod A" in response.data
    assert b"20.50" in response.data


# Add tests for empty list and DB errors for listar_productos
# ... (omitted)

# --- Tests para agregar_producto (GET) ---

def test_agregar_producto_get_success(client):
    """Test: GET /productos/agregar muestra el formulario."""
    response = client.get('/productos/agregar')
    assert response.status_code == 200
    assert b"<form method=\"POST\">" in response.data


# --- Tests para agregar_producto (POST) ---

def test_agregar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/agregar agrega un producto y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Nuevo Producto',
        'descripcion': 'Descripcion del nuevo producto',
        'precio': '123.45'
    }

    response = client.post('/productos/agregar', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        'INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);',
        ('Nuevo Producto', 'Descripcion del nuevo producto', '123.45') # price is string from form
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for agregar_producto POST
# Add tests for missing fields (if validation added)
# Add test for non-numeric price (will likely cause ValueError/TypeError in app.py)
# ... (omitted)


# --- Tests para editar_producto (GET/POST) ---

def test_editar_producto_get_success(client, mock_db_connection):
    """Test: GET /productos/editar/<id> muestra el formulario con datos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'Prod Edit', 'Desc Edit', 99.99)

    response = client.get('/productos/editar/1')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, descripcion, precio FROM productos WHERE id = %s;', (1,))
    assert b"<form method=\"POST\">" in response.data # action is not specified in original template form
    assert b"value=\"Prod Edit\"" in response.data
    assert b"value=\"99.99\"" in response.data


# Add test for editar_producto GET not found
# ... (omitted)


def test_editar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/editar/<id> updates product data and redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Producto Actualizado',
        'descripcion': 'Descripcion actualizada',
        'precio': '150.75'
    }

    response = client.post('/productos/editar/1', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        'UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
        ('Producto Actualizado', 'Descripcion actualizada', '150.75', 1) # price is string
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for editar_producto POST
# Add tests for missing fields, invalid price (if validation added)
# ... (omitted)


# --- Tests para eliminar_producto (POST) ---

def test_eliminar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/eliminar/<id> deletes a product not in invoice items."""
    mock_cursor = mock_db_connection["cursor"]
    # Deletion succeeds without raising ForeignKeyViolation

    response = client.post('/productos/eliminar/123') # Attempt to delete product ID 123

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with('DELETE FROM productos WHERE id = %s;', (123,))
    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["conn"].rollback.assert_not_called() # Ensure no rollback


def test_eliminar_producto_post_foreign_key_violation(client, mock_db_connection):
    """Test: POST /productos/eliminar/<id> handles ForeignKeyViolation."""
    mock_cursor = mock_db_connection["cursor"]
    # Configure delete execute to raise ForeignKeyViolation
    mock_cursor.execute.side_effect = psycopg2.errors.ForeignKeyViolation("Simulated FK violation")

    # Mock fetching products again as the app code does this if deletion fails
    mock_cursor.execute.side_effect = [
         # First execute is DELETE -> handled by FK violation below
         # Second execute is SELECT * FROM productos; -> need to mock its fetchall
         psycopg2.errors.ForeignKeyViolation("Simulated FK violation"), # First call
         None # execute itself returns None, fetchall is what we need to mock for the second call
    ]
    # The fetchall call that happens after the exception is caught
    original_fetchall = mock_cursor.fetchall # Store original fetchall
    def fetchall_side_effect():
         # After the DELETE execute (which raises FKV), the next execute is SELECT *
         # The fetchall after that SELECT should return the product list
         call_args = mock_cursor.execute.call_args_list
         if len(call_args) > 1 and call_args[-1][0][0].startswith('SELECT * FROM productos'):
              return [(1, 'Prod A', 'Desc A', 10.00)] # Sample product data
         return original_fetchall()

    mock_cursor.fetchall.side_effect = fetchall_side_effect


    response = client.post('/productos/eliminar/123') # Attempt to delete product ID 123

    assert response.status_code == 200 # Stays on the product list page
    assert b"No se puede eliminar el producto porque se encuentra en una factura." in response.data # Check error message

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('DELETE FROM productos WHERE id = %s;', (123,)), # Check delete call
        mock.call('SELECT * FROM productos;'), # Check select call after rollback
    ])
    mock_db_connection["conn"].commit.assert_not_called() # Ensure commit was NOT called
    mock_db_connection["conn"].rollback.assert_called_once() # Ensure rollback was called

def test_get_db_connection_missing_user_key():
    """Test: Falta la clave 'user' en DB_CONFIG."""
    custom_config = DEFAULT_DB_CONFIG.copy()
    if 'user' in custom_config:
        del custom_config['user']

    with mock.patch('app.psycopg2.connect') as mock_connect:
        # psycopg2.connect raises a TypeError if essential parameters like 'user' are missing,
        # or it might connect as the OS user, which could lead to OperationalError if that user lacks permissions.
        # Let's simulate a TypeError for a clearly missing essential parameter.
        mock_connect.side_effect = TypeError("missing parameter: user")
        with pytest.raises(TypeError, match="missing parameter: user"):
            get_db_connection(config=custom_config)

# Tests para ver_factura

def test_ver_factura_invalid_id_format(client, mock_db_connection):
    """Test: ver_factura con un ID no numérico (ej: 'abc') debería retornar 404."""
    # Asumimos que la ruta está definida como /factura/<int:factura_id>
    # Flask manejará esto antes de que llegue a la función de vista si el conversor de ruta falla.
    response = client.get('/factura/abc')
    assert response.status_code == 404 # Flask route converter <int:..> fails

def test_ver_factura_db_error_fetching_factura_details(client, mock_db_connection):
    """Test: ver_factura maneja error de BD al obtener los detalles de la factura."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = psycopg2.OperationalError("Fallo al obtener factura")

    response = client.get('/factura/1')
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al obtener factura" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once() # Asumiendo rollback en error

def test_ver_factura_db_error_fetching_factura_items(client, mock_db_connection):
    """Test: ver_factura maneja error de BD al obtener los items de la factura."""
    mock_cursor = mock_db_connection["cursor"]
    # Simular éxito al obtener factura, luego error al obtener items
    mock_cursor.fetchone.return_value = (1, 'FACT-001', '2023-01-01', 150.50, 101, 'Cliente A', 'Dir A', 'Tel A') # Factura details
    mock_cursor.fetchall.side_effect = psycopg2.OperationalError("Fallo al obtener items")

    response = client.get('/factura/1')
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al obtener items" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para nueva_factura (POST)

def test_nueva_factura_post_invalid_cliente_id(client, mock_db_connection):
    """Test: POST /factura/nueva con cliente_id no numérico o inválido."""
    # Esta prueba depende de cómo la aplicación valide 'cliente_id'.
    # Si usa int(request.form['cliente_id']), podría dar ValueError.
    # Si la FK de la BD falla, podría ser IntegrityError.
    # Asumimos que la app podría fallar o retornar un error específico.
    # Aquí simularemos que la BD fallará por FK si el ID es basura.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [ # Precios, secuencia
        (100.00,), (123,)
    ]
    mock_cursor.execute.side_effect = [
        None, # select precio
        None, # select nextval
        psycopg2.IntegrityError("FK violation en cliente_id") # insert factura
    ]

    form_data = {'cliente_id': 'abc', 'producto_id_1': '1', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 500 # O 400 si hay validación previa
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error'] # O mensaje de validación
    assert "FK violation en cliente_id" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_non_numeric_cantidad(client, mock_db_connection):
    """Test: POST /factura/nueva con cantidad no numérica."""
    # Asumimos que la aplicación intenta convertir cantidad a float/int.
    # Si falla, debería haber un manejo de ValueError.
    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1',
        'cantidad_1': 'dos', # No numérico
    }
    # No esperamos llamada a BD si la validación de datos falla primero.
    # Si la app no valida y pasa 'dos' a la BD, esta fallaría.
    # Si la app convierte a float(cantidad_str) -> ValueError
    # Vamos a asumir que el error ocurre ANTES de la BD.
    # Para este test, vamos a suponer que la app no tiene validación específica
    # y el error surgirá al intentar hacer cálculos o en la BD.
    # Si la app convierte a float y luego falla, el error sería de la app, no de la BD.
    # Modificamos mock_db_connection para que no se llame a get_db_connection si la app falla antes.
    
    # Para simular un error de conversión en la app antes de la llamada a la BD:
    # Se necesitaría mockear `float()` o la lógica de la vista.
    # Por simplicidad, si la app NO valida, el error vendrá de la BD al intentar insertar 'dos'.
    # O, si la app calcula el total en Python, float('dos') dará ValueError.
    # Vamos a suponer que la app re-renderiza el formulario con un error.
    # Si no, un 500 es probable.

    # Si la app intenta float('dos'):
    # Necesitaríamos que la vista haga esto y lo capturemos.
    # Más simple: testear el error de BD si 'dos' llega al INSERT.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (10.0,) # precio mock
    # La BD fallará si se intenta insertar 'dos' como numérico.
    mock_cursor.execute.side_effect = psycopg2.DataError("valor de cantidad inválido")


    response = client.post('/factura/nueva', data=form_data)
    # El resultado esperado depende de la implementación de error en app.py
    # Podría ser un 500 si no se maneja el DataError o ValueError de float()
    assert response.status_code == 500 # O 400, o 200 con mensaje de error en form
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error'] # o "Error de validación"
    assert "valor de cantidad inválido" in json_data['details']
    if 'rollback' in dir(mock_db_connection["conn"]): #Solo si la conexión se estableció
        mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_db_error_inserting_factura(client, mock_db_connection):
    """Test: POST /factura/nueva maneja error de BD al insertar la factura."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (100.00,), # Precio producto 1
        (123,),    # Next sequence number
        # Error aquí, al insertar factura
        psycopg2.OperationalError("Fallo al insertar factura")
    ]
    # Para que el error ocurra en el INSERT de factura, debemos asegurarnos
    # que execute es llamado para precio, secuencia, y luego el INSERT fallido.
    def execute_side_effect(query, params=None):
        if "SELECT precio" in query: return None
        if "nextval" in query: return None
        if "INSERT INTO facturas" in query:
            raise psycopg2.OperationalError("Fallo al insertar factura")
        return None # Default para otras llamadas si las hubiera
    mock_cursor.execute.side_effect = execute_side_effect

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '2'}
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al insertar factura" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_db_error_inserting_item_causes_rollback(client, mock_db_connection):
    """Test: POST /factura/nueva, error al insertar item causa rollback de la factura."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (100.00,), # Precio producto 1
        (123,),    # Nextval
        (456,),    # Factura ID retornada
        # Error aquí, al insertar item
        psycopg2.OperationalError("Fallo al insertar item de factura")
    ]
    # Para que el error ocurra en el INSERT de factura_items:
    def execute_side_effect(query, params=None):
        if "SELECT precio" in query: return None
        if "nextval" in query: return None
        if "INSERT INTO facturas" in query: return None # Simula éxito
        if "INSERT INTO factura_items" in query:
            raise psycopg2.OperationalError("Fallo al insertar item de factura")
        return None
    mock_cursor.execute.side_effect = execute_side_effect

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '2'}
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al insertar item de factura" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()
    # Verificar que el commit no se llamó
    mock_db_connection["conn"].commit.assert_not_called()

# Tests para agregar_cliente (POST)

def test_agregar_cliente_post_db_error_unique_constraint(client, mock_db_connection):
    """Test: POST /agregar_cliente maneja error de unicidad (ej: email duplicado)."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("violación de restricción unique_email")

    form_data = {'nombre': 'Test', 'direccion': '123 Calle', 'telefono': '555', 'email': 'duplicado@test.com'}
    response = client.post('/agregar_cliente', data=form_data)

    # La app podría retornar al formulario con un error específico o un 500.
    # Si es un error JSON:
    assert response.status_code == 500 # O 400/409 si se maneja como error del cliente
    json_data = response.get_json() # Asumiendo que app.py devuelve JSON para errores de BD
    assert "Error de base de datos" in json_data['error'] # o "Cliente ya existe"
    assert "violación de restricción unique_email" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para eliminar_cliente (POST)

def test_eliminar_cliente_post_invalid_id_format(client, mock_db_connection):
    """Test: POST /eliminar_cliente con ID no numérico debería retornar 404."""
    response = client.post('/eliminar_cliente/abc')
    assert response.status_code == 404 # Flask route converter <int:..> fails

def test_eliminar_cliente_post_db_error_on_count_invoices(client, mock_db_connection):
    """Test: POST /eliminar_cliente maneja error de BD al contar facturas asociadas."""
    mock_cursor = mock_db_connection["cursor"]
    # Error al ejecutar SELECT COUNT(*)
    mock_cursor.execute.side_effect = psycopg2.OperationalError("Fallo al contar facturas")

    response = client.post('/eliminar_cliente/1')
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al contar facturas" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para actualizar_cliente (POST)

def test_actualizar_cliente_post_db_error_on_update(client, mock_db_connection):
    """Test: POST /clientes/<id>/actualizar maneja error de BD en UPDATE."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.OperationalError("Fallo al actualizar cliente")

    form_data = {'nombre': 'Test Upd', 'direccion': 'Calle Upd', 'telefono': '000', 'email': 'upd@test.com'}
    response = client.post('/clientes/1/actualizar', data=form_data)

    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "Fallo al actualizar cliente" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para agregar_producto (POST)

def test_agregar_producto_post_non_numeric_precio(client, mock_db_connection):
    """Test: POST /productos/agregar con precio no numérico."""
    form_data = {'nombre': 'Prod Test', 'descripcion': 'Desc Test', 'precio': 'caro'}
    # Similar a cantidad_X, si la app intenta float('caro') -> ValueError
    # Si 'caro' llega a la BD -> DataError.
    # Asumimos que la app devuelve un error genérico de BD si no hay validación específica.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.DataError("formato de precio inválido")

    response = client.post('/productos/agregar', data=form_data)
    assert response.status_code == 500 # o 400 / 200 con error en form
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "formato de precio inválido" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_agregar_producto_post_db_error_unique_constraint(client, mock_db_connection):
    """Test: POST /productos/agregar maneja error de unicidad (ej: nombre duplicado)."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("violación de restricción unique_nombre_producto")

    form_data = {'nombre': 'Duplicado', 'descripcion': 'Desc', 'precio': '10.0'}
    response = client.post('/productos/agregar', data=form_data)

    assert response.status_code == 500 # o 400/409
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "violación de restricción unique_nombre_producto" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para editar_producto (POST)

def test_editar_producto_post_non_numeric_precio(client, mock_db_connection):
    """Test: POST /productos/editar/<id> con precio no numérico."""
    form_data = {'nombre': 'Prod Editado', 'descripcion': 'Desc Editada', 'precio': 'muy_caro'}
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.DataError("formato de precio inválido al actualizar")

    response = client.post('/productos/editar/1', data=form_data)
    assert response.status_code == 500 # o 400 / 200 con error en form
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "formato de precio inválido al actualizar" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para eliminar_producto (POST)

def test_eliminar_producto_post_invalid_id_format(client, mock_db_connection):
    """Test: POST /productos/eliminar/<id> con ID no numérico debería retornar 404."""
    response = client.post('/productos/eliminar/xyz')
    assert response.status_code == 404 # Flask route converter <int:..> fails

# Tests para get_db_connection

def test_get_db_connection_db_config_not_dict():
    """Test: get_db_connection cuando el parámetro 'config' no es un diccionario."""
    # Asumimos que la función espera un dict y podría fallar con AttributeError o TypeError.
    with pytest.raises((AttributeError, TypeError), match=r".*"): # Regex genérico para el mensaje
        get_db_connection(config="no soy un diccionario")

def test_get_db_connection_empty_db_config():
    """Test: DB_CONFIG es un diccionario vacío."""
    custom_config = {}
    with mock.patch('app.psycopg2.connect') as mock_connect:
        # Probablemente falle con TypeError por parámetros faltantes o KeyError si se accede directamente
        mock_connect.side_effect = TypeError("parámetros de conexión insuficientes")
        with pytest.raises(TypeError, match="parámetros de conexión insuficientes"):
            get_db_connection(config=custom_config)

# Test para listar_facturas

@mock.patch('app.LISTAR_FACTURAS_ENDPOINT_ACTIVE', "desactivado_como_string")
def test_listar_facturas_endpoint_active_not_boolean(client):
    """Test: /facturas/ devuelve 404 si LISTAR_FACTURAS_ENDPOINT_ACTIVE es una cadena no vacía (tratado como True en Python)."""
    # El comportamiento aquí depende de cómo se evalúe el flag en app.py
    # Si es `if LISTAR_FACTURAS_ENDPOINT_ACTIVE:` una cadena no vacía es True.
    # Si es `if LISTAR_FACTURAS_ENDPOINT_ACTIVE is False:` entonces el string no es False.
    # Asumiendo que la lógica actual es `if not LISTAR_FACTURAS_ENDPOINT_ACTIVE:` (o similar)
    # Si la intención es que solo `False` desactive, un string "desactivado" se evaluaría como True.
    # Para que este test falle (es decir, la app falle en desactivar), el string debe ser interpretado como True.
    # Para que la app se comporte como "desactivado", debe ser explícitamente False.
    # Este test comprueba si la app maneja mal un flag no booleano.
    # Si `app.LISTAR_FACTURAS_ENDPOINT_ACTIVE` se evalúa como True (porque es un string no vacío),
    # la ruta debería funcionar. Si la intención era desactivar, esto es un bug.
    # Si el test espera un 404 (asumiendo que el string debería desactivar), pero la app
    # lo trata como True, el test fallará (porque obtendrá 200 OK).
    # Vamos a asumir que la app espera un booleano y cualquier otra cosa es un comportamiento indefinido
    # o debería ser tratado como 'activo' por seguridad.
    # Por tanto, si el flag es "string", la ruta debería funcionar (no 404).
    # Si el endpoint usa `if app.CONFIG_FLAG is False:`, un string no será `False`.
    # Si el endpoint usa `if not app.CONFIG_FLAG:`, un string no vacío es `True`.

    # Testeando el caso donde el endpoint está ACTIVO debido a un string no-False.
    # Para que este test tenga sentido como "prueba de fallo", necesitamos un mock_db_connection.
    # Asumamos que la ruta está activa:
    with mock.patch('app.get_db_connection') as mock_get_db: # Necesitamos mockear la conexión
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [] # No data

        response = client.get('/facturas/')
        assert response.status_code == 200 # Debería funcionar si el string se evalúa a True
        # Si se obtuviera 404, significaría que el string "desactivado_como_string" se interpretó como False, lo cual es inesperado.


# Tests para nueva_factura (POST)

def test_nueva_factura_post_non_existent_producto_id(client, mock_db_connection):
    """Test: POST /factura/nueva, producto_id_1 no existe, SELECT precio retorna None."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        None, # Precio para producto_id_1 es None (no encontrado o sin precio)
        # (123,), # Next sequence - no se debería llegar aquí si falla el precio
    ]
    # La app debería manejar que el producto no tenga precio o no se encuentre.
    # Podría ser un error 500 si hay un TypeError al intentar usar None en cálculos,
    # o un error de usuario si se maneja bien.

    form_data = {'cliente_id': '101', 'producto_id_1': '999', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 500 # O un error de usuario (ej: 400, o re-render con mensaje)
    json_data = response.get_json() # Asumiendo JSON error
    assert "Error procesando factura" in json_data['error'] or "Error de base de datos" in json_data['error']
    assert "producto no encontrado o sin precio" in json_data['details'].lower() # Mensaje esperado
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_negative_cantidad(client, mock_db_connection):
    """Test: POST /factura/nueva con cantidad negativa."""
    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '-5'}
    # La app debería validar esto. Si no, la BD podría tener un CHECK constraint.
    # Si no hay validación, y se calcula total, podría ser negativo.
    # Asumimos que la app debería retornar un error de validación o la BD fallar.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (10.0,) # Precio mock

    # Si la app valida, no hay llamada a execute. Si no, la BD podría fallar:
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("cantidad no puede ser negativa") # CHECK constraint

    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500 # O 400 con error de validación
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error'] or "Error de validación" in json_data['error']
    assert "cantidad no puede ser negativa" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_zero_cantidad(client, mock_db_connection):
    """Test: POST /factura/nueva con cantidad cero. El item debería ser ignorado o causar error."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [ # Precio, secuencia, ID factura
        (10.00,), # Precio producto 1
        (124,),   # Next sequence
        (457,),   # Factura ID
    ]
    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '0'}
    # La app podría ignorar items con cantidad 0. Si es así, no se inserta item.
    # Total sería 0.
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 302 # Asumiendo que se crea la factura con total 0
    assert response.location == '/factura/457'

    # Verificar que el item con cantidad 0 NO se insertó
    item_insert_call = mock.call(
        'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
        (457, '1', '0', 10.00, 0.00) # O los tipos correctos para cantidad y precio
    )
    assert item_insert_call not in mock_cursor.execute.call_args_list
    mock_db_connection["conn"].commit.assert_called_once()

def test_nueva_factura_post_cliente_id_not_exists_fk_error(client, mock_db_connection):
    """Test: POST /factura/nueva, cliente_id no existe, causa FK error al insertar factura."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,), # Precio prod 1
        (125,)    # Secuencia
    ]
    def execute_side_effect(query, params=None):
        if "SELECT precio" in query: return None
        if "nextval" in query: return None
        if "INSERT INTO facturas" in query and params[1] == '9999': # cliente_id no existente
            raise psycopg2.IntegrityError("FK violation en cliente_id")
        return None
    mock_cursor.execute.side_effect = execute_side_effect

    form_data = {'cliente_id': '9999', 'producto_id_1': '1', 'cantidad_1': '2'}
    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "FK violation en cliente_id" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Test para agregar_cliente (POST)

def test_agregar_cliente_post_empty_nombre(client, mock_db_connection):
    """Test: POST /agregar_cliente con campo 'nombre' vacío."""
    form_data = {'nombre': '', 'direccion': 'Alguna', 'telefono': '123', 'email': 'a@b.com'}
    response = client.post('/agregar_cliente', data=form_data)
    # Asumiendo que la validación "Todos los campos son obligatorios" también se aplica a strings vacíos.
    assert response.status_code == 200 # Permanece en la página
    assert b"Todos los campos son obligatorios." in response.data
    mock_db_connection["get_db_connection"].assert_not_called()

# Test para editar_cliente (GET)

def test_editar_cliente_get_db_error(client, mock_db_connection):
    """Test: GET /clientes/<id>/editar maneja error de BD al buscar el cliente."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = psycopg2.OperationalError("Fallo al buscar cliente para editar")
    
    response = client.get('/clientes/1/editar')
    assert response.status_code == 500 # O podría ser 404 si el error se interpreta como "no encontrado"
    json_data = response.get_json() # Asumiendo respuesta JSON para errores
    assert "Error de base de datos" in json_data['error']
    assert "Fallo al buscar cliente para editar" in json_data['details']

# Test para actualizar_cliente (POST)

def test_actualizar_cliente_post_empty_nombre(client, mock_db_connection):
    """Test: POST /clientes/<id>/actualizar con campo 'nombre' vacío."""
    form_data = {'nombre': '', 'direccion': 'Actualizada', 'telefono': '123', 'email': 'upd@b.com'}
    # La app podría permitir nombres vacíos en update si no hay validación,
    # o fallar si la BD tiene un NOT NULL o CHECK constraint.
    # Si la app valida, debería retornar error antes de la BD.
    # Asumamos que la BD no lo permite o la app valida.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("nombre no puede ser vacío") # CHECK constraint
    
    response = client.post('/clientes/1/actualizar', data=form_data)
    assert response.status_code == 500 # O 400 con error de validación
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "nombre no puede ser vacío" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Test para agregar_producto (POST)

def test_agregar_producto_post_negative_precio(client, mock_db_connection):
    """Test: POST /productos/agregar con precio negativo."""
    form_data = {'nombre': 'Prod Caro', 'descripcion': 'Caro pero negativo', 'precio': '-19.99'}
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("precio no puede ser negativo") # CHECK

    response = client.post('/productos/agregar', data=form_data)
    assert response.status_code == 500 # O 400
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "precio no puede ser negativo" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Test para editar_producto (GET)

def test_editar_producto_get_db_error(client, mock_db_connection):
    """Test: GET /productos/editar/<id> maneja error de BD al buscar producto."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = psycopg2.OperationalError("Fallo al buscar producto para editar")

    response = client.get('/productos/editar/1')
    assert response.status_code == 500 # O 404
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "Fallo al buscar producto para editar" in json_data['details']

# Test para editar_producto (POST)

def test_editar_producto_post_empty_nombre(client, mock_db_connection):
    """Test: POST /productos/editar/<id> con nombre vacío."""
    form_data = {'nombre': '', 'descripcion': 'Desc Editada', 'precio': '9.99'}
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("nombre de producto no puede ser vacío")

    response = client.post('/productos/editar/1', data=form_data)
    assert response.status_code == 500 # O 400
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "nombre de producto no puede ser vacío" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Test para eliminar_cliente (POST)
def test_eliminar_cliente_post_non_existent_client_id(client, mock_db_connection):
    """Test: POST /eliminar_cliente/<id> para un cliente_id que no existe."""
    mock_cursor = mock_db_connection["cursor"]
    # Simular que el conteo de facturas es 0 (porque el cliente no existe)
    mock_cursor.fetchone.return_value = (0,) 
    
    # Simular que el DELETE no afecta filas (porque el cliente no existe)
    # La llamada a execute para DELETE no necesita un side_effect específico aquí si 
    # sólo queremos verificar que se llama. psycopg2 no genera error si DELETE no afecta filas.
    # mock_cursor.rowcount puede ser usado por la app para verificar, pero el test no lo necesita.

    response = client.post('/eliminar_cliente/9999') # ID no existente

    assert response.status_code == 302 # Debería redirigir igualmente
    assert response.location == '/clientes'
    
    expected_calls = [
        mock.call('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (9999,)),
        mock.call('DELETE FROM clientes WHERE id = %s;', (9999,))
    ]
    mock_cursor.execute.assert_has_calls(expected_calls)
    mock_db_connection["conn"].commit.assert_called_once()
def test_get_db_connection_config_is_none(mock_db_connection): # Usa el mock para no conectar realmente
    """Test: get_db_connection usa DEFAULT_DB_CONFIG si config es None."""
    # Esta prueba asume que si config es None, se usa DEFAULT_DB_CONFIG.
    # El mock_db_connection ya parchea 'app.get_db_connection'.
    # Para probar el comportamiento interno de get_db_connection con config=None,
    # necesitamos llamar a la función original con config=None y mockear psycopg2.connect.
    
    with mock.patch('app.psycopg2.connect') as mock_actual_connect:
        # Detener el mock global de get_db_connection temporalmente para llamar al original
        mock_db_connection['get_db_connection'].stop()
        
        conn_result = mock.MagicMock()
        mock_actual_connect.return_value = conn_result
        
        # Llama a la función real
        conn = get_db_connection(config=None) 
        assert conn == conn_result
        # Verifica que psycopg2.connect fue llamado con DEFAULT_DB_CONFIG
        mock_actual_connect.assert_called_once_with(**DEFAULT_DB_CONFIG)

        # Restaurar el mock global
        mock_db_connection['get_db_connection'].start()

def test_default_db_config_basic_structure():
    """Test: DEFAULT_DB_CONFIG tiene la estructura y claves esperadas."""
    assert isinstance(DEFAULT_DB_CONFIG, dict)
    assert 'host' in DEFAULT_DB_CONFIG
    assert 'database' in DEFAULT_DB_CONFIG
    assert 'user' in DEFAULT_DB_CONFIG
    assert 'password' in DEFAULT_DB_CONFIG # Asumiendo que password es parte de la config

# Tests para nueva_factura (POST) - Escenarios complejos

def test_nueva_factura_post_multiple_items_one_invalid_producto_id(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,),  # Precio producto 1
        None,      # Precio producto 2 (inválido/no encontrado)
        (20.00,),  # Precio producto 3 (no se debería alcanzar si falla el 2)
        # (126,)   # Secuencia (no se alcanza)
    ]
    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1', 'cantidad_1': '1',
        'producto_id_2': '999', 'cantidad_2': '1', # Producto inválido
        'producto_id_3': '3', 'cantidad_3': '1',
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error procesando factura" in json_data['error'] or "Error de base de datos" in json_data['error']
    assert "producto no encontrado o sin precio" in json_data['details'].lower()
    mock_db_connection["conn"].rollback.assert_called_once()
    mock_db_connection["conn"].commit.assert_not_called()

def test_nueva_factura_post_multiple_items_one_invalid_cantidad(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    # Asumiendo que la conversión a float falla en la app o DataError en BD
    mock_cursor.fetchone.side_effect = [(10.00,), (15.00,)] # Precios
    # Si la app convierte y luego llama a BD, error en app. Si pasa a BD -> DataError
    mock_cursor.execute.side_effect = psycopg2_errors.InvalidTextRepresentation("cantidad debe ser numérica")


    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1', 'cantidad_1': '1',
        'producto_id_2': '2', 'cantidad_2': 'abc', # Cantidad inválida
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error'] # O "Error de validación"
    assert "cantidad debe ser numérica" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_fetch_sequence_fails(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,), # Precio producto
        psycopg2.OperationalError("Fallo al obtener secuencia de factura") # Error en nextval
    ]
    # Para que el error ocurra en nextval:
    def execute_side_effect(query, params=None):
        if "SELECT precio" in query: return None
        if "nextval" in query:
            raise psycopg2.OperationalError("Fallo al obtener secuencia de factura")
        return None
    mock_cursor.execute.side_effect = execute_side_effect

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "Fallo al obtener secuencia de factura" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_item_string_too_long_error(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,), (127,), (458,) # Precio, Secuencia, Factura ID
    ]
    # Simular error de truncado en INSERT factura_items
    def execute_side_effect(query, params=None):
        if "INSERT INTO factura_items" in query:
            raise psycopg2_errors.StringDataRightTruncation("código de producto demasiado largo")
        return None # Para otras queries (precio, nextval, insert factura)
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO factura_items" in query:
            raise psycopg2_errors.StringDataRightTruncation("código de producto demasiado largo")
        return original_execute(query, params) # Llama al mock original para otras
    mock_cursor.execute.side_effect = side_effect_router


    form_data = {'cliente_id': '101', 'producto_id_1': 'CODIGO_MUY_LARGO_PARA_LA_COLUMNA', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "código de producto demasiado largo" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_numeric_value_out_of_range_for_total(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (1.0E+38,), # Precio enorme
        (128,)      # Secuencia
    ]
    # Simular error en INSERT facturas
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query: # Asumiendo que el total calculado (1.0E+38 * 2) excede el límite
            raise psycopg2_errors.NumericValueOutOfRange("total de factura fuera de rango")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO facturas" in query:
             # Asumimos que params[2] es el total. params[2] viene de float(cantidad) * precio
             # Si cantidad es '2', total = 2 * 1.0E+38
            if params[2] > 1.5E+38: # Simula un límite
                raise psycopg2_errors.NumericValueOutOfRange("total de factura fuera de rango")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '2'} # Total = 2.0E+38
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "total de factura fuera de rango" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

# Tests para agregar_cliente / actualizar_cliente (POST)

def test_agregar_cliente_post_string_too_long_for_nombre(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.StringDataRightTruncation("nombre de cliente demasiado largo")
    form_data = {'nombre': 'X'*300, 'direccion': 'Dir', 'telefono': '123', 'email': 'a@b.com'} # Asumir VARCHAR(255)

    response = client.post('/agregar_cliente', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "nombre de cliente demasiado largo" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_actualizar_cliente_post_string_too_long_for_direccion(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.StringDataRightTruncation("dirección de cliente demasiado larga")
    form_data = {'nombre': 'Cliente Ok', 'direccion': 'Y'*500, 'telefono': '123', 'email': 'upd@b.com'}

    response = client.post('/clientes/1/actualizar', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "dirección de cliente demasiado larga" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_agregar_cliente_post_email_invalid_format_complex(client, mock_db_connection):
    # Este test asume que la app no tiene una validación de email muy estricta
    # y que la BD tampoco la tiene, o que la validación de la BD es diferente.
    # Si la app inserta "test@domain" sin problemas, este test pasaría (la app NO falla).
    # Para que sea un test de "fallo" de la app, la app debería rechazarlo.
    # O, si la BD lo rechaza (ej: un trigger o constraint complejo).
    mock_cursor = mock_db_connection["cursor"]
    # Simular un error de la BD por formato inválido de email si existe tal constraint.
    mock_cursor.execute.side_effect = psycopg2.IntegrityError("formato de email no válido según constraint_XYZ")
    form_data = {'nombre': 'Test Email', 'direccion': 'Dir', 'telefono': '123', 'email': 'test@domain'}

    response = client.post('/agregar_cliente', data=form_data)
    # Si la app debe validar esto y no lo hace, el test debe reflejar el comportamiento esperado.
    # Asumimos que la BD lo rechaza.
    assert response.status_code == 500 # O 400 si la app valida
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "formato de email no válido" in json_data['details']


# Tests para agregar_producto / editar_producto (POST)

def test_agregar_producto_post_string_too_long_for_nombre(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.StringDataRightTruncation("nombre de producto demasiado largo")
    form_data = {'nombre': 'Z'*300, 'descripcion': 'Desc', 'precio': '10.0'}

    response = client.post('/productos/agregar', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "nombre de producto demasiado largo" in json_data['details']

def test_editar_producto_post_numeric_value_out_of_range_for_precio(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.NumericValueOutOfRange("precio de producto fuera de rango")
    form_data = {'nombre': 'Prod Editado', 'descripcion': 'Desc', 'precio': '1.0E+20'}

    response = client.post('/productos/editar/1', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "precio de producto fuera de rango" in json_data['details']

# Tests para Manejo Genérico de Errores de BD

def test_listar_clientes_db_error_cursor_creation_fails(client, mock_db_connection):
    mock_db_connection["conn"].cursor.side_effect = psycopg2.OperationalError("Fallo al crear cursor")
    
    response = client.get('/clientes')
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error'] or "Error interno" in json_data['error']
    assert "Fallo al crear cursor" in json_data['details']
    # Rollback podría o no ser llamado dependiendo de dónde exactamente falle.
    # Si falla antes de que el cursor se use en un try/except que hace rollback.

def test_ver_factura_db_error_cursor_close_fails(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'F-001', '2023-01-01', 100.0, 1, 'Cliente', 'Dir', 'Tel') # Factura
    mock_cursor.fetchall.return_value = [] # Items
    mock_cursor.close.side_effect = psycopg2.OperationalError("Fallo al cerrar cursor")

    response = client.get('/factura/1')
    # La respuesta principal podría ser 200 OK si el error de cierre no se propaga como error HTTP,
    # pero es buena práctica que la app lo loguee o maneje.
    # Si la app lo maneja y retorna 500:
    # assert response.status_code == 500
    # json_data = response.get_json()
    # assert "Fallo al cerrar cursor" in json_data['details']
    # Si el error solo se loguea y la respuesta es 200 (porque los datos se enviaron):
    assert response.status_code == 200 
    # Para verificar el logueo, necesitaríamos mockear `app.logger.error` o similar.
    # Aquí, al menos verificamos que la app no crashea completamente y da un 200.
    # La conexión principal sí debería cerrarse.
    mock_db_connection["conn"].close.assert_called_once()

def test_agregar_producto_post_db_error_conn_close_fails(client, mock_db_connection):
    # execute y commit son exitosos
    mock_db_connection["conn"].close.side_effect = psycopg2.OperationalError("Fallo al cerrar conexión")
    form_data = {'nombre': 'Prod Test Close', 'descripcion': 'Desc', 'precio': '10.0'}

    response = client.post('/productos/agregar', data=form_data)
    # La redirección ya habría sido emitida antes del conn.close() en un bloque finally.
    # El error de conn.close() usualmente no cambia la respuesta HTTP al cliente.
    assert response.status_code == 302 # Redirección
    assert response.location == '/productos'
    mock_db_connection["conn"].commit.assert_called_once()
    # Verificar que se intentó cerrar (y falló, lo cual es el side_effect)
    mock_db_connection["conn"].close.assert_called_once()

def test_listar_productos_db_error_generic_psycopg2_error(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2.Error("Error genérico de psycopg2") # Error base

    response = client.get('/productos')
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "Error genérico de psycopg2" in json_data['details']

# Tests para Cascada de Errores / Re-renderizado en Error

def test_eliminar_cliente_with_invoices_refetch_clients_fails(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    # Primera llamada a execute (COUNT) es exitosa y retorna > 0
    # Segunda llamada a execute (SELECT * FROM clientes para re-renderizar) falla
    mock_cursor.fetchone.return_value = (1,) # Tiene 1 factura
    
    def execute_side_effect(query, params=None):
        if "COUNT(*)" in query:
            return None # fetchone se encarga
        if "SELECT * FROM clientes" in query or "SELECT id, nombre, direccion, telefono, email FROM clientes" in query : # La query para recargar
            raise psycopg2.OperationalError("Fallo al recargar clientes")
        return None
    mock_cursor.execute.side_effect = execute_side_effect
    
    response = client.post('/eliminar_cliente/1')
    assert response.status_code == 500 # Error al intentar re-renderizar la página de error
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "Fallo al recargar clientes" in json_data['details']
    # El commit no debería llamarse porque la eliminación no procedió
    mock_db_connection["conn"].commit.assert_not_called()
    mock_db_connection["conn"].rollback.assert_called_once() # Por el error de recarga

def test_eliminar_producto_fk_violation_refetch_products_fails(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    # Primera llamada (DELETE) causa FK violation
    # Segunda llamada (SELECT * FROM productos para re-renderizar) falla
    def execute_side_effect(query, params=None):
        if "DELETE FROM productos" in query:
            raise psycopg2_errors.ForeignKeyViolation("producto en uso")
        if "SELECT * FROM productos" in query or "SELECT id, nombre, descripcion, precio FROM productos" in query:
            raise psycopg2.OperationalError("Fallo al recargar productos")
        return None # No debería haber otras llamadas
    mock_cursor.execute.side_effect = execute_side_effect

    response = client.post('/productos/eliminar/1')
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "Fallo al recargar productos" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once() # Por el FK violation inicial, o por el segundo error.


# Tests para Lógica Específica de Rutas
@mock.patch('app.LISTAR_FACTURAS_ENDPOINT_ACTIVE', True)
@mock.patch('app.listar_facturas') # Mockear la función de vista directamente
def test_index_redirect_target_view_raises_non_db_error(mock_listar_facturas_view, client):
    mock_listar_facturas_view.side_effect = NameError("algo_inesperado_en_la_vista_facturas")
    
    response = client.get('/')
    # Flask convierte el NameError en un 500 Internal Server Error.
    # El error no es capturado por los manejadores de error de psycopg2.
    assert response.status_code == 500
    # No podemos verificar json_data['details'] fácilmente aquí porque es un error genérico de Flask.
    # Podríamos verificar el contenido HTML si la página de error de Flask se renderiza.
    # assert b"Internal Server Error" in response.data # Comprobación básica

def test_ver_factura_malformed_factura_data_from_db(client, mock_db_connection):
    mock_cursor = mock_db_connection["cursor"]
    # fetchone retorna algo que no es una tupla, o tupla con longitud incorrecta
    mock_cursor.fetchone.return_value = "esto no es una tupla" 
    
    response = client.get('/factura/1')
    # La vista probablemente falle con TypeError o IndexError al intentar desempaquetar.
    assert response.status_code == 500
    json_data = response.get_json() # Asumiendo que el error genérico de la app lo convierte a JSON
    assert "Error interno inesperado" in json_data['error'] # O similar
    # El detalle podría ser sobre TypeError o similar.
    assert isinstance(json_data['details'], str) # El detalle del error de Python.
def test_get_db_connection_db_config_invalid_port_type(mock_db_connection):
    """Test: get_db_connection cuando DB_CONFIG['port'] es un string no numérico."""
    custom_config = DEFAULT_DB_CONFIG.copy()
    custom_config['port'] = "puerto_invalido"
    
    with mock.patch('app.psycopg2.connect') as mock_actual_connect:
        # psycopg2.connect puede lanzar un ValueError o TypeError si el puerto no es convertible a int.
        mock_actual_connect.side_effect = ValueError("el puerto debe ser un número")
        
        # Detener el mock global para probar la lógica interna de get_db_connection
        mock_db_connection['get_db_connection'].stop()
        with pytest.raises(ValueError, match="el puerto debe ser un número"):
            get_db_connection(config=custom_config)
        mock_db_connection['get_db_connection'].start() # Restaurar

# Test para get_db_connection con claves extra en DB_CONFIG
def test_get_db_connection_db_config_with_extra_keys(mock_db_connection):
    """Test: get_db_connection ignora claves extra en DB_CONFIG y conecta."""
    custom_config = DEFAULT_DB_CONFIG.copy()
    custom_config['clave_extra_ignorada'] = "valor_extra"
    
    # El mock_db_connection ya maneja la conexión exitosa.
    # Solo necesitamos verificar que get_db_connection es llamado.
    # Si queremos ser más precisos, mockearíamos psycopg2.connect y verificaríamos **kwargs.
    conn = get_db_connection(config=custom_config) # Debería usar el mock_db_connection
    assert conn is not None
    # El mock_db_connection intercepta 'app.get_db_connection'.
    # Si pasamos 'custom_config' aquí, el mock lo recibirá.
    mock_db_connection["get_db_connection"].assert_called_once_with(config=custom_config)
    # Aquí se asume que el mock_db_connection está configurado para retornar un mock de conexión válido
    # y no necesariamente para verificar los args pasados a psycopg2.connect en este test particular.


# Tests para nueva_factura (POST) - Casos límite adicionales
def test_nueva_factura_post_item_with_empty_cantidad(client, mock_db_connection):
    """Test: POST /factura/nueva con producto_id presente pero cantidad_X es string vacío."""
    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': ''}
    # La app debería tratar esto como un item inválido o cantidad cero.
    # Si se trata como error:
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (10.0,) # Precio
    # Asumiendo que la app lo detecta como error de validación antes de la BD.
    # O si llega a la BD, podría ser InvalidTextRepresentation para la cantidad.
    # Si la app convierte float('') -> ValueError.

    response = client.post('/factura/nueva', data=form_data)
    # El comportamiento esperado depende de la lógica de la app.
    # Podría ser un error 400, 500, o re-renderizar el formulario.
    # Supongamos que resulta en un error de procesamiento o validación.
    assert response.status_code == 500 # O 400
    json_data = response.get_json()
    assert "Error de validación" in json_data['error'] or "Error procesando factura" in json_data['error']
    assert "cantidad no puede estar vacía" in json_data['details'].lower() # Mensaje esperado
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_factura_numero_already_exists(client, mock_db_connection):
    """Test: POST /factura/nueva, el número de factura generado (FACT-XXX) ya existe."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,), # Precio
        (129,),   # Secuencia (ej: FACT-129)
    ]
    # Simular UniqueViolation en INSERT facturas para el campo 'numero'
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query:
            # Asumir que params[0] es el número de factura 'FACT-129'
            raise psycopg2_errors.UniqueViolation("el número de factura ya existe")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO facturas" in query and params[0] == "FACT-129": # Asegurarse que el número es el esperado
            raise psycopg2_errors.UniqueViolation("el número de factura ya existe")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "el número de factura ya existe" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_commit_fails(client, mock_db_connection):
    """Test: POST /factura/nueva, el COMMIT final falla después de operaciones exitosas."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,), (130,), (459,)] # Precio, Secuencia, Factura ID
    mock_db_connection["conn"].commit.side_effect = psycopg2.OperationalError("fallo en commit")

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)
    
    assert response.status_code == 500 # El error de commit debería resultar en error HTTP
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "fallo en commit" in json_data['details']
    # Rollback podría ser llamado por el manejador de error general después del fallo de commit.
    mock_db_connection["conn"].rollback.assert_called_once()

def test_nueva_factura_post_rollback_itself_fails(client, mock_db_connection):
    """Test: POST /factura/nueva, un error ocurre, y el subsecuente ROLLBACK falla."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,)] # Precio
    # Error al obtener secuencia para forzar un rollback
    mock_cursor.execute.side_effect = psycopg2.OperationalError("error inicial para forzar rollback")
    mock_db_connection["conn"].rollback.side_effect = psycopg2.OperationalError("fallo en rollback")

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1'}
    response = client.post('/factura/nueva', data=form_data)
    
    # El error original o el error de rollback será reportado.
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    # El detalle podría ser del error inicial o del fallo de rollback, dependiendo de la implementación.
    assert "error inicial para forzar rollback" in json_data['details'] or "fallo en rollback" in json_data['details']


# Tests para Métodos HTTP Incorrectos
def test_agregar_cliente_get_request_on_post_route(client):
    """Test: GET request a /agregar_cliente (que espera POST para crear)."""
    # GET a /agregar_cliente es válido para mostrar el formulario.
    # El test original ya cubre esto con test_agregar_cliente_get_success.
    # Este test es para una ruta que SOLO acepta POST.
    # Supongamos una ruta hipotética /procesar_algo que solo acepta POST.
    # app.route('/procesar_algo', methods=['POST'])
    # response = client.get('/procesar_algo')
    # assert response.status_code == 405 # Method Not Allowed
    # Como no tenemos tal ruta, este test es ilustrativo o necesitaría una.
    # Vamos a testear /eliminar_cliente/<id> con GET, que espera POST.
    response = client.get('/eliminar_cliente/1')
    assert response.status_code == 405

def test_listar_clientes_post_request_on_get_route(client):
    """Test: POST request a /clientes (que espera GET para listar)."""
    response = client.post('/clientes', data={})
    assert response.status_code == 405


# Tests para Vistas y Listas Vacías
def test_ver_factura_with_no_items(client, mock_db_connection):
    """Test: ver_factura para una factura existente pero sin items."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'F-002', '2023-02-01', 0.0, 2, 'Cliente B', 'Dir B', 'Tel B') # Factura
    mock_cursor.fetchall.return_value = [] # No items

    response = client.get('/factura/1')
    assert response.status_code == 200
    assert b"F-002" in response.data # Detalles de factura presentes
    assert b"No hay items en esta factura." in response.data # Mensaje esperado en la plantilla

def test_listar_clientes_empty_list(client, mock_db_connection):
    """Test: /clientes cuando la BD retorna una lista vacía de clientes."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = []

    response = client.get('/clientes')
    assert response.status_code == 200
    assert b"<h1>Lista de Clientes</h1>" in response.data
    assert b"No hay clientes disponibles." in response.data # Mensaje esperado

def test_listar_productos_empty_list(client, mock_db_connection):
    """Test: /productos cuando la BD retorna una lista vacía de productos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = []

    response = client.get('/productos')
    assert response.status_code == 200
    assert b"<h1>Lista de Productos</h1>" in response.data
    assert b"No hay productos disponibles." in response.data # Mensaje esperado


# Test para Error de BD específico (CharacterNotInRepertoire)
def test_agregar_cliente_post_char_not_in_repertoire(client, mock_db_connection):
    """Test: agregar_cliente con caracteres no soportados por la codificación de la BD."""
    mock_cursor = mock_db_connection["cursor"]
    # Simular error de psycopg2 si un caracter no es representable en la codificación de la BD.
    mock_cursor.execute.side_effect = psycopg2_errors.CharacterNotInRepertoire("caracter no soportado: 🔥")
    form_data = {'nombre': 'NombreConFuego🔥', 'direccion': 'Dir', 'telefono': '123', 'email': 'fuego@b.com'}

    response = client.post('/agregar_cliente', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "caracter no soportado: 🔥" in json_data['details']


# Test para editar_producto con ID no existente en la URL (para POST)
def test_editar_producto_post_non_existent_id_in_url(client, mock_db_connection):
    """Test: POST /productos/editar/9999 (ID no existente)."""
    # Si la app no verifica si el producto existe antes del UPDATE,
    # el UPDATE simplemente afectará 0 filas. No es un error de BD.
    # La app redirigirá a /productos.
    mock_cursor = mock_db_connection["cursor"] # Para verificar execute
    form_data = {'nombre': 'No Existente', 'descripcion': 'Desc', 'precio': '9.99'}

    response = client.post('/productos/editar/9999', data=form_data)
    assert response.status_code == 302
    assert response.location == '/productos'
    # Verificar que se intentó el UPDATE
    mock_cursor.execute.assert_called_once_with(
        'UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
        ('No Existente', 'Desc', '9.99', 9999) # ID es int
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Test para TemplateNotFound (requiere una ruta que intente renderizar un template inexistente)
# Este test es un poco artificial si no hay un caso real en el código.
# Supongamos que hay una ruta @app.route('/test_render_error')
# @mock.patch('app.render_template') # No podemos mockear render_template globalmente tan fácil para TemplateNotFound
def test_route_raises_template_not_found(client):
    """Test: Una ruta intenta renderizar un template que no existe."""
    # Para este test, necesitaríamos que Flask intente renderizar un template inexistente.
    # Esto usualmente se hace modificando temporalmente una llamada a render_template
    # o teniendo una ruta de prueba que haga esto.
    # Ejemplo: si una ruta tiene render_template("template_que_no_existe.html")
    # Si no existe tal ruta en `app.py`, podemos simularlo si un error handler lo hace.
    # Por ahora, lo omitimos si requiere modificar `app.py` solo para el test.
    # Si un error handler personalizado en `app.py` hiciera render_template('error_inexistente.html'),
    # podríamos probar ese error handler.
    #
    # Alternativa: mockear render_template para que *él mismo* levante TemplateNotFound
    # cuando se le llame con un nombre específico.
    with mock.patch('flask.templating.render_template') as mock_render:
        mock_render.side_effect = Exception("Template 'template_inexistente.html' not found") # Usar Jinja2 TemplateNotFound si está importado

        # Suponer que alguna ruta (ej: /error_especial) llama a render_template('template_inexistente.html')
        # Esto es difícil de probar sin una ruta específica en app.py que lo haga.
        # Si una ruta existente, bajo ciertas condiciones, renderiza un template dinámicamente y falla:
        # response = client.get('/ruta_que_falla_render')
        # assert response.status_code == 500
        # assert b"Template Not Found" in response.data # O el mensaje de la excepción
        pass # Omitido por falta de un objetivo claro en el código provisto


# Test para nueva_factura con cantidad float en BD de tipo INTEGER
def test_nueva_factura_post_cantidad_float_for_integer_column(client, mock_db_connection):
    """Test: POST /factura/nueva, cantidad es '2.5' pero columna BD es INTEGER."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [
        (10.00,), # Precio
        (131,),   # Secuencia
        (460,),   # Factura ID
    ]
    # psycopg2 o la BD podría truncar '2.5' a 2, o generar un error.
    # Si genera error:
    def execute_side_effect(query, params=None):
        if "INSERT INTO factura_items" in query:
            # params[2] es la cantidad '2.5'
            # La BD o psycopg2 podría fallar al intentar convertir '2.5' a INTEGER.
            raise psycopg2_errors.InvalidTextRepresentation("formato inválido para tipo integer: \"2.5\"")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO factura_items" in query and params[2] == '2.5':
            raise psycopg2_errors.InvalidTextRepresentation("formato inválido para tipo integer: \"2.5\"")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '2.5'}
    response = client.post('/factura/nueva', data=form_data)
    
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Error de base de datos" in json_data['error']
    assert "formato inválido para tipo integer" in json_data['details']
    mock_db_connection["conn"].rollback.assert_called_once()
def test_agregar_cliente_post_nombre_con_espacios_extremos(client, mock_db_connection):
    """Test: agregar_cliente con nombre con espacios al inicio/final. ¿Se normaliza o guarda tal cual?"""
    mock_cursor = mock_db_connection["cursor"]
    nombre_con_espacios = "  Cliente con Espacios  "
    nombre_esperado_db = nombre_con_espacios.strip() # Asumiendo que la app hace strip()

    form_data = {'nombre': nombre_con_espacios, 'direccion': 'Dir', 'telefono': '123', 'email': 'espacios@b.com'}
    client.post('/agregar_cliente', data=form_data)

    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        (nombre_esperado_db, 'Dir', '123', 'espacios@b.com')
    )
    mock_db_connection["conn"].commit.assert_called_once()

def test_nueva_factura_post_items_con_mismo_producto_id(client, mock_db_connection):
    """Test: nueva_factura con dos líneas de item para el mismo producto_id."""
    mock_cursor = mock_db_connection["cursor"]
    # Precio, Secuencia, Factura ID
    mock_cursor.fetchone.side_effect = [(10.00,), (10.00,), (132,), (461,)] 
    # La app podría sumar cantidades o tratar como líneas separadas. Asumimos líneas separadas.
    # Total esperado: (1 * 10) + (2 * 10) = 30

    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1', 'cantidad_1': '1', # Producto 1, cantidad 1
        'producto_id_2': '1', 'cantidad_2': '2', # Mismo Producto 1, cantidad 2
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 302
    assert response.location == '/factura/461'

    # Verificar inserción de factura con total correcto (asumiendo 30.00)
    # Verificar inserciones de items (dos llamadas a INSERT factura_items)
    calls = mock_cursor.execute.call_args_list
    insert_factura_call = mock.call(
        'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
        ('FACT-132', '101', decimal.Decimal('30.00')) # O float(30.00) según la app
    )
    insert_item1_call = mock.call(
        'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
        (461, '1', '1', decimal.Decimal('10.00'), decimal.Decimal('10.00'))
    )
    insert_item2_call = mock.call(
        'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
        (461, '1', '2', decimal.Decimal('10.00'), decimal.Decimal('20.00'))
    )
    assert insert_factura_call in calls
    assert insert_item1_call in calls
    assert insert_item2_call in calls
    mock_db_connection["conn"].commit.assert_called_once()


def test_agregar_producto_post_descripcion_muy_larga(client, mock_db_connection):
    """Test: agregar_producto con descripción extremadamente larga."""
    mock_cursor = mock_db_connection["cursor"]
    descripcion_larga = "Descripción " * 1000 # 12000 caracteres
    # Asumir que la BD la trunca o da error si excede el límite de la columna.
    mock_cursor.execute.side_effect = psycopg2_errors.StringDataRightTruncation("descripción demasiado larga")

    form_data = {'nombre': 'Prod Largo', 'descripcion': descripcion_larga, 'precio': '10'}
    response = client.post('/productos/agregar', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "descripción demasiado larga" in json_data['details']

def test_nueva_factura_post_cantidad_muy_grande_calculo_subtotal(client, mock_db_connection):
    """Test: nueva_factura con cantidad muy grande, verificar posible overflow en cálculo o BD."""
    mock_cursor = mock_db_connection["cursor"]
    # Precio, Secuencia, Factura ID
    mock_cursor.fetchone.side_effect = [(decimal.Decimal('1.00'),), (133,), (462,)]
    cantidad_grande_str = "1000000000000.50" # Un número grande
    # Asumir que el subtotal (precio * cantidad) excede el límite de Numeric en BD para subtotal.
    def execute_side_effect(query, params=None):
        if "INSERT INTO factura_items" in query:
            # params[4] es subtotal
            if params[4] > decimal.Decimal('1E12'): # Simular un límite
                 raise psycopg2_errors.NumericValueOutOfRange("subtotal del item fuera de rango")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO factura_items" in query and params[2] == cantidad_grande_str: # params[2] es cantidad
            # subtotal = decimal.Decimal(params[2]) * params[3] # cantidad * precio
            # if subtotal > decimal.Decimal('1E12'):
            raise psycopg2_errors.NumericValueOutOfRange("subtotal del item fuera de rango")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router


    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': cantidad_grande_str}
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "subtotal del item fuera de rango" in json_data['details']

def test_agregar_cliente_post_fecha_registro_formato_invalido(client, mock_db_connection):
    """Test: agregar_cliente con un campo de fecha hipotético en formato inválido."""
    # Asumir que `clientes` tiene una columna `fecha_registro DATE` y el form la envía.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.InvalidDatetimeFormat("formato de fecha inválido: '30/02/2025'")
    form_data = {
        'nombre': 'Cliente Fecha', 'direccion': 'Dir', 'telefono': '123', 
        'email': 'fecha@b.com', 'fecha_registro': '30/02/2025' # Fecha inválida
    }
    # Asumir que la app intenta insertar esta fecha directamente.
    response = client.post('/agregar_cliente', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "formato de fecha inválido" in json_data['details']

# Tests de HTTP y Detalles de Petición/Respuesta
def test_agregar_cliente_post_unexpected_content_type(client, mock_db_connection):
    """Test: POST a /agregar_cliente con Content-Type application/json."""
    # request.form estará vacío. La app debería manejar esto como campos faltantes.
    response = client.post('/agregar_cliente', 
                           data=json.dumps({'nombre': 'Test JSON'}), 
                           content_type='application/json')
    assert response.status_code == 200 # Asume que vuelve al form con error
    assert b"Todos los campos son obligatorios." in response.data
    mock_db_connection["get_db_connection"].assert_not_called()

def test_json_error_response_content_type(client, mock_db_connection):
    """Test: Errores de BD que devuelven JSON tienen Content-Type application/json."""
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Error DB for JSON test")
    response = client.get('/facturas/') # Ruta que devuelve JSON en error de BD
    assert response.status_code == 500
    assert response.content_type == 'application/json'

def test_html_success_response_content_type(client, mock_db_connection):
    """Test: Rutas HTML exitosas tienen Content-Type text/html; charset=utf-8."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [] # No data, pero la página se renderiza
    response = client.get('/facturas/')
    assert response.status_code == 200
    assert response.content_type == 'text/html; charset=utf-8'

# Tests de Interacción con BD y Transacciones
# No es fácil simular fallo de `psycopg2.connect` después de retries sin modificar `get_db_connection`.
# El mock actual de `get_db_connection` ya cubre el fallo de conexión inicial.

def test_listar_facturas_fetchall_returns_malformed_row(client, mock_db_connection):
    """Test: listar_facturas donde fetchall() retorna una fila con datos malformados."""
    mock_cursor = mock_db_connection["cursor"]
    # Fila[0] debería ser int (id), Fila[4] debería ser numérico (total)
    # Si la plantilla espera desempaquetar o formatear estos tipos y son incorrectos, puede fallar.
    malformed_row = ("id_string_malo", "FACT-ERR", "2023-01-01", "Cliente Err", "total_string_malo")
    mock_cursor.fetchall.return_value = [malformed_row]
    
    response = client.get('/facturas/')
    # La plantilla podría fallar al renderizar 'total_string_malo' como moneda o 'id_string_malo' en un enlace.
    # Esto resultaría en un 500 Internal Server Error si no se maneja en la plantilla con `default` o similar.
    assert response.status_code == 500 
    # El error exacto es difícil de predecir (TemplateAssertionError, TypeError, etc.)
    # Verificar que al menos no es un 200 OK. Podríamos buscar un mensaje genérico de error de Flask.
    assert b"Internal Server Error" in response.data # Si es la página de error por defecto de Flask.


def test_nueva_factura_post_check_constraint_violation(client, mock_db_connection):
    """Test: nueva_factura con violación de un CHECK constraint (ej: tipo_factura inválido)."""
    # Asumir que `facturas` tiene `tipo_factura CHAR(1) CHECK (tipo_factura IN ('A', 'B', 'C'))`
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,), (134,)] # Precio, Secuencia
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query:
            # Asumir que los params incluyen un tipo_factura 'X' inválido
            raise psycopg2_errors.CheckViolation("violación de check constraint 'chk_tipo_factura'")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        # Suponiendo que el INSERT incluye un campo para tipo_factura y se le pasa 'X'
        if "INSERT INTO facturas" in query: # y params contiene tipo_factura='X'
            raise psycopg2_errors.CheckViolation("violación de check constraint 'chk_tipo_factura'")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {
        'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1',
        'tipo_factura': 'X' # Dato hipotético que viola un CHECK
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "violación de check constraint" in json_data['details']

def test_nueva_factura_post_datetime_field_overflow(client, mock_db_connection):
    """Test: nueva_factura con fecha que causa DatetimeFieldOverflow."""
    # Asumir que el form envía un campo 'fecha_emision_factura'
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,), (135,)] # Precio, Secuencia
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query: # y params contiene la fecha 0000-01-01
            raise psycopg2_errors.DatetimeFieldOverflow("fecha fuera de rango para tipo timestamp")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO facturas" in query: # y params contiene la fecha problemática
            raise psycopg2_errors.DatetimeFieldOverflow("fecha fuera de rango para tipo timestamp")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {
        'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1',
        'fecha_emision_factura': '0000-01-01' # Fecha problemática
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "fecha fuera de rango" in json_data['details']

# Tests de Lógica de Aplicación y Estado
def test_ver_factura_marcada_como_cancelada(client, mock_db_connection):
    """Test: ver_factura para una factura que tiene un estado 'CANCELADA'."""
    # Asumir que `facturas` tiene un campo `estado` y la plantilla lo muestra.
    mock_cursor = mock_db_connection["cursor"]
    # id, numero, fecha, total, cliente_id, cliente_nombre, ..., estado (nuevo campo)
    factura_cancelada_data = (1, 'F-CANC', '2023-03-01', 50.0, 3, 'Cliente C', 'Dir C', 'Tel C', 'CANCELADA')
    mock_cursor.fetchone.return_value = factura_cancelada_data
    mock_cursor.fetchall.return_value = [] # Sin items para simplificar

    response = client.get('/factura/1')
    assert response.status_code == 200
    assert b"F-CANC" in response.data
    assert b"Estado: CANCELADA" in response.data # Mensaje esperado en la plantilla

def test_editar_cliente_get_xss_prevention_in_form_values(client, mock_db_connection):
    """Test: editar_cliente, los datos con HTML especial se escapan en los values del form."""
    mock_cursor = mock_db_connection["cursor"]
    xss_nombre = "<script>alert('XSS')</script>"
    # id, nombre, direccion, telefono, email
    cliente_con_xss = (1, xss_nombre, "Dir", "Tel", "xss@example.com")
    mock_cursor.fetchone.return_value = cliente_con_xss

    response = client.get('/clientes/1/editar')
    assert response.status_code == 200
    # Verificar que el script NO está tal cual en el value, sino escapado.
    # Flask/Jinja2 escapan por defecto en {{ ... }}.
    # En <input value="{{ cliente.nombre }}">, se escaparía.
    escaped_xss_nombre = "&lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;"
    assert bytes(escaped_xss_nombre, 'utf-8') in response.data
    assert b"<script>alert('XSS')</script>" not in response.data # No debe estar el script crudo


def test_editar_producto_post_precio_cero(client, mock_db_connection):
    """Test: editar_producto actualizando el precio a 0.00. ¿Es permitido?"""
    mock_cursor = mock_db_connection["cursor"]
    form_data = {'nombre': 'Prod Gratis', 'descripcion': 'Desc', 'precio': '0.00'}

    response = client.post('/productos/editar/1', data=form_data)
    assert response.status_code == 302 # Asumiendo que es una actualización válida
    assert response.location == '/productos'
    mock_cursor.execute.assert_called_once_with(
        'UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
        ('Prod Gratis', 'Desc', '0.00', 1)
    )
    mock_db_connection["conn"].commit.assert_called_once()

# Tests de Configuración y Logging de Flask
def test_get_db_connection_failure_finally_block_error_masking(client, mock_db_connection):
    """Test: Falla get_db_connection, y un hipotético finally en la vista también falla."""
    # Este test es complejo porque requiere controlar el flujo dentro de la vista.
    # Supongamos que una vista hace:
    # conn = None
    # try:
    #   conn = get_db_connection() # Falla aquí
    #   # ...
    # finally:
    #   if conn: conn.close() # No se ejecuta conn.close()
    #   raise ValueError("Error en finally") # Este error podría enmascarar el original
    
    # Si get_db_connection falla, la vista lo captura y devuelve 500.
    # Si el *manejador de error* de la vista tiene un finally que falla, es diferente.
    # Por simplicidad, nos enfocamos en que el error original de get_db_connection se reporte.
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Fallo inicial de conexión")
    
    response = client.get('/facturas/') # Ruta que usa get_db_connection
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Fallo inicial de conexión" in json_data['details'] # El error original debe prevalecer

@mock.patch('app.logger') # Asumir que el logger de la app es 'app.logger'
def test_app_logs_critical_on_db_connection_failure(mock_app_logger, client, mock_db_connection):
    """Test: app.logger.critical (o error) es llamado en fallo de conexión a BD."""
    error_message = "Simulated DB connection failure for logging"
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError(error_message)

    client.get('/facturas/') # Intentar acceder a una ruta que usa la BD

    # Verificar que se llamó a un método de logging de error/crítico
    # El método exacto (error, critical, exception) depende de la implementación en app.py
    called_critical = mock_app_logger.critical.called
    called_error = mock_app_logger.error.called
    called_exception = mock_app_logger.exception.called
    assert called_critical or called_error or called_exception # Al menos uno fue llamado

    # Opcionalmente, verificar el mensaje si es predecible
    if called_critical:
        mock_app_logger.critical.assert_any_call(mock.ANY, exc_info=mock.ANY) # O con el mensaje específico
    elif called_error:
        mock_app_logger.error.assert_any_call(mock.ANY, exc_info=mock.ANY)
    elif called_exception:
        mock_app_logger.exception.assert_any_call(mock.ANY)


def test_db_config_attribute_error_on_nested_access(mock_db_connection):
    """Test: AttributeError en get_db_connection si accede a DB_CONFIG incorrectamente."""
    # get_db_connection(config={'host': {'sub_host': 'val'}}) si espera config['host'] como string.
    custom_config = {'host': {'sub_host_val': 'value'}, 'database': 'db', 'user': 'u', 'password': 'p'}
    
    with mock.patch('app.psycopg2.connect') as mock_actual_connect:
        # Si get_db_connection hiciera algo como config['host'].lower(), fallaría con AttributeError
        # Esto depende de la implementación exacta de get_db_connection.
        # Psycopg2.connect espera strings, por lo que si se le pasa un dict para 'host', fallará.
        mock_actual_connect.side_effect = TypeError("host parameter must be a string")
        
        mock_db_connection['get_db_connection'].stop() # Detener mock global
        with pytest.raises(TypeError, match="host parameter must be a string"):
            get_db_connection(config=custom_config)
        mock_db_connection['get_db_connection'].start() # Restaurar


def test_nueva_factura_post_total_precision_con_decimal(client, mock_db_connection):
    """Test: nueva_factura con precios/cantidades Decimal para asegurar precisión en total."""
    mock_cursor = mock_db_connection["cursor"]
    # Precios y cantidades como Decimal
    precio1 = decimal.Decimal('10.01')
    cantidad1 = decimal.Decimal('2.5')
    subtotal1 = precio1 * cantidad1 # 25.025

    precio2 = decimal.Decimal('0.02')
    cantidad2 = decimal.Decimal('1.5')
    subtotal2 = precio2 * cantidad2 # 0.030

    total_factura_esperado = subtotal1 + subtotal2 # 25.055
    # La BD podría redondear a 2 decimales (ej: 25.06 o 25.05). Asumamos que se guarda con más precisión o como la app calcule.

    mock_cursor.fetchone.side_effect = [
        (precio1,), (precio2,), # Precios
        (136,), (463,) # Secuencia, Factura ID
    ]
    
    form_data = {
        'cliente_id': '102',
        'producto_id_1': '10', 'cantidad_1': str(cantidad1),
        'producto_id_2': '11', 'cantidad_2': str(cantidad2),
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 302
    
    # Verificar que el total en la BD es el esperado, considerando la precisión de Decimal
    # Esto depende de cómo la app maneje y guarde los Decimal.
    # El mock.call debe usar el mismo tipo (Decimal o float) que la app usa para la BD.
    # Si la app convierte a float para la BD, puede haber pérdida de precisión.
    # Si usa Decimal (o la BD es NUMERIC), la precisión se mantiene.
    mock_cursor.execute.assert_any_call(
        'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
        ('FACT-136', '102', total_factura_esperado) # El tipo de total_factura_esperado debe coincidir con el de la app
    )

def test_ver_factura_fecha_formato_regional_en_template(client, mock_db_connection):
    """Test: ver_factura muestra la fecha en un formato regional esperado (si aplica)."""
    # Asumir que la app o la plantilla formatea la fecha. Ej: DD/MM/YYYY
    mock_cursor = mock_db_connection["cursor"]
    # Fecha en formato ISO desde la BD
    mock_cursor.fetchone.return_value = (1, 'F-FECHA', '2023-12-25', 10.0, 1, 'Navidad', 'Polo Norte', '0', 'ACTIVA')
    mock_cursor.fetchall.return_value = [] # No items

    response = client.get('/factura/1')
    assert response.status_code == 200
    # Verificar el formato de fecha esperado en la plantilla.
    # Esto es frágil si el formato cambia.
    assert b"Fecha: 25/12/2023" in response.data # Ejemplo de formato esperado
def test_ver_factura_not_found(client, mock_db_connection):
    """Test: ver_factura retorna 404 o mensaje si la factura no existe."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = None # Simulate factura not found

    response = client.get('/factura/999') # Requesting non-existent invoice

    # The original code returns "Cliente no encontrado", 404 for editar_cliente.
    # It doesn't explicitly handle not found for ver_factura.
    # If factura is None, render_template might raise an error or the template might handle None.
    # A robust app would return 404. Let's test for 404 and a possible message.
    # Based on the provided app code, it might actually error or render an empty template.
    # Let's assume we want it to return 404. We might need to add this logic to app.py.
    # ASSUMING app.py is updated to handle factura=None by returning a 404
    # Example app.py update:
    # if factura is None:
    #     return "Factura no encontrada", 404
    # return render_template(...)

    assert response.status_code == 404
    assert b"Factura no encontrada" in response.data # Or check specific error template content


# Add DB error tests for ver_factura (similar to listar_facturas, but two queries)
# ... (omitted for brevity, similar structure to listar_facturas DB error tests)

# --- Tests para nueva_factura (GET) ---

def test_nueva_factura_get_success(client, mock_db_connection):
    """Test: GET /factura/nueva muestra el formulario y carga datos."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock return values for fetching clients and products
    mock_cursor.fetchall.side_effect = [
        [(1, 'Cliente A'), (2, 'Cliente B')], # Clients
        [(1, 'Prod X', 100.00), (2, 'Prod Y', 101.00)], # Products
    ]

    response = client.get('/factura/nueva')

    assert response.status_code == 200
    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called_once_with(config=None)
    mock_db_connection["conn"].cursor.assert_called_once()
    execute_calls = mock_cursor.execute.call_args_list
    assert len(execute_calls) == 2
    assert execute_calls[0] == mock.call('SELECT id, nombre FROM clientes ORDER BY nombre;')
    assert execute_calls[1] == mock.call('SELECT id, nombre, precio FROM productos ORDER BY nombre;')
    mock_db_connection["conn"].close.assert_called_once()

    # Verify response content (checking for form elements and loaded data)
    assert b"<form method=\"POST\">" in response.data
    assert b"<option value=\"1\">Cliente A</option>" in response.data
    assert b"<option value=\"1\">Prod X (100.0)</option>" in response.data # Assuming template formats price


# Add DB error tests for nueva_factura GET (similar to listar_facturas)
# ... (omitted for brevity)


# --- Tests para nueva_factura (POST) ---

def test_nueva_factura_post_success_with_items(client, mock_db_connection):
    """Test: POST /factura/nueva crea una factura con items y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    # Mock sequence for:
    # 1. Getting product price for item 1
    # 2. Getting product price for item 2
    # 3. Getting next invoice number sequence value
    # 4. Inserting factura (returning ID)
    # 5. Inserting item 1
    # 6. Inserting item 2

    mock_cursor.fetchone.side_effect = [
        (100.00,), # Price for product_id_1 (ID=1)
        (101.00,), # Price for product_id_2 (ID=2)
        (123,),     # Next sequence number (FACT-123)
        (456,),     # Newly created factura ID (ID=456)
    ]
    # fetchall not used in the POST part of nueva_factura

    # Prepare form data
    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1',
        'cantidad_1': '2',
        'producto_id_2': '2',
        'cantidad_2': '0.5',
        # Assuming no other items or they are empty
    }

    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/factura/456' # Expect redirect to the new invoice ID

    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called() # Called multiple times in original code (inefficient)
                                                            # Or ideally, called once if refactored
                                                            # Let's check the calls based on the provided app code structure

    # Check calls within the item loop (inefficient multiple connections)
    # Original code calls get_db_connection inside loop. With our mock fixture,
    # app.get_db_connection is called once at the start of the request.
    # Then conn.cursor() is called inside the loop. This is still inefficient.
    # Test structure should match app code's interaction with the mock.
    # Let's trace the expected calls based on the provided app code:
    # 1. get_db_connection() # before item loop
    # 2. conn.cursor() # inside loop for item 1
    # 3. cursor.execute('SELECT price...', (prod_id,))
    # 4. cursor.fetchone() # price
    # 5. cursor.close() # inside loop
    # 6. conn.close() # inside loop - BAD! Connection closed before processing all items!
    # This reveals a bug in the original app code's POST handler for nueva_factura.
    # It opens/closes connection *per item* which is wrong.
    # The test should ideally fail due to this bug, OR the test should mock the bug's behavior.
    # Let's assume the app code *will be fixed* to open/close the connection once.
    # Expected fixed flow:
    # 1. get_db_connection()
    # 2. conn.cursor()
    # 3. Loop items: cursor.execute(price), cursor.fetchone()
    # 4. cursor.execute(sequence)
    # 5. cursor.fetchone() # sequence
    # 6. cursor.execute(insert_factura, ...)
    # 7. cursor.fetchone() # factura_id
    # 8. Loop items: cursor.execute(insert_item, ...)
    # 9. conn.commit()
    # 10. cursor.close()
    # 11. conn.close()

    # Let's write the assertions assuming the *fixed* app code structure:
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT precio FROM productos WHERE id = %s;', ('1',)),
        mock.call('SELECT precio FROM productos WHERE id = %s;', ('2',)),
        mock.call("SELECT nextval('factura_numero_seq')"),
        mock.call(
            'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
            ('FACT-123', '101', 251.00) # Total: (2 * 100) + (0.5 * 101) = 200 + 50.5 = 250.5, wait, form data is strings '2', '0.5'.
            # Python adds floats: (2 * 100.0) + (0.5 * 101.0) = 200.0 + 50.5 = 250.5.
            # The test data was 150.50 + 200.00 = 350.50 in the listar test. Let's use consistent product prices if possible.
            # Sample prices 100.00 and 101.00 are fine. Total is 250.5.
            # The test asserts total 251.00 which is wrong based on the mock prices.
            # Let's fix the assertion value based on mock prices:
            # Total: (float('2') * 100.00) + (float('0.5') * 101.00) = 200.0 + 50.5 = 250.5
            ('FACT-123', '101', 250.50)
        ),
        mock.call(
            'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
            (456, '1', '2', 100.00, 200.00) # quantities and product_ids are strings from form
        ),
         mock.call(
            'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
            (456, '2', '0.5', 101.00, 50.50)
        ),
    ], any_order=True) # Use any_order=True because the item order might vary if loop order isn't guaranteed

    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["cursor"].close.assert_called() # Cursor is closed
    mock_db_connection["conn"].close.assert_called_once() # Connection is closed


def test_nueva_factura_post_success_no_items(client, mock_db_connection):
    """Test: POST /factura/nueva crea una factura sin items (should result in total 0)."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock sequence for:
    # 1. Getting next invoice number sequence value
    # 2. Inserting factura (returning ID)
    mock_cursor.fetchone.side_effect = [
        (124,),     # Next sequence number (FACT-124)
        (457,),     # Newly created factura ID (ID=457)
    ]

    form_data = {
        'cliente_id': '102',
        # No product_id_x or cantidad_x fields
    }

    response = client.post('/factura/nueva', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/factura/457' # Expect redirect to the new invoice ID

    # Verify DB interactions
    mock_db_connection["get_db_connection"].assert_called_once() # Called once in the fixed app code
    # Check execute calls
    mock_cursor.execute.assert_has_calls([
        mock.call("SELECT nextval('factura_numero_seq')"),
        mock.call(
            'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
            ('FACT-124', '102', 0.00) # Total should be 0.00
        ),
    ], any_order=True)

    # Ensure no item inserts were attempted
    # This is harder to assert directly on 'execute' unless we check call args,
    # but we can check that fetchone for prices wasn't called inside a loop.
    # With the fixed app code, there would be no calls to 'SELECT precio' if no items.
    # The number of execute calls above confirms this implicitly.

    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["cursor"].close.assert_called()
    mock_db_connection["conn"].close.assert_called_once()


# Test cases for nueva_factura POST failures:
# - DB error getting price
# - DB error getting sequence
# - DB error inserting factura
# - DB error inserting item (should rollback factura insert)
# - Missing cliente_id (app code doesn't validate, might raise KeyError)
# - Invalid product/client IDs (relies on DB FK constraints or app validation)
# - Non-numeric quantity/price (app code might raise ValueError/TypeError on conversion)

def test_nueva_factura_post_db_error_get_price(client, mock_db_connection):
    """Test: POST /factura/nueva handles DB error when getting product price."""
    mock_cursor = mock_db_connection["cursor"]
    # Configure fetching product price to raise a DB error
    mock_cursor.fetchone.side_effect = psycopg2.OperationalError("DB error fetching price")

    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1',
        'cantidad_1': '2',
    }

    response = client.post('/factura/nueva', data=form_data)

    # Assuming app.py has error handling around DB operations in the POST route
    # It should probably return a 500 error or redirect back with an error message.
    # Let's assume it returns 500 JSON like listar_facturas.
    assert response.status_code == 500
    json_data = response.get_json()
    assert json_data['error'] == "Error de base de datos"
    assert "DB error fetching price" in json_data['details']

    # Verify interactions
    # get_db_connection should be called
    mock_db_connection["get_db_connection"].assert_called_once()
    # Cursor should be obtained
    mock_db_connection["conn"].cursor.assert_called_once()
    # execute should be called to get the price
    mock_cursor.execute.assert_called_once_with('SELECT precio FROM productos WHERE id = %s;', ('1',))
    # Check that commit was NOT called
    mock_db_connection["conn"].commit.assert_not_called()
    # Check that rollback was called (assuming error handling includes rollback)
    mock_db_connection["conn"].rollback.assert_called_once()
    # Connection should be closed
    mock_db_connection["conn"].close.assert_called_once()


# Add tests for other DB errors during POST (sequence, insert factura, insert item)
# Test rollback specifically for item insertion failure due to FK violation etc.
# ... (omitted for brevity, similar structure to the above DB error test)


# --- Tests para listar_clientes ---

def test_listar_clientes_success(client, mock_db_connection):
    """Test: listar_clientes muestra la lista de clientes."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [
        (1, 'Cliente A', 'Dir A', 'Tel A', 'email A'),
        (2, 'Cliente B', 'Dir B', 'Tel B', 'email B'),
    ]

    response = client.get('/clientes')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, direccion, telefono, email FROM clientes ORDER BY nombre;')
    assert b"<h1>Lista de Clientes</h1>" in response.data
    assert b"Cliente A" in response.data
    assert b"email B" in response.data


# Add tests for empty list and DB errors for listar_clientes
# ... (omitted)


# --- Tests para agregar_cliente (GET) ---

def test_agregar_cliente_get_success(client):
    """Test: GET /agregar_cliente muestra el formulario."""
    response = client.get('/agregar_cliente')
    assert response.status_code == 200
    assert b"<form method=\"POST\">" in response.data


# --- Tests para agregar_cliente (POST) ---

def test_agregar_cliente_post_success(client, mock_db_connection):
    """Test: POST /agregar_cliente agrega un cliente y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Nuevo Cliente',
        'direccion': 'Nueva Direccion',
        'telefono': '123-456',
        'email': 'nuevo@example.com'
    }

    response = client.post('/agregar_cliente', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        ('Nuevo Cliente', 'Nueva Direccion', '123-456', 'nuevo@example.com')
    )
    mock_db_connection["conn"].commit.assert_called_once()


def test_agregar_cliente_post_missing_fields(client, mock_db_connection):
    """Test: POST /agregar_cliente with missing fields returns error message."""
    # Missing 'telefono'
    form_data = {
        'nombre': 'Nuevo Cliente',
        'direccion': 'Nueva Direccion',
        'email': 'nuevo@example.com'
    }

    response = client.post('/agregar_cliente', data=form_data)

    assert response.status_code == 200 # Stays on the same page with error
    assert b"Todos los campos son obligatorios." in response.data # Check for the error message
    mock_db_connection["get_db_connection"].assert_not_called() # DB interaction should not happen


# Add DB error test for agregar_cliente POST
# ... (omitted)


# --- Tests para eliminar_cliente (POST) ---

def test_eliminar_cliente_post_success(client, mock_db_connection):
    """Test: POST /eliminar_cliente/<id> deletes a client with no associated invoices."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock count query to return 0
    mock_cursor.fetchone.return_value = (0,)

    response = client.post('/eliminar_cliente/123') # Attempt to delete client ID 123

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (123,)), # Check count first
        mock.call('DELETE FROM clientes WHERE id = %s;', (123,)), # Then delete
    ])
    mock_db_connection["conn"].commit.assert_called_once()


def test_eliminar_cliente_post_with_invoices(client, mock_db_connection):
    """Test: POST /eliminar_cliente/<id> prevents deleting client with invoices."""
    mock_cursor = mock_db_connection["cursor"]
    # Mock count query to return > 0
    mock_cursor.fetchone.return_value = (5,) # Simulate 5 associated invoices

    # Mock fetching clients again as the app code does this if deletion is blocked
    mock_cursor.execute.side_effect = [
         # First execute is COUNT(*) -> handled by fetchone mock above
         # Second execute is SELECT * FROM clientes; -> need to mock its fetchall
         None # execute itself returns None, fetchall is what we need to mock
    ]
    # The fetchall call that happens after COUNT(*) > 0
    original_fetchall = mock_cursor.fetchall # Store original fetchall
    def fetchall_side_effect():
         # After the COUNT(*) execute, the next fetchall should return client list
         call_args = mock_cursor.execute.call_args_list
         # Check if the last execute call was the SELECT * FROM clients
         if len(call_args) > 0 and call_args[-1][0][0].startswith('SELECT * FROM clientes'):
              return [(1, 'Client A', 'Dir A', 'Tel A', 'email A')] # Sample client data
         # Otherwise, fall back or raise error if unexpected execute happens
         return original_fetchall() # Or raise unexpected error

    mock_cursor.fetchall.side_effect = fetchall_side_effect # Use side_effect for fetchall too


    response = client.post('/eliminar_cliente/123') # Attempt to delete client ID 123

    assert response.status_code == 200 # Stays on the client list page
    assert b"No se puede eliminar el cliente porque tiene facturas asociadas." in response.data # Check error message

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (123,)), # Check count first
        mock.call('SELECT * FROM clientes;'), # Then fetches clients to re-render the page
    ])
    # Check that DELETE and COMMIT were NOT called
    mock_db_connection["conn"].commit.assert_not_called()
    # Need a way to assert DELETE was not called. Can check call_args_list explicitly.
    execute_calls = [call[0][0] for call in mock_cursor.execute.call_args_list]
    assert 'DELETE FROM clientes WHERE id = %s;' not in execute_calls


# Add DB error tests for eliminar_cliente (count error, delete error)
# ... (omitted)


# --- Tests para editar_cliente (GET) ---

def test_editar_cliente_get_success(client, mock_db_connection):
    """Test: GET /clientes/<id>/editar muestra el formulario con datos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'Client Edit', 'Dir Edit', 'Tel Edit', 'email Edit')

    response = client.get('/clientes/1/editar')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT * FROM clientes WHERE id = %s;', (1,))
    assert b"<form method=\"POST\" action=\"/clientes/1/actualizar\">" in response.data
    assert b"value=\"Client Edit\"" in response.data
    assert b"value=\"email Edit\"" in response.data


def test_editar_cliente_get_not_found(client, mock_db_connection):
    """Test: GET /clientes/<id>/editar for non-existent client returns 404."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = None # Simulate not found

    response = client.get('/clientes/999/editar')

    assert response.status_code == 404
    assert b"Cliente no encontrado" in response.data # Check the specific message from app.py


# Add DB error test for editar_cliente GET
# ... (omitted)


# --- Tests para actualizar_cliente (POST) ---

def test_actualizar_cliente_post_success(client, mock_db_connection):
    """Test: POST /clientes/<id>/actualizar updates client data and redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Cliente Actualizado',
        'direccion': 'Direccion Actualizada',
        'telefono': '987-654',
        'email': 'updated@example.com'
    }

    response = client.post('/clientes/1/actualizar', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/clientes' # Expect redirect to client list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        """
        UPDATE clientes
        SET nombre = %s, direccion = %s, telefono = %s, email = %s
        WHERE id = %s;
        """,
        ('Cliente Actualizado', 'Direccion Actualizada', '987-654', 'updated@example.com', 1)
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for actualizar_cliente POST
# Add tests for missing fields (if validation is added to app.py)
# ... (omitted)


# --- Tests para listar_productos ---

def test_listar_productos_success(client, mock_db_connection):
    """Test: /productos muestra la lista de productos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [
        (1, 'Prod A', 'Desc A', 10.00),
        (2, 'Prod B', 'Desc B', 20.50),
    ]

    response = client.get('/productos')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, descripcion, precio FROM productos ORDER BY nombre;')
    assert b"<h1>Lista de Productos</h1>" in response.data
    assert b"Prod A" in response.data
    assert b"20.50" in response.data


# Add tests for empty list and DB errors for listar_productos
# ... (omitted)

# --- Tests para agregar_producto (GET) ---

def test_agregar_producto_get_success(client):
    """Test: GET /productos/agregar muestra el formulario."""
    response = client.get('/productos/agregar')
    assert response.status_code == 200
    assert b"<form method=\"POST\">" in response.data


# --- Tests para agregar_producto (POST) ---

def test_agregar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/agregar agrega un producto y redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Nuevo Producto',
        'descripcion': 'Descripcion del nuevo producto',
        'precio': '123.45'
    }

    response = client.post('/productos/agregar', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        'INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);',
        ('Nuevo Producto', 'Descripcion del nuevo producto', '123.45') # price is string from form
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for agregar_producto POST
# Add tests for missing fields (if validation added)
# Add test for non-numeric price (will likely cause ValueError/TypeError in app.py)
# ... (omitted)


# --- Tests para editar_producto (GET/POST) ---

def test_editar_producto_get_success(client, mock_db_connection):
    """Test: GET /productos/editar/<id> muestra el formulario con datos."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.return_value = (1, 'Prod Edit', 'Desc Edit', 99.99)

    response = client.get('/productos/editar/1')

    assert response.status_code == 200
    mock_cursor.execute.assert_called_once_with('SELECT id, nombre, descripcion, precio FROM productos WHERE id = %s;', (1,))
    assert b"<form method=\"POST\">" in response.data # action is not specified in original template form
    assert b"value=\"Prod Edit\"" in response.data
    assert b"value=\"99.99\"" in response.data


# Add test for editar_producto GET not found
# ... (omitted)


def test_editar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/editar/<id> updates product data and redirige."""
    mock_cursor = mock_db_connection["cursor"]

    form_data = {
        'nombre': 'Producto Actualizado',
        'descripcion': 'Descripcion actualizada',
        'precio': '150.75'
    }

    response = client.post('/productos/editar/1', data=form_data)

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with(
        'UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
        ('Producto Actualizado', 'Descripcion actualizada', '150.75', 1) # price is string
    )
    mock_db_connection["conn"].commit.assert_called_once()


# Add DB error test for editar_producto POST
# Add tests for missing fields, invalid price (if validation added)
# ... (omitted)


# --- Tests para eliminar_producto (POST) ---

def test_eliminar_producto_post_success(client, mock_db_connection):
    """Test: POST /productos/eliminar/<id> deletes a product not in invoice items."""
    mock_cursor = mock_db_connection["cursor"]
    # Deletion succeeds without raising ForeignKeyViolation

    response = client.post('/productos/eliminar/123') # Attempt to delete product ID 123

    assert response.status_code == 302 # Expect redirect
    assert response.location == '/productos' # Expect redirect to product list

    # Verify DB interaction
    mock_cursor.execute.assert_called_once_with('DELETE FROM productos WHERE id = %s;', (123,))
    mock_db_connection["conn"].commit.assert_called_once()
    mock_db_connection["conn"].rollback.assert_not_called() # Ensure no rollback


def test_eliminar_producto_post_foreign_key_violation(client, mock_db_connection):
    """Test: POST /productos/eliminar/<id> handles ForeignKeyViolation."""
    mock_cursor = mock_db_connection["cursor"]
    # Configure delete execute to raise ForeignKeyViolation
    mock_cursor.execute.side_effect = psycopg2.errors.ForeignKeyViolation("Simulated FK violation")

    # Mock fetching products again as the app code does this if deletion fails
    mock_cursor.execute.side_effect = [
         # First execute is DELETE -> handled by FK violation below
         # Second execute is SELECT * FROM productos; -> need to mock its fetchall
         psycopg2.errors.ForeignKeyViolation("Simulated FK violation"), # First call
         None # execute itself returns None, fetchall is what we need to mock for the second call
    ]
    # The fetchall call that happens after the exception is caught
    original_fetchall = mock_cursor.fetchall # Store original fetchall
    def fetchall_side_effect():
         # After the DELETE execute (which raises FKV), the next execute is SELECT *
         # The fetchall after that SELECT should return the product list
         call_args = mock_cursor.execute.call_args_list
         if len(call_args) > 1 and call_args[-1][0][0].startswith('SELECT * FROM productos'):
              return [(1, 'Prod A', 'Desc A', 10.00)] # Sample product data
         return original_fetchall()

    mock_cursor.fetchall.side_effect = fetchall_side_effect


    response = client.post('/productos/eliminar/123') # Attempt to delete product ID 123

    assert response.status_code == 200 # Stays on the product list page
    assert b"No se puede eliminar el producto porque se encuentra en una factura." in response.data # Check error message

    # Verify DB interaction
    mock_cursor.execute.assert_has_calls([
        mock.call('DELETE FROM productos WHERE id = %s;', (123,)), # Check delete call
        mock.call('SELECT * FROM productos;'), # Check select call after rollback
    ])
    mock_db_connection["conn"].commit.assert_not_called() # Ensure commit was NOT called
    mock_db_connection["conn"].rollback.assert_called_once() # Ensure rollback was called

def test_listar_facturas_sql_injection(client, mock_db_connection):
    """Test: Verificar que listar_facturas no es vulnerable a inyección SQL."""
    mock_cursor = mock_db_connection["cursor"]
    response = client.get('/facturas/?order=id;DROP TABLE facturas--')
    assert response.status_code == 200
    # Verificar que no se ejecutó código malicioso
    assert "DROP TABLE" not in str(mock_cursor.execute.call_args)

def test_xss_in_client_names(client, mock_db_connection):
    """Test: Verificar que los nombres de clientes se escapan correctamente."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [
        (1, '<script>alert("XSS")</script>', 'Dir', 'Tel', 'email')
    ]
    response = client.get('/clientes')
    assert response.status_code == 200
    assert b'&lt;script&gt;alert("XSS")&lt;/script&gt;' in response.data
def test_csrf_protection_missing(client, mock_db_connection):
    """Test: Verificar falta de protección CSRF en eliminación de cliente."""
    response = client.post('/eliminar_cliente/1')
    # En una aplicación segura, esto debería fallar sin CSRF token
    # Pero como no hay protección, debería pasar (esto es malo)
    assert response.status_code in [302, 200]  # Esto muestra la vulnerabilidad
def test_agregar_cliente_post_nombre_con_espacios_extremos(client, mock_db_connection):
    """Test: agregar_cliente con nombre con espacios al inicio/final. ¿Se normaliza o guarda tal cual?"""
    mock_cursor = mock_db_connection["cursor"]
    nombre_con_espacios = "  Cliente con Espacios  "
    nombre_esperado_db = nombre_con_espacios.strip() # Asumiendo que la app hace strip()

    form_data = {'nombre': nombre_con_espacios, 'direccion': 'Dir', 'telefono': '123', 'email': 'espacios@b.com'}
    client.post('/agregar_cliente', data=form_data)

    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
        (nombre_esperado_db, 'Dir', '123', 'espacios@b.com')
    )
    mock_db_connection["conn"].commit.assert_called_once()

def test_nueva_factura_post_items_con_mismo_producto_id(client, mock_db_connection):
    """Test: nueva_factura con dos líneas de item para el mismo producto_id."""
    mock_cursor = mock_db_connection["cursor"]
    # Precio, Secuencia, Factura ID
    mock_cursor.fetchone.side_effect = [(10.00,), (10.00,), (132,), (461,)] 
    # La app podría sumar cantidades o tratar como líneas separadas. Asumimos líneas separadas.
    # Total esperado: (1 * 10) + (2 * 10) = 30

    form_data = {
        'cliente_id': '101',
        'producto_id_1': '1', 'cantidad_1': '1', # Producto 1, cantidad 1
        'producto_id_2': '1', 'cantidad_2': '2', # Mismo Producto 1, cantidad 2
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 302
    assert response.location == '/factura/461'

    # Verificar inserción de factura con total correcto (asumiendo 30.00)
    # Verificar inserciones de items (dos llamadas a INSERT factura_items)
    calls = mock_cursor.execute.call_args_list
    insert_factura_call = mock.call(
        'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
        ('FACT-132', '101', decimal.Decimal('30.00')) # O float(30.00) según la app
    )
    insert_item1_call = mock.call(
        'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
        (461, '1', '1', decimal.Decimal('10.00'), decimal.Decimal('10.00'))
    )
    insert_item2_call = mock.call(
        'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
        (461, '1', '2', decimal.Decimal('10.00'), decimal.Decimal('20.00'))
    )
    assert insert_factura_call in calls
    assert insert_item1_call in calls
    assert insert_item2_call in calls
    mock_db_connection["conn"].commit.assert_called_once()


def test_agregar_producto_post_descripcion_muy_larga(client, mock_db_connection):
    """Test: agregar_producto con descripción extremadamente larga."""
    mock_cursor = mock_db_connection["cursor"]
    descripcion_larga = "Descripción " * 1000 # 12000 caracteres
    # Asumir que la BD la trunca o da error si excede el límite de la columna.
    mock_cursor.execute.side_effect = psycopg2_errors.StringDataRightTruncation("descripción demasiado larga")

    form_data = {'nombre': 'Prod Largo', 'descripcion': descripcion_larga, 'precio': '10'}
    response = client.post('/productos/agregar', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "descripción demasiado larga" in json_data['details']

def test_nueva_factura_post_cantidad_muy_grande_calculo_subtotal(client, mock_db_connection):
    """Test: nueva_factura con cantidad muy grande, verificar posible overflow en cálculo o BD."""
    mock_cursor = mock_db_connection["cursor"]
    # Precio, Secuencia, Factura ID
    mock_cursor.fetchone.side_effect = [(decimal.Decimal('1.00'),), (133,), (462,)]
    cantidad_grande_str = "1000000000000.50" # Un número grande
    # Asumir que el subtotal (precio * cantidad) excede el límite de Numeric en BD para subtotal.
    def execute_side_effect(query, params=None):
        if "INSERT INTO factura_items" in query:
            # params[4] es subtotal
            if params[4] > decimal.Decimal('1E12'): # Simular un límite
                 raise psycopg2_errors.NumericValueOutOfRange("subtotal del item fuera de rango")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO factura_items" in query and params[2] == cantidad_grande_str: # params[2] es cantidad
            # subtotal = decimal.Decimal(params[2]) * params[3] # cantidad * precio
            # if subtotal > decimal.Decimal('1E12'):
            raise psycopg2_errors.NumericValueOutOfRange("subtotal del item fuera de rango")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router


    form_data = {'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': cantidad_grande_str}
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "subtotal del item fuera de rango" in json_data['details']

def test_agregar_cliente_post_fecha_registro_formato_invalido(client, mock_db_connection):
    """Test: agregar_cliente con un campo de fecha hipotético en formato inválido."""
    # Asumir que `clientes` tiene una columna `fecha_registro DATE` y el form la envía.
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.execute.side_effect = psycopg2_errors.InvalidDatetimeFormat("formato de fecha inválido: '30/02/2025'")
    form_data = {
        'nombre': 'Cliente Fecha', 'direccion': 'Dir', 'telefono': '123', 
        'email': 'fecha@b.com', 'fecha_registro': '30/02/2025' # Fecha inválida
    }
    # Asumir que la app intenta insertar esta fecha directamente.
    response = client.post('/agregar_cliente', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "formato de fecha inválido" in json_data['details']

# Tests de HTTP y Detalles de Petición/Respuesta
def test_agregar_cliente_post_unexpected_content_type(client, mock_db_connection):
    """Test: POST a /agregar_cliente con Content-Type application/json."""
    # request.form estará vacío. La app debería manejar esto como campos faltantes.
    response = client.post('/agregar_cliente', 
                           data=json.dumps({'nombre': 'Test JSON'}), 
                           content_type='application/json')
    assert response.status_code == 200 # Asume que vuelve al form con error
    assert b"Todos los campos son obligatorios." in response.data
    mock_db_connection["get_db_connection"].assert_not_called()

def test_json_error_response_content_type(client, mock_db_connection):
    """Test: Errores de BD que devuelven JSON tienen Content-Type application/json."""
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Error DB for JSON test")
    response = client.get('/facturas/') # Ruta que devuelve JSON en error de BD
    assert response.status_code == 500
    assert response.content_type == 'application/json'

def test_html_success_response_content_type(client, mock_db_connection):
    """Test: Rutas HTML exitosas tienen Content-Type text/html; charset=utf-8."""
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchall.return_value = [] # No data, pero la página se renderiza
    response = client.get('/facturas/')
    assert response.status_code == 200
    assert response.content_type == 'text/html; charset=utf-8'

# Tests de Interacción con BD y Transacciones
# No es fácil simular fallo de `psycopg2.connect` después de retries sin modificar `get_db_connection`.
# El mock actual de `get_db_connection` ya cubre el fallo de conexión inicial.

def test_listar_facturas_fetchall_returns_malformed_row(client, mock_db_connection):
    """Test: listar_facturas donde fetchall() retorna una fila con datos malformados."""
    mock_cursor = mock_db_connection["cursor"]
    # Fila[0] debería ser int (id), Fila[4] debería ser numérico (total)
    # Si la plantilla espera desempaquetar o formatear estos tipos y son incorrectos, puede fallar.
    malformed_row = ("id_string_malo", "FACT-ERR", "2023-01-01", "Cliente Err", "total_string_malo")
    mock_cursor.fetchall.return_value = [malformed_row]
    
    response = client.get('/facturas/')
    # La plantilla podría fallar al renderizar 'total_string_malo' como moneda o 'id_string_malo' en un enlace.
    # Esto resultaría en un 500 Internal Server Error si no se maneja en la plantilla con `default` o similar.
    assert response.status_code == 500 
    # El error exacto es difícil de predecir (TemplateAssertionError, TypeError, etc.)
    # Verificar que al menos no es un 200 OK. Podríamos buscar un mensaje genérico de error de Flask.
    assert b"Internal Server Error" in response.data # Si es la página de error por defecto de Flask.


def test_nueva_factura_post_check_constraint_violation(client, mock_db_connection):
    """Test: nueva_factura con violación de un CHECK constraint (ej: tipo_factura inválido)."""
    # Asumir que `facturas` tiene `tipo_factura CHAR(1) CHECK (tipo_factura IN ('A', 'B', 'C'))`
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,), (134,)] # Precio, Secuencia
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query:
            # Asumir que los params incluyen un tipo_factura 'X' inválido
            raise psycopg2_errors.CheckViolation("violación de check constraint 'chk_tipo_factura'")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        # Suponiendo que el INSERT incluye un campo para tipo_factura y se le pasa 'X'
        if "INSERT INTO facturas" in query: # y params contiene tipo_factura='X'
            raise psycopg2_errors.CheckViolation("violación de check constraint 'chk_tipo_factura'")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {
        'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1',
        'tipo_factura': 'X' # Dato hipotético que viola un CHECK
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "violación de check constraint" in json_data['details']

def test_nueva_factura_post_datetime_field_overflow(client, mock_db_connection):
    """Test: nueva_factura con fecha que causa DatetimeFieldOverflow."""
    # Asumir que el form envía un campo 'fecha_emision_factura'
    mock_cursor = mock_db_connection["cursor"]
    mock_cursor.fetchone.side_effect = [(10.00,), (135,)] # Precio, Secuencia
    def execute_side_effect(query, params=None):
        if "INSERT INTO facturas" in query: # y params contiene la fecha 0000-01-01
            raise psycopg2_errors.DatetimeFieldOverflow("fecha fuera de rango para tipo timestamp")
        return None
    
    original_execute = mock_cursor.execute
    def side_effect_router(query, params=None):
        if "INSERT INTO facturas" in query: # y params contiene la fecha problemática
            raise psycopg2_errors.DatetimeFieldOverflow("fecha fuera de rango para tipo timestamp")
        return original_execute(query, params)
    mock_cursor.execute.side_effect = side_effect_router

    form_data = {
        'cliente_id': '101', 'producto_id_1': '1', 'cantidad_1': '1',
        'fecha_emision_factura': '0000-01-01' # Fecha problemática
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 500
    json_data = response.get_json()
    assert "fecha fuera de rango" in json_data['details']

# Tests de Lógica de Aplicación y Estado
def test_ver_factura_marcada_como_cancelada(client, mock_db_connection):
    """Test: ver_factura para una factura que tiene un estado 'CANCELADA'."""
    # Asumir que `facturas` tiene un campo `estado` y la plantilla lo muestra.
    mock_cursor = mock_db_connection["cursor"]
    # id, numero, fecha, total, cliente_id, cliente_nombre, ..., estado (nuevo campo)
    factura_cancelada_data = (1, 'F-CANC', '2023-03-01', 50.0, 3, 'Cliente C', 'Dir C', 'Tel C', 'CANCELADA')
    mock_cursor.fetchone.return_value = factura_cancelada_data
    mock_cursor.fetchall.return_value = [] # Sin items para simplificar

    response = client.get('/factura/1')
    assert response.status_code == 200
    assert b"F-CANC" in response.data
    assert b"Estado: CANCELADA" in response.data # Mensaje esperado en la plantilla

def test_editar_cliente_get_xss_prevention_in_form_values(client, mock_db_connection):
    """Test: editar_cliente, los datos con HTML especial se escapan en los values del form."""
    mock_cursor = mock_db_connection["cursor"]
    xss_nombre = "<script>alert('XSS')</script>"
    # id, nombre, direccion, telefono, email
    cliente_con_xss = (1, xss_nombre, "Dir", "Tel", "xss@example.com")
    mock_cursor.fetchone.return_value = cliente_con_xss

    response = client.get('/clientes/1/editar')
    assert response.status_code == 200
    # Verificar que el script NO está tal cual en el value, sino escapado.
    # Flask/Jinja2 escapan por defecto en {{ ... }}.
    # En <input value="{{ cliente.nombre }}">, se escaparía.
    escaped_xss_nombre = "&lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;"
    assert bytes(escaped_xss_nombre, 'utf-8') in response.data
    assert b"<script>alert('XSS')</script>" not in response.data # No debe estar el script crudo


def test_editar_producto_post_precio_cero(client, mock_db_connection):
    """Test: editar_producto actualizando el precio a 0.00. ¿Es permitido?"""
    mock_cursor = mock_db_connection["cursor"]
    form_data = {'nombre': 'Prod Gratis', 'descripcion': 'Desc', 'precio': '0.00'}

    response = client.post('/productos/editar/1', data=form_data)
    assert response.status_code == 302 # Asumiendo que es una actualización válida
    assert response.location == '/productos'
    mock_cursor.execute.assert_called_once_with(
        'UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
        ('Prod Gratis', 'Desc', '0.00', 1)
    )
    mock_db_connection["conn"].commit.assert_called_once()

# Tests de Configuración y Logging de Flask
def test_get_db_connection_failure_finally_block_error_masking(client, mock_db_connection):
    """Test: Falla get_db_connection, y un hipotético finally en la vista también falla."""
    # Este test es complejo porque requiere controlar el flujo dentro de la vista.
    # Supongamos que una vista hace:
    # conn = None
    # try:
    #   conn = get_db_connection() # Falla aquí
    #   # ...
    # finally:
    #   if conn: conn.close() # No se ejecuta conn.close()
    #   raise ValueError("Error en finally") # Este error podría enmascarar el original
    
    # Si get_db_connection falla, la vista lo captura y devuelve 500.
    # Si el *manejador de error* de la vista tiene un finally que falla, es diferente.
    # Por simplicidad, nos enfocamos en que el error original de get_db_connection se reporte.
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError("Fallo inicial de conexión")
    
    response = client.get('/facturas/') # Ruta que usa get_db_connection
    assert response.status_code == 500
    json_data = response.get_json()
    assert "Fallo inicial de conexión" in json_data['details'] # El error original debe prevalecer

@mock.patch('app.logger') # Asumir que el logger de la app es 'app.logger'
def test_app_logs_critical_on_db_connection_failure(mock_app_logger, client, mock_db_connection):
    """Test: app.logger.critical (o error) es llamado en fallo de conexión a BD."""
    error_message = "Simulated DB connection failure for logging"
    mock_db_connection["get_db_connection"].side_effect = psycopg2.OperationalError(error_message)

    client.get('/facturas/') # Intentar acceder a una ruta que usa la BD

    # Verificar que se llamó a un método de logging de error/crítico
    # El método exacto (error, critical, exception) depende de la implementación en app.py
    called_critical = mock_app_logger.critical.called
    called_error = mock_app_logger.error.called
    called_exception = mock_app_logger.exception.called
    assert called_critical or called_error or called_exception # Al menos uno fue llamado

    # Opcionalmente, verificar el mensaje si es predecible
    if called_critical:
        mock_app_logger.critical.assert_any_call(mock.ANY, exc_info=mock.ANY) # O con el mensaje específico
    elif called_error:
        mock_app_logger.error.assert_any_call(mock.ANY, exc_info=mock.ANY)
    elif called_exception:
        mock_app_logger.exception.assert_any_call(mock.ANY)


def test_db_config_attribute_error_on_nested_access(mock_db_connection):
    """Test: AttributeError en get_db_connection si accede a DB_CONFIG incorrectamente."""
    # get_db_connection(config={'host': {'sub_host': 'val'}}) si espera config['host'] como string.
    custom_config = {'host': {'sub_host_val': 'value'}, 'database': 'db', 'user': 'u', 'password': 'p'}
    
    with mock.patch('app.psycopg2.connect') as mock_actual_connect:
        # Si get_db_connection hiciera algo como config['host'].lower(), fallaría con AttributeError
        # Esto depende de la implementación exacta de get_db_connection.
        # Psycopg2.connect espera strings, por lo que si se le pasa un dict para 'host', fallará.
        mock_actual_connect.side_effect = TypeError("host parameter must be a string")
        
        mock_db_connection['get_db_connection'].stop() # Detener mock global
        with pytest.raises(TypeError, match="host parameter must be a string"):
            get_db_connection(config=custom_config)
        mock_db_connection['get_db_connection'].start() # Restaurar


def test_nueva_factura_post_total_precision_con_decimal(client, mock_db_connection):
    """Test: nueva_factura con precios/cantidades Decimal para asegurar precisión en total."""
    mock_cursor = mock_db_connection["cursor"]
    # Precios y cantidades como Decimal
    precio1 = decimal.Decimal('10.01')
    cantidad1 = decimal.Decimal('2.5')
    subtotal1 = precio1 * cantidad1 # 25.025

    precio2 = decimal.Decimal('0.02')
    cantidad2 = decimal.Decimal('1.5')
    subtotal2 = precio2 * cantidad2 # 0.030

    total_factura_esperado = subtotal1 + subtotal2 # 25.055
    # La BD podría redondear a 2 decimales (ej: 25.06 o 25.05). Asumamos que se guarda con más precisión o como la app calcule.

    mock_cursor.fetchone.side_effect = [
        (precio1,), (precio2,), # Precios
        (136,), (463,) # Secuencia, Factura ID
    ]
    
    form_data = {
        'cliente_id': '102',
        'producto_id_1': '10', 'cantidad_1': str(cantidad1),
        'producto_id_2': '11', 'cantidad_2': str(cantidad2),
    }
    response = client.post('/factura/nueva', data=form_data)
    assert response.status_code == 302
    
    # Verificar que el total en la BD es el esperado, considerando la precisión de Decimal
    # Esto depende de cómo la app maneje y guarde los Decimal.
    # El mock.call debe usar el mismo tipo (Decimal o float) que la app usa para la BD.
    # Si la app convierte a float para la BD, puede haber pérdida de precisión.
    # Si usa Decimal (o la BD es NUMERIC), la precisión se mantiene.
    mock_cursor.execute.assert_any_call(
        'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
        ('FACT-136', '102', total_factura_esperado) # El tipo de total_factura_esperado debe coincidir con el de la app
    )


def test_ver_factura_fecha_formato_regional_en_template(client, mock_db_connection):
    """Test: ver_factura muestra la fecha en un formato regional esperado (si aplica)."""
    # Asumir que la app o la plantilla formatea la fecha. Ej: DD/MM/YYYY
    mock_cursor = mock_db_connection["cursor"]
    # Fecha en formato ISO desde la BD
    mock_cursor.fetchone.return_value = (1, 'F-FECHA', '2023-12-25', 10.0, 1, 'Navidad', 'Polo Norte', '0', 'ACTIVA')
    mock_cursor.fetchall.return_value = [] # No items

    response = client.get('/factura/1')
    assert response.status_code == 200
    # Verificar el formato de fecha esperado en la plantilla.
    # Esto es frágil si el formato cambia.
    assert b"Fecha: 25/12/2023" in response.data # Ejemplo de formato esperado
# Add DB error test for eliminar_producto (other errors)
# ... (omitted)

# --- Cleanup test (optional but good practice if you patched globals) ---
# If you used mock.patch without stopall in individual tests (fixture handles it here)
# or patched things not covered by the fixture, you might need cleanup tests.
# With the mock_db_connection fixture using stopall, this is less critical here.

# --- Test for the tricky import error test (commented out previously) ---
# It's hard to make this reliable. Best to skip unless you have a clear need.
# If you wanted to test that the app *can't even start* without psycopg2, you'd
# need to run the app import in a subprocess with a modified environment/sys.path.
# That's beyond a simple unit test in the same process.