import psycopg2
from psycopg2 import sql

# Configuración de la base de datos
DB_CONFIG = {
    'host': 'localhost',
    'database': 'facturacion_db',
    'user': 'postgres',
    'password': 'root'
}

def create_tables():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(100) NOT NULL,
            direccion TEXT,
            telefono VARCHAR(20),
            email VARCHAR(100)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS productos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(100) NOT NULL,
            descripcion TEXT,
            precio DECIMAL(10, 2) NOT NULL,
            stock INTEGER DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS facturas (
            id SERIAL PRIMARY KEY,
            numero VARCHAR(20) NOT NULL UNIQUE,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cliente_id INTEGER NOT NULL,
            total DECIMAL(10, 2) NOT NULL,
            FOREIGN KEY (cliente_id) REFERENCES clientes (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS factura_items (
            id SERIAL PRIMARY KEY,
            factura_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            precio DECIMAL(10, 2) NOT NULL,
            subtotal DECIMAL(10, 2) NOT NULL,
            FOREIGN KEY (factura_id) REFERENCES facturas (id),
            FOREIGN KEY (producto_id) REFERENCES productos (id)
        )
        """,
        """
        CREATE SEQUENCE IF NOT EXISTS factura_numero_seq START WITH 1000
        """
    )
    
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Eliminar tablas si existen (solo para desarrollo)
        cur.execute("DROP TABLE IF EXISTS factura_items CASCADE")
        cur.execute("DROP TABLE IF EXISTS facturas CASCADE")
        cur.execute("DROP TABLE IF EXISTS productos CASCADE")
        cur.execute("DROP TABLE IF EXISTS clientes CASCADE")
        cur.execute("DROP SEQUENCE IF EXISTS factura_numero_seq")
        conn.commit()
        
        for command in commands:
            cur.execute(command)
        
        # Insertar datos de prueba
        insert_test_data(cur)
        
        conn.commit()
        cur.close()
        print("Tablas creadas y datos de prueba insertados correctamente.")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error al crear tablas: {error}")
    finally:
        if conn is not None:
            conn.close()

def insert_test_data(cur):
    # Verificar si ya hay datos
    cur.execute("SELECT COUNT(*) FROM clientes;")
    if cur.fetchone()[0] > 0:
        return
    
    # Insertar clientes
    clientes = [
        ("Cliente Uno", "Calle 123", "555-1234", "cliente1@example.com"),
        ("Cliente Dos", "Avenida 456", "555-5678", "cliente2@example.com"),
        ("Cliente Tres", "Boulevard 789", "555-9012", "cliente3@example.com")
    ]
    
    for cliente in clientes:
        cur.execute(
            "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
            cliente
        )
    
    # Insertar productos
    productos = [
        ("Producto A", "Descripción producto A", 10.50),
        ("Producto B", "Descripción producto B", 25.75),
        ("Producto C", "Descripción producto C", 5.99),
        ("Producto D", "Descripción producto D", 100.00),
        ("Producto E", "Descripción producto E", 15.25)
    ]
    
    for producto in productos:
        cur.execute(
            "INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);",
            producto
        )

if __name__ == '__main__':
    create_tables()