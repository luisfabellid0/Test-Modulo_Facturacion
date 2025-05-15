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