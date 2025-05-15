from flask import Flask, render_template, request, redirect, url_for
import psycopg2
from psycopg2 import sql

app = Flask(__name__)

# Configuración de la base de datos
DB_CONFIG = {
    'host': 'localhost',
    'database': 'facturacion_db',
    'user': 'postgres',
    'password': 'root'
}

def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    return conn

@app.route('/')
def index():
    return redirect(url_for('listar_facturas'))

@app.route('/facturas')
def listar_facturas():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT f.id, f.numero, f.fecha, c.nombre as cliente, f.total FROM facturas f JOIN clientes c ON f.cliente_id = c.id ORDER BY f.fecha DESC;')
    facturas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('facturas.html', facturas=facturas)

@app.route('/factura/nueva', methods=['GET', 'POST'])
def nueva_factura():
    if request.method == 'POST':
        # Obtener datos del formulario
        cliente_id = request.form['cliente_id']
        items = []
        total = 0
        
        # Procesar items
        for i in range(1, 6):  # Máximo 5 items por factura
            producto_id = request.form.get(f'producto_id_{i}')
            cantidad = request.form.get(f'cantidad_{i}')
            if producto_id and cantidad:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute('SELECT precio FROM productos WHERE id = %s;', (producto_id,))
                precio = cur.fetchone()[0]
                subtotal = float(precio) * float(cantidad)
                items.append({
                    'producto_id': producto_id,
                    'cantidad': cantidad,
                    'precio': precio,
                    'subtotal': subtotal
                })
                total += subtotal
                cur.close()
                conn.close()
        
        # Insertar factura con número generado
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Obtener el próximo número de factura de la secuencia
        cur.execute("SELECT nextval('factura_numero_seq')")
        numero_factura = f"FACT-{cur.fetchone()[0]}"
        
        cur.execute(
            'INSERT INTO facturas (numero, cliente_id, total) VALUES (%s, %s, %s) RETURNING id;',
            (numero_factura, cliente_id, total)
        )
        factura_id = cur.fetchone()[0]
        
        # Insertar items de factura
        for item in items:
            cur.execute(
                'INSERT INTO factura_items (factura_id, producto_id, cantidad, precio, subtotal) VALUES (%s, %s, %s, %s, %s);',
                (factura_id, item['producto_id'], item['cantidad'], item['precio'], item['subtotal'])
            )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return redirect(url_for('ver_factura', id=factura_id))
    
    else:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Obtener clientes
        cur.execute('SELECT id, nombre FROM clientes ORDER BY nombre;')
        clientes = cur.fetchall()
        
        # Obtener productos
        cur.execute('SELECT id, nombre, precio FROM productos ORDER BY nombre;')
        productos = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return render_template('nueva_factura.html', clientes=clientes, productos=productos)

@app.route('/factura/<int:id>')
def ver_factura(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Obtener factura
    cur.execute('''
        SELECT f.id, f.numero, f.fecha, f.total, c.id as cliente_id, c.nombre as cliente_nombre, 
               c.direccion as cliente_direccion, c.telefono as cliente_telefono
        FROM facturas f JOIN clientes c ON f.cliente_id = c.id WHERE f.id = %s;
    ''', (id,))
    factura = cur.fetchone()
    
    # Obtener items
    cur.execute('''
        SELECT fi.id, p.nombre as producto, fi.cantidad, fi.precio, fi.subtotal
        FROM factura_items fi JOIN productos p ON fi.producto_id = p.id
        WHERE fi.factura_id = %s;
    ''', (id,))
    items = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('ver_factura.html', factura=factura, items=items)

@app.route('/clientes')
def listar_clientes():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id, nombre, direccion, telefono, email FROM clientes ORDER BY nombre;')
    clientes = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('clientes.html', clientes=clientes)

@app.route('/agregar_cliente', methods=['GET', 'POST'])
def agregar_cliente():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        direccion = request.form.get('direccion')
        email = request.form.get('email')
        telefono = request.form.get('telefono')

        if not nombre or not direccion or not email or not telefono:
            return render_template('agregar_cliente.html', error="Todos los campos son obligatorios.")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO clientes (nombre, direccion, telefono, email) VALUES (%s, %s, %s, %s);",
            (nombre, direccion, telefono, email)
        )
        conn.commit()
        cur.close()
        conn.close()

        return redirect(url_for('listar_clientes'))

    return render_template('agregar_cliente.html')


@app.route('/eliminar_cliente/<int:id>', methods=['POST'])
def eliminar_cliente(id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Verificar si el cliente tiene facturas asociadas antes de eliminar
    cur.execute('SELECT COUNT(*) FROM facturas WHERE cliente_id = %s;', (id,))
    factura_count = cur.fetchone()[0]

    if factura_count > 0:
        # Obtener la lista de clientes para volver a mostrarla junto con el error
        cur.execute('SELECT * FROM clientes;')
        clientes = cur.fetchall()
        cur.close()
        conn.close()
        return render_template('clientes.html', clientes=clientes, error="No se puede eliminar el cliente porque tiene facturas asociadas.")

    # Eliminar el cliente si no tiene facturas asociadas
    cur.execute('DELETE FROM clientes WHERE id = %s;', (id,))
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect(url_for('listar_clientes'))


@app.route('/clientes/<int:id>/editar')
def editar_cliente(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM clientes WHERE id = %s;', (id,))
    cliente = cur.fetchone()
    cur.close()
    conn.close()

    if cliente is None:
        return "Cliente no encontrado", 404

    return render_template('editar_cliente.html', cliente=cliente)


@app.route('/clientes/<int:id>/actualizar', methods=['POST'])
def actualizar_cliente(id):
    nombre = request.form['nombre']
    direccion = request.form['direccion']
    telefono = request.form['telefono']
    email = request.form['email']

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clientes
        SET nombre = %s, direccion = %s, telefono = %s, email = %s
        WHERE id = %s;
    """, (nombre, direccion, telefono, email, id))
    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for('listar_clientes'))

@app.route('/productos')
def listar_productos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id, nombre, descripcion, precio FROM productos ORDER BY nombre;')
    productos = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('listar_productos.html', productos=productos)

@app.route('/productos/agregar', methods=['GET', 'POST'])
def agregar_producto():
    if request.method == 'POST':
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        precio = request.form['precio']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO productos (nombre, descripcion, precio) VALUES (%s, %s, %s);', 
                    (nombre, descripcion, precio))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('listar_productos'))

    return render_template('agregar_producto.html')

@app.route('/productos/editar/<int:id>', methods=['GET', 'POST'])
def editar_producto(id):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        precio = request.form['precio']

        cur.execute('UPDATE productos SET nombre = %s, descripcion = %s, precio = %s WHERE id = %s;',
                    (nombre, descripcion, precio, id))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('listar_productos'))

    cur.execute('SELECT id, nombre, descripcion, precio FROM productos WHERE id = %s;', (id,))
    producto = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('editar_producto.html', producto=producto)

@app.route('/productos/eliminar/<int:id>', methods=['POST'])
def eliminar_producto(id):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM productos WHERE id = %s;', (id,))
        conn.commit()
    except psycopg2.errors.ForeignKeyViolation:
        conn.rollback()
        # Obtener los productos para recargar la vista con error
        cur.execute('SELECT * FROM productos;')
        productos = cur.fetchall()
        error = "No se puede eliminar el producto porque se encuentra en una factura."
        return render_template('listar_productos.html', productos=productos, error=error)
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('listar_productos'))

if __name__ == '__main__':
    app.run(debug=True)