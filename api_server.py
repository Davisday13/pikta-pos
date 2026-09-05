import sqlite3
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import json
import logging
from datetime import datetime, timedelta
import socket
import threading
import time
import difflib
import re
import os
import hashlib
import secrets
import jwt
from functools import wraps
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
try:
    import win32print
    WIN32PRINT_AVAILABLE = True
except ImportError:
    WIN32PRINT_AVAILABLE = False

JWT_SECRET = os.environ.get('PIKTA_JWT_SECRET', secrets.token_urlsafe(32))
JWT_EXPIRES_HOURS = 24

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "PIk'TADB.db")
SYSTEM_VERSION = "1.0.0"
ADS_DIR = os.path.join(BASE_DIR, 'Imagenes', 'publicidad')
IMG_DIR = os.path.join(BASE_DIR, 'Imagenes')
ERROR_LOG_PATH = os.path.join(BASE_DIR, 'error_log.txt')
SQL_PROXY_TOKEN = os.environ.get('PIKTA_SQL_TOKEN', '') or ''

if not os.path.exists(ADS_DIR):
    try:
        os.makedirs(ADS_DIR)
    except Exception:
        pass

class PasswordManager:
    PBKDF2_ITERATIONS = 100000
    BCRYPT_ROUNDS = 10
    PEPPER = os.environ.get('PIKTA_PEPPER', '')

    @staticmethod
    def verify_password_advanced(provided_password, stored_hash):
        if not stored_hash:
            return False
        pepper = PasswordManager.PEPPER
        if stored_hash.startswith('1|'):
            try:
                _, salt, hash_value = stored_hash.split('|')
                pwd_pepper = provided_password + pepper
                hash_obj = hashlib.pbkdf2_hmac('sha256', pwd_pepper.encode(), salt.encode(), PasswordManager.PBKDF2_ITERATIONS)
                return hash_obj.hex() == hash_value
            except Exception:
                return False
        if stored_hash.startswith('2|'):
            if not BCRYPT_AVAILABLE:
                return False
            try:
                version, salt_hex, bcrypt_hash = stored_hash.split('|')
                pwd_pepper = provided_password + pepper
                salt = bytes.fromhex(salt_hex)
                kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PasswordManager.PBKDF2_ITERATIONS)
                pbkdf2_hash = kdf.derive(pwd_pepper.encode())
                return bcrypt.checkpw(pbkdf2_hash, bcrypt_hash.encode())
            except Exception:
                return False
        if ':' in stored_hash:
            try:
                salt, hash_value = stored_hash.split(':')
                hash_obj = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 100000)
                return hash_obj.hex() == hash_value
            except Exception:
                return False
        return False

    @staticmethod
    def hash_password(password):
        salt = os.urandom(16)
        pwd_pepper = password + PasswordManager.PEPPER
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PasswordManager.PBKDF2_ITERATIONS)
        pbkdf2_hash = kdf.derive(pwd_pepper.encode())
        if BCRYPT_AVAILABLE:
            bcrypt_hash = bcrypt.hashpw(pbkdf2_hash, bcrypt.gensalt(PasswordManager.BCRYPT_ROUNDS))
            return f"2|{salt.hex()}|{bcrypt_hash.decode()}"
        return f"1|{salt.hex()}|{hashlib.pbkdf2_hmac('sha256', pwd_pepper.encode(), salt, PasswordManager.PBKDF2_ITERATIONS).hex()}"

app = Flask(__name__)

JWT_ORIGINS_STR = os.environ.get('PIKTA_CORS_ORIGINS', 'http://localhost:5000,https://*')
CORS_ORIGINS = [o.strip() for o in JWT_ORIGINS_STR.split(',') if o.strip() and o.strip() != '*']
if '*' in JWT_ORIGINS_STR:
    CORS(app)
else:
    CORS(app, origins=CORS_ORIGINS, supports_credentials=True, methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def make_error(status_code, message, log_msg=None):
    if log_msg:
        logging.error(log_msg)
    return jsonify({"status": "error", "message": message}), status_code

def sanitize_path(user_path, base_dir):
    allowed = os.path.realpath(base_dir)
    requested = os.path.realpath(os.path.join(base_dir, user_path))
    if not requested.startswith(allowed):
        return None
    return requested

def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return make_error(401, 'Token requerido')
        token = auth.split(' ')[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            request.current_user = payload
        except jwt.ExpiredSignatureError:
            return make_error(401, 'Token expirado')
        except jwt.InvalidTokenError:
            return make_error(401, 'Token inválido')
        return f(*args, **kwargs)
    return decorated

def create_jwt(user_id, username, rol, nombre_completo):
    payload = {
        'user_id': user_id,
        'username': username,
        'rol': rol,
        'nombre_completo': nombre_completo,
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRES_HOURS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

# ─── Ruta raíz ──────────────────────────────────────────

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "success",
        "service": "PIK'TA POS API",
        "version": SYSTEM_VERSION,
        "endpoints": {
            "version": "/api/version",
            "login": "/api/login",
            "menu": "/api/menu",
            "usuarios": "/api/usuarios",
            "pedidos": "/api/pedidos",
            "inventario": "/api/inventario",
            "cierres": "/api/cierres",
            "caja": "/api/caja",
            "seguridad": "/api/seguridad",
            "publicidad": "/api/publicidad",
            "images": "/api/images/<nombre>",
            "sql_proxy": "/api/sql/proxy"
        },
        "docs": "API REST para sistema POS de restaurante",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        return jsonify({
            "status": "success",
            "message": "Servidor funcionando correctamente",
            "version": SYSTEM_VERSION,
            "database": "connected",
            "tables": [t[0] for t in tables],
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error de conexion: {str(e)}"
        }), 500

# ─── Endpoints Públicos ─────────────────────────────────

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        totp_code = data.get('totp_code')

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password, rol, nombre_completo, two_factor_secret FROM usuarios WHERE username = ?", (username,))
        user = cur.fetchone()
        conn.close()

        if user and PasswordManager.verify_password_advanced(password, user['password']):
            if user['rol'] == 'Administrador':
                import pyotp
                secret = user['two_factor_secret']
                if secret:
                    totp = pyotp.TOTP(secret)
                    if not totp_code or not totp.verify(totp_code):
                        return make_error(401, 'Código 2FA requerido o incorrecto')

            token = create_jwt(user['id'], user['username'], user['rol'], user['nombre_completo'])
            user_data = dict(user)
            del user_data['password']
            return jsonify({"status": "success", "token": token, "user": user_data})
        else:
            return make_error(401, 'Usuario o contraseña incorrectos')
    except Exception:
        return make_error(500, 'Error interno del servidor', 'Error en /api/login')

@app.route('/api/version', methods=['GET'])
def get_version():
    return jsonify({"status": "success", "version": SYSTEM_VERSION, "update_required": False})

# ─── Endpoints Protegidos ────────────────────────────────

@app.route('/api/caja/abrir_cajon', methods=['POST'])
@jwt_required
def api_abrir_cajon():
    if not WIN32PRINT_AVAILABLE:
        return make_error(501, 'win32print no disponible en este servidor')
    try:
        printer_name = win32print.GetDefaultPrinter()
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hPrinter, 1, ("Pikta Remote Drawer", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, b'\x1b\x70\x00\x19\xfa')
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
            return jsonify({"status": "success", "message": "Cajón abierto"})
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception as e:
        return make_error(500, 'Error al abrir el cajón', f'Error abriendo cajón: {e}')

@app.route('/api/sql/proxy', methods=['POST'])
@jwt_required
def sql_proxy():
    user = request.current_user
    if user.get('rol') != 'Administrador':
        return make_error(403, 'Solo administradores pueden ejecutar SQL')
    data = request.json
    query = data.get('query')
    params = data.get('params', [])
    fetch = data.get('fetch', False)

    forbidden = ['drop', 'truncate', 'alter']
    if any(kw in query.lower() for kw in forbidden):
        return make_error(403, 'Operación no permitida')

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(query, params)
        result = None
        columns = []
        if fetch:
            rows = cur.fetchall()
            if rows:
                columns = list(rows[0].keys())
                result = [list(row) for row in rows]
        last_id = cur.lastrowid
        row_count = conn.total_changes
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "columns": columns, "result": result, "lastrowid": last_id, "changes": row_count})
    except Exception as e:
        return make_error(500, 'Error ejecutando consulta SQL', f'Error en SQL Proxy: {e}')

@app.route('/api/menu', methods=['GET'])
@jwt_required
def get_menu():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM productos_menu ORDER BY id DESC")
        productos = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "data": productos})
    except Exception as e:
        return make_error(500, 'Error al obtener menú', f'Error en /api/menu GET: {e}')

@app.route('/api/menu', methods=['POST'])
@jwt_required
def create_menu_item():
    try:
        data = request.json
        nombre = data.get('nombre')
        precio = data.get('precio', 0.0)
        categoria = data.get('categoria', 'Otros')
        emoji = data.get('emoji', '')
        prep = data.get('prep_duration', 15)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO productos_menu (nombre, descripcion, precio, categoria, emoji, prep_duration, disponible) VALUES (?, '', ?, ?, ?, ?, 1)",
                    (nombre, precio, categoria, emoji, prep))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Producto creado"}), 201
    except Exception as e:
        return make_error(500, 'Error al crear producto', f'Error creando producto: {e}')

@app.route('/api/menu/<int:product_id>', methods=['DELETE'])
@jwt_required
def delete_menu_item(product_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM productos_menu WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Producto eliminado"})
    except Exception as e:
        return make_error(500, 'Error al eliminar producto', f'Error eliminando producto: {e}')

@app.route('/api/usuarios', methods=['GET'])
@jwt_required
def get_usuarios():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, rol, nombre_completo FROM usuarios")
        usuarios = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "data": usuarios})
    except Exception as e:
        return make_error(500, 'Error al obtener usuarios', f'Error en /api/usuarios GET: {e}')

@app.route('/api/usuarios', methods=['POST'])
@jwt_required
def create_usuario():
    if request.current_user.get('rol') != 'Administrador':
        return make_error(403, 'Solo administradores pueden crear usuarios')
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        rol = data.get('rol', 'Mesero')
        nombre = data.get('nombre_completo', username)
        if not username or not password:
            return make_error(400, 'Faltan credenciales')
        hashed = PasswordManager.hash_password(password)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO usuarios (username, password, rol, nombre_completo) VALUES (?, ?, ?, ?)",
                    (username, hashed, rol, nombre))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Usuario creado"}), 201
    except Exception as e:
        return make_error(500, 'Error al crear usuario', f'Error en /api/usuarios POST: {e}')

@app.route('/api/usuarios/<int:user_id>', methods=['PUT', 'DELETE'])
@jwt_required
def manage_usuario(user_id):
    if request.current_user.get('rol') != 'Administrador':
        return make_error(403, 'Solo administradores pueden modificar usuarios')
    try:
        conn = get_db()
        cur = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Usuario eliminado"})
        elif request.method == 'PUT':
            data = request.json
            rol = data.get('rol')
            nombre = data.get('nombre_completo')
            cur.execute("UPDATE usuarios SET rol = ?, nombre_completo = ? WHERE id = ?", (rol, nombre, user_id))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Usuario actualizado"})
    except Exception as e:
        return make_error(500, 'Error al modificar usuario', f'Error en /api/usuarios PUT/DELETE: {e}')

@app.route('/api/inventario', methods=['GET'])
@jwt_required
def get_inventario():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, ingrediente, cantidad, unidad, stock_minimo FROM inventario")
        inventario = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "data": inventario})
    except Exception as e:
        return make_error(500, 'Error al obtener inventario', f'Error en /api/inventario GET: {e}')

@app.route('/api/inventario/<int:item_id>', methods=['PUT', 'DELETE'])
@jwt_required
def manage_inventario_item(item_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        if request.method == 'DELETE':
            cur.execute("DELETE FROM inventario WHERE id = ?", (item_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Item eliminado"})
        elif request.method == 'PUT':
            data = request.json
            cantidad = data.get('cantidad')
            minimo = data.get('stock_minimo')
            cur.execute("UPDATE inventario SET cantidad = ?, stock_minimo = ? WHERE id = ?", (cantidad, minimo, item_id))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Inventario actualizado"})
    except Exception as e:
        return make_error(500, 'Error al modificar inventario', f'Error en /api/inventario PUT/DELETE: {e}')

@app.route('/api/cierres', methods=['GET'])
@jwt_required
def get_cierres():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM caja_sesiones ORDER BY id DESC LIMIT 50")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "data": rows})
    except Exception as e:
        return make_error(500, 'Error al obtener cierres', f'Error en /api/cierres: {e}')

@app.route('/api/cierres/<int:sesion_id>', methods=['GET'])
@jwt_required
def get_cierre_detalle(sesion_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT cs.*, u.nombre_completo as cajero_nombre, u.username
            FROM caja_sesiones cs
            LEFT JOIN usuarios u ON cs.usuario_id = u.id
            WHERE cs.id = ?
        """, (sesion_id,))
        sesion = cur.fetchone()
        if not sesion:
            conn.close()
            return make_error(404, 'Sesión no encontrada')
        cur.execute("""
            SELECT numero, total, metodo_pago, created_at
            FROM pedidos
            WHERE sesion_id = ? AND pagado = 1
        """, (sesion_id,))
        tickets = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "sesion": dict(sesion), "tickets": tickets})
    except Exception as e:
        return make_error(500, 'Error al obtener detalle del cierre', f'Error en /api/cierres/detalle: {e}')

@app.route('/api/pedidos', methods=['GET'])
@jwt_required
def get_pedidos():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos WHERE estado NOT IN ('COBRADO', 'ENTREGADO', 'LISTO', 'CANCELADO') ORDER BY id DESC")
        pedidos = []
        for row in cur.fetchall():
            p = dict(row)
            try:
                p['items'] = json.loads(p['items'])
            except Exception:
                pass
            pedidos.append(p)
        conn.close()
        return jsonify({"status": "success", "data": pedidos})
    except Exception as e:
        return make_error(500, 'Error al obtener pedidos', f'Error en /api/pedidos GET: {e}')

@app.route('/api/pedidos/historial', methods=['GET'])
@jwt_required
def get_pedidos_historial():
    try:
        search = request.args.get('search', '').strip()
        fecha = request.args.get('fecha', '').strip()
        conn = get_db()
        cur = conn.cursor()
        query = "SELECT * FROM pedidos WHERE pagado = 1"
        params = []
        if search:
            query += " AND (numero LIKE ? OR mesa LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        if fecha:
            query += " AND created_at LIKE ?"
            params.append(f"{fecha}%")
        query += " ORDER BY created_at DESC LIMIT 200"
        cur.execute(query, params)
        pedidos = []
        for row in cur.fetchall():
            p = dict(row)
            if isinstance(p.get('items'), str):
                try:
                    p['items'] = json.loads(p['items'])
                except Exception:
                    pass
            pedidos.append(p)
        conn.close()
        return jsonify({"status": "success", "data": pedidos})
    except Exception as e:
        return make_error(500, 'Error al obtener historial', f'Error en /api/pedidos/historial: {e}')

@app.route('/api/pedidos', methods=['POST'])
@jwt_required
def create_pedido():
    try:
        data = request.json
        items = data.get('items', [])
        mesa = data.get('mesa', 'Mesa General')
        total = data.get('total', 0)
        numero = f"PED-MOV-{int(datetime.now().timestamp())}"
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO pedidos (numero, items, subtotal, total, estado, canal, mesa, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (numero, json.dumps(items), total, total, 'RECIBIDO', 'MESERO', mesa, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Pedido creado", "numero": numero})
    except Exception as e:
        return make_error(500, 'Error al crear pedido', f'Error en /api/pedidos POST: {e}')

@app.route('/api/pedidos/<int:pedido_id>', methods=['PUT'])
@jwt_required
def update_pedido(pedido_id):
    try:
        data = request.json
        nuevo_estado = data.get('estado')
        if not nuevo_estado:
            return make_error(400, "Falta el campo 'estado'")
        conn = get_db()
        cur = conn.cursor()
        if nuevo_estado == 'PREPARANDO':
            inicio = datetime.now().isoformat()
            cur.execute("UPDATE pedidos SET estado = ?, preparacion_inicio = ? WHERE id = ?", (nuevo_estado, inicio, pedido_id))
        else:
            cur.execute("UPDATE pedidos SET estado = ? WHERE id = ?", (nuevo_estado, pedido_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Pedido {pedido_id} actualizado a {nuevo_estado}"})
    except Exception as e:
        return make_error(500, 'Error al actualizar pedido', f'Error en /api/pedidos PUT: {e}')

@app.route('/api/pedidos/<int:pedido_id>/extras', methods=['POST'])
@jwt_required
def update_pedido_extras(pedido_id):
    try:
        data = request.json
        nuevos_items = data.get('items', [])
        nuevo_total_adicional = float(data.get('total', 0))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT items, total FROM pedidos WHERE id = ?", (pedido_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return make_error(404, 'Pedido no encontrado')
        items_actuales = json.loads(row['items'])
        total_actual = float(row['total'])
        items_actuales.extend(nuevos_items)
        total_final = total_actual + nuevo_total_adicional
        cur.execute("UPDATE pedidos SET items = ?, total = ?, estado = 'RECIBIDO' WHERE id = ?",
                    (json.dumps(items_actuales), total_final, pedido_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Productos agregados correctamente"})
    except Exception as e:
        return make_error(500, 'Error al agregar extras', f'Error en /api/pedidos EXTRAS: {e}')

@app.route('/api/pedidos/pendientes', methods=['GET'])
@jwt_required
def get_pedidos_pendientes():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, numero, mesa, total, created_at, items, canal, estado
            FROM pedidos
            WHERE pagado = 0 AND canal IN ('MESERO', 'LLEVAR', 'Móvil', 'PED-MOV')
            ORDER BY created_at DESC
        """)
        pedidos = []
        for row in cur.fetchall():
            p = dict(row)
            try:
                p['items'] = json.loads(p['items'])
            except Exception:
                pass
            pedidos.append(p)
        conn.close()
        return jsonify({"status": "success", "data": pedidos})
    except Exception as e:
        return make_error(500, 'Error al obtener pedidos pendientes', f'Error en /api/pedidos/pendientes: {e}')

@app.route('/api/caja/abrir', methods=['POST'])
@jwt_required
def abrir_caja():
    try:
        data = request.json
        usuario_id = data.get('usuario_id')
        monto_inicial = data.get('monto_inicial', 0.0)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM caja_sesiones WHERE usuario_id = ? AND estado = 'ABIERTO'", (usuario_id,))
        existente = cur.fetchone()
        if existente:
            conn.close()
            return jsonify({"status": "success", "message": "Sesión de caja recuperada", "sesion_id": existente['id']})
        cur.execute("SELECT id FROM caja_sesiones WHERE estado = 'ABIERTO' ORDER BY id DESC LIMIT 1")
        sesion_activa = cur.fetchone()
        if sesion_activa:
            conn.close()
            return make_error(400, 'Ya hay una caja abierta')
        apertura_at = datetime.now().isoformat()
        cur.execute(
            "INSERT INTO caja_sesiones (usuario_id, inicio, inicial, monto_apertura, estado) VALUES (?,?,?,?,?)",
            (usuario_id, apertura_at, monto_inicial, monto_inicial, 'ABIERTO'))
        conn.commit()
        sesion_id = cur.lastrowid
        conn.close()
        return jsonify({"status": "success", "message": "Caja abierta", "sesion_id": sesion_id})
    except Exception as e:
        return make_error(500, 'Error al abrir caja', f'Error en /api/caja/abrir: {e}')

@app.route('/api/caja/cerrar', methods=['POST'])
@jwt_required
def cerrar_caja():
    try:
        data = request.json
        sesion_id = data.get('sesion_id')
        conn = get_db()
        cur = conn.cursor()
        if not sesion_id:
            cur.execute("SELECT id FROM caja_sesiones WHERE estado = 'ABIERTO' ORDER BY id DESC LIMIT 1")
            sesion = cur.fetchone()
            if not sesion:
                conn.close()
                return make_error(400, 'No hay caja abierta')
            sesion_id = sesion['id']
        cur.execute("SELECT numero, total, metodo_pago, created_at FROM pedidos WHERE sesion_id = ? AND pagado = 1", (sesion_id,))
        rows = cur.fetchall()
        sum_efectivo = sum(float(r['total'] or 0) for r in rows if r['metodo_pago'] == 'EFECTIVO')
        sum_yappy = sum(float(r['total'] or 0) for r in rows if r['metodo_pago'] == 'YAPPY')
        sum_tarjeta = sum(float(r['total'] or 0) for r in rows if r['metodo_pago'] == 'TARJETA')
        sum_otros = sum_yappy + sum_tarjeta
        sum_total = sum_efectivo + sum_otros
        cur.execute("SELECT inicial FROM caja_sesiones WHERE id = ?", (sesion_id,))
        caja_row = cur.fetchone()
        inicial = float(caja_row['inicial'] or 0) if caja_row else 0.0
        cierre_at = datetime.now().isoformat()
        cur.execute("UPDATE caja_sesiones SET estado='CERRADO', cierre_total=?, cierre_at=? WHERE id=?", (sum_total, cierre_at, sesion_id))
        conn.commit()
        conn.close()
        return jsonify({
            "status": "success", "message": "Caja cerrada",
            "reporte": {
                "sesion_id": sesion_id, "tickets": len(rows),
                "monto_inicial": inicial, "efectivo": round(sum_efectivo, 2),
                "yappy": round(sum_yappy, 2), "tarjeta": round(sum_tarjeta, 2),
                "total_ventas": round(sum_total, 2), "total_en_caja": round(sum_efectivo + inicial, 2),
                "cierre_at": cierre_at
            }})
    except Exception as e:
        return make_error(500, 'Error al cerrar caja', f'Error en /api/caja/cerrar: {e}')

@app.route('/api/caja/crear_pedido_caja', methods=['POST'])
@jwt_required
def crear_pedido_caja():
    try:
        data = request.json
        items = data.get('items', [])
        mesa = data.get('mesa', 'CAJA')
        total = float(data.get('total', 0))
        canal = data.get('canal', 'CAJA')
        sesion_id = data.get('sesion_id')
        usuario_id = data.get('usuario_id')
        cliente_nombre = data.get('cliente_nombre', '')
        metodo_pago = data.get('metodo_pago', 'EFECTIVO')
        numero = f"{canal[:3].upper()}-{int(datetime.now().timestamp())}"
        pagado = data.get('pagado', 1 if canal == 'CAJA' else 0)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pedidos (numero, cliente_nombre, items, subtotal, total,
                                 estado, canal, mesa, sesion_id, usuario_id,
                                 metodo_pago, pagado, created_at)
            VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?)
        """, (numero, cliente_nombre, json.dumps(items), total, total,
              'RECIBIDO', canal, mesa, sesion_id, usuario_id,
              metodo_pago, pagado, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Pedido creado", "numero": numero})
    except Exception as e:
        return make_error(500, 'Error al crear pedido de caja', f'Error en /api/caja/crear_pedido_caja: {e}')

@app.route('/api/pedidos/cobrar/<int:pedido_id>', methods=['POST'])
@jwt_required
def cobrar_pedido(pedido_id):
    try:
        data = request.json
        metodo = data.get('metodo_pago', 'EFECTIVO')
        sesion_id = data.get('sesion_id')
        conn = get_db()
        cur = conn.cursor()
        if not sesion_id:
            cur.execute("SELECT id FROM caja_sesiones WHERE estado = 'ABIERTO' ORDER BY id DESC LIMIT 1")
            sesion = cur.fetchone()
            sesion_id = sesion['id'] if sesion else None
        cur.execute("UPDATE pedidos SET pagado = 1, metodo_pago = ?, sesion_id = ? WHERE id = ?", (metodo, sesion_id, pedido_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Pedido {pedido_id} cobrado"})
    except Exception as e:
        return make_error(500, 'Error al cobrar pedido', f'Error en /api/pedidos/cobrar: {e}')

@app.route('/api/caja/activa/<int:usuario_id>', methods=['GET'])
@jwt_required
def check_caja_activa(usuario_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM caja_sesiones WHERE usuario_id = ? AND estado = 'ABIERTO' ORDER BY id DESC LIMIT 1", (usuario_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({"status": "success", "sesion_id": row['id']})
        return jsonify({"status": "not_found"})
    except Exception as e:
        return make_error(500, 'Error al verificar caja activa', f'Error en /api/caja/activa: {e}')

@app.route('/api/cierres/historial', methods=['GET'])
@jwt_required
def get_cierres_historial():
    try:
        fecha = request.args.get('fecha', '').strip()
        conn = get_db()
        cur = conn.cursor()
        query = "SELECT * FROM cierres_caja"
        params = []
        if fecha:
            query += " WHERE fecha LIKE ?"
            params.append(f"{fecha}%")
        query += " ORDER BY id DESC LIMIT 100"
        cur.execute(query, params)
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"status": "success", "data": rows})
    except Exception as e:
        return make_error(500, 'Error al obtener historial de cierres', f'Error en /api/cierres/historial: {e}')

@app.route('/api/menu/upload_image', methods=['POST'])
@jwt_required
def upload_product_image():
    try:
        if 'file' not in request.files or 'product_name' not in request.form:
            return make_error(400, 'Faltan datos (archivo o nombre de producto)')
        file = request.files['file']
        product_name = request.form['product_name']
        if file.filename == '':
            return make_error(400, 'Nombre de archivo vacío')
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.avif']:
            return make_error(400, 'Formato de imagen no soportado')
        def normalize(t): return re.sub(r'[^a-zA-Z0-9]', '', str(t)).lower()
        norm_n = normalize(product_name)
        dest_path = os.path.join(IMG_DIR, f"{norm_n}{ext}")
        safe_path = sanitize_path(f"{norm_n}{ext}", IMG_DIR)
        if not safe_path:
            return make_error(400, 'Ruta inválida')
        for f in os.listdir(IMG_DIR):
            if normalize(os.path.splitext(f)[0]) == norm_n:
                existing_path = os.path.join(IMG_DIR, f)
                safe_existing = sanitize_path(f, IMG_DIR)
                if safe_existing:
                    os.remove(safe_existing)
        file.save(safe_path)
        return jsonify({"status": "success", "message": f"Imagen para {product_name} guardada correctamente"})
    except Exception as e:
        return make_error(500, 'Error al subir imagen', f'Error subiendo imagen: {e}')

@app.route('/api/publicidad/upload', methods=['POST'])
@jwt_required
def upload_ad():
    try:
        if 'file' not in request.files:
            return make_error(400, 'No hay archivo en la petición')
        file = request.files['file']
        if file.filename == '':
            return make_error(400, 'Nombre de archivo vacío')
        if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif', '.mp4', '.avi', '.mov')):
            return make_error(400, 'Formato no permitido')
        safe_path = sanitize_path(file.filename, ADS_DIR)
        if not safe_path:
            return make_error(400, 'Ruta inválida')
        file.save(safe_path)
        return jsonify({"status": "success", "message": f"Archivo {file.filename} subido correctamente"})
    except Exception as e:
        return make_error(500, 'Error al subir archivo', f'Error subiendo publicidad: {e}')

@app.route('/api/publicidad', methods=['GET'])
@jwt_required
def get_ads_list():
    try:
        images = []
        if os.path.exists(ADS_DIR):
            for f in os.listdir(ADS_DIR):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif', '.mp4', '.avi', '.mov')):
                    images.append(f)
        return jsonify({"status": "success", "images": images})
    except Exception as e:
        return make_error(500, 'Error al listar publicidad', f'Error en /api/publicidad: {e}')

@app.route('/api/publicidad/<path:nombre>', methods=['GET'])
@jwt_required
def get_ad_image(nombre):
    safe_path = sanitize_path(nombre, ADS_DIR)
    if safe_path and os.path.exists(safe_path):
        return send_file(safe_path)
    return make_error(404, 'Imagen no encontrada')

@app.route('/api/publicidad/delete/<nombre>', methods=['DELETE'])
@jwt_required
def delete_ad(nombre):
    safe_path = sanitize_path(nombre, ADS_DIR)
    if not safe_path:
        return make_error(400, 'Ruta inválida')
    if os.path.exists(safe_path):
        try:
            os.remove(safe_path)
            return jsonify({"status": "success", "message": "Archivo eliminado"})
        except Exception as e:
            return make_error(500, 'Error al eliminar archivo', f'Error eliminando publicidad: {e}')
    return make_error(404, 'Archivo no encontrado')

@app.route('/api/images/<path:nombre>', methods=['GET'])
@jwt_required
def get_image(nombre):
    safe_path = sanitize_path(nombre, IMG_DIR)
    if safe_path and os.path.exists(safe_path):
        return send_file(safe_path)
    safe_result = find_product_image_path(nombre)
    if safe_result:
        return send_file(safe_result)
    return make_error(404, 'Imagen no encontrada')

@app.route('/api/seguridad', methods=['GET'])
@jwt_required
def get_seguridad():
    if request.current_user.get('rol') != 'Administrador':
        return make_error(403, 'Solo administradores pueden ver registros de seguridad')
    try:
        if os.path.exists(ERROR_LOG_PATH):
            with open(ERROR_LOG_PATH, 'r', encoding='utf-8') as f:
                lines = f.readlines()[-50:]
            return jsonify({"status": "success", "data": "".join(lines)})
        return jsonify({"status": "success", "data": "No hay registros de seguridad."})
    except Exception as e:
        return make_error(500, 'Error al leer registros', f'Error en /api/seguridad: {e}')

# ─── Funciones Auxiliares ────────────────────────────────

def find_product_image_path(product_name):
    if not os.path.exists(IMG_DIR):
        return None
    def normalize(t): return re.sub(r'[^a-zA-Z0-9]', '', str(t)).lower()
    target = normalize(product_name)
    files_map = {}
    for f in os.listdir(IMG_DIR):
        safe_f = sanitize_path(f, IMG_DIR)
        if safe_f and f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif')):
            base_name = os.path.splitext(f)[0]
            files_map[normalize(base_name)] = safe_f
    if target in files_map:
        return files_map[target]
    matches = difflib.get_close_matches(target, files_map.keys(), n=1, cutoff=0.5)
    if matches:
        return files_map[matches[0]]
    return None

# ─── Servidor ────────────────────────────────────────────

def udp_broadcast_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    local_ip = '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    bcast_ip = '255.255.255.255'
    if local_ip != '127.0.0.1':
        parts = local_ip.split('.')
        bcast_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    message = json.dumps({"pikta_server": True, "port": 5000, "version": SYSTEM_VERSION, "ip": local_ip}).encode('utf-8')
    while True:
        try:
            sock.sendto(message, ('255.255.255.255', 5005))
            if bcast_ip != '255.255.255.255':
                sock.sendto(message, (bcast_ip, 5005))
            time.sleep(2)
        except Exception as e:
            if "10065" not in str(e):
                logging.error(f"Error en UDP Broadcast: {e}")
            time.sleep(5)

def init_db():
    """Inicializa las tablas necesarias en la base de datos (para Render y otros servidores)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    cur.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        rol TEXT NOT NULL,
        nombre_completo TEXT,
        two_factor_secret TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS productos_menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        precio REAL NOT NULL,
        categoria TEXT,
        emoji TEXT,
        disponible BOOLEAN DEFAULT 1,
        imagen_url TEXT,
        created_at TEXT,
        prep_duration INTEGER DEFAULT 15
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT UNIQUE NOT NULL,
        cliente_telefono TEXT,
        cliente_nombre TEXT,
        items TEXT NOT NULL,
        subtotal REAL,
        descuento REAL DEFAULT 0,
        total REAL NOT NULL,
        estado TEXT DEFAULT 'RECIBIDO',
        canal TEXT,
        metodo_pago TEXT,
        pagado BOOLEAN DEFAULT 0,
        notas TEXT,
        mesa TEXT,
        sesion_id INTEGER,
        usuario_id INTEGER,
        created_at TEXT,
        updated_at TEXT,
        preparacion_inicio TEXT,
        preparacion_duracion INTEGER
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS inventario (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ingrediente TEXT NOT NULL UNIQUE,
        cantidad REAL NOT NULL DEFAULT 0,
        unidad TEXT NOT NULL,
        stock_minimo REAL NOT NULL DEFAULT 0,
        costo_unitario REAL,
        proveedor_id INTEGER,
        activo BOOLEAN DEFAULT 1,
        updated_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tabla TEXT NOT NULL,
        accion TEXT NOT NULL,
        usuario TEXT,
        detalles TEXT,
        datos_previos TEXT,
        datos_nuevos TEXT,
        fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS access_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        action TEXT,
        details TEXT,
        created_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS caja_sesiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER,
        monto_apertura REAL,
        monto_cierre REAL,
        fecha_apertura TEXT,
        fecha_cierre TEXT,
        estado TEXT DEFAULT 'ABIERTO',
        resumen_ventas TEXT,
        inicio TEXT,
        inicial REAL,
        cierre_total REAL,
        cierre_at TEXT,
        reporte_texto TEXT,
        ingresos_efectivo REAL,
        ingresos_otros REAL
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS cierres_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER,
        monto_inicial REAL,
        monto_final REAL,
        fecha_cierre TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        telefono TEXT,
        email TEXT,
        direccion TEXT,
        activo BOOLEAN DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS categorias_menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        orden INTEGER
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS recetas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_id INTEGER,
        ingrediente_id INTEGER,
        cantidad REAL
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telefono TEXT,
        nombre TEXT,
        pedidos_count INTEGER DEFAULT 0,
        ultimo_pedido TEXT,
        canal_registro TEXT,
        created_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS sistema_config (
        clave TEXT PRIMARY KEY,
        valor TEXT
    )''')

    # Insertar usuario admin por defecto si no existe
    cur.execute('SELECT id FROM usuarios WHERE username = ?', ('admin',))
    if not cur.fetchone():
        hashed_p = PasswordManager.hash_password('admin')
        cur.execute('INSERT INTO usuarios (username, password, rol, nombre_completo) VALUES (?, ?, ?, ?)',
                    ('admin', hashed_p, 'Administrador', 'Administrador Sistema'))

    # Insertar productos de prueba si la tabla esta vacia
    cur.execute('SELECT COUNT(*) FROM productos_menu')
    if cur.fetchone()[0] == 0:
        test_products = [
            ("Hamburguesa Clasica", 8.50, "Combos", ""),
            ("Pizza Pepperoni", 12.00, "Combos", ""),
            ("Papas Fritas XL", 4.50, "Extras", ""),
            ("Alitas BBQ (6 unidades)", 7.25, "Extras", ""),
            ("Coca Cola 600ml", 2.00, "Bebidas", ""),
            ("Jugo de Naranja Natural", 3.50, "Bebidas", "")
        ]
        for nombre, precio, categoria, emoji in test_products:
            cur.execute('INSERT INTO productos_menu (nombre, precio, categoria, emoji, prep_duration) VALUES (?, ?, ?, ?, ?)',
                        (nombre, precio, categoria, emoji, 15))

    conn.commit()
    conn.close()
    print(f"Base de datos inicializada: {DB_NAME}")

def run_server():
    log = logging.getLogger('werkzeug')
    log.disabled = True
    app.logger.disabled = True
    print("Iniciando API Server...")
    
    # Inicializar base de datos
    init_db()
    
    if 'PYTHONANYWHERE_DOMAIN' not in os.environ:
        try:
            udp_thread = threading.Thread(target=udp_broadcast_server, daemon=True)
            udp_thread.start()
            print("Broadcast UDP iniciado.")
        except Exception as e:
            print(f"UDP no disponible: {e}")
    else:
        print("Modo Nube: UDP Broadcast omitido.")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)

if __name__ == '__main__':
    run_server()
