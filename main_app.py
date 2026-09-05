"""
main_app.py - Interfaz de escritorio estilo 'web' (Tkinter)

Este archivo contiene una versión de escritorio del panel del
restaurante (POS, KDS, Admin) adaptada desde la carpeta `web/`.

Componentes principales:
- `DatabaseManager`: Inicializa y gestiona la base de datos SQLite y migraciones.
- `LoginWindow`: Ventana modal para inicio de sesión con control de roles.
- `App`: Clase principal de la aplicación que gestiona el contenedor de pestañas (Notebook).
- `POSFrame`: Interfaz de Punto de Venta (Caja).
- `KDSFrame`: Monitor de Cocina para gestión de pedidos.
- `AdminFrame`: Panel administrativo para inventario y usuarios.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import PRIMARY, SUCCESS, DANGER, WARNING, INFO, LIGHT, DARK
import sqlite3
import json
import os
import webbrowser
import threading
import multiprocessing
try:
    import api_server
except ImportError:
    api_server = None

try:
    import webview
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False
from datetime import datetime, timedelta
import logging
import sys
import tempfile
import hashlib
import secrets
import time
import winsound
import shutil
import re
import base64
try:
    import win32print
    import win32api
    WIN32_PRINT_AVAILABLE = True
except ImportError:
    WIN32_PRINT_AVAILABLE = False

from printer_config import PrinterConfig, PrinterConfigDialog

# =============================================================================
# FUNCIONES DE IMPRESIÓN Y HARDWARE
# =============================================================================

def find_pos_printer(force_select=False):
    """Busca automáticamente o permite seleccionar la impresora térmica."""
    if not WIN32_PRINT_AVAILABLE: return None
    try:
        # Si ya se guardó una preferencia en esta sesión, usarla
        if hasattr(tk, "_selected_printer") and not force_select:
            return tk._selected_printer

        default = win32print.GetDefaultPrinter()
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
        printer_names = [p[2] for p in printers]

        # Si forzamos selección o no hay nada obvio, preguntar
        if force_select:
            from tkinter import simpledialog
            # Crear una pequeña ventana de selección
            win = tk.Toplevel()
            win.withdraw()
            msg = "Seleccione su impresora térmica:\n\n" + "\n".join([f"{i+1}. {n}" for i, n in enumerate(printer_names)])
            idx = simpledialog.askinteger("Configurar Impresora", msg, parent=win, minvalue=1, maxvalue=len(printer_names))
            win.destroy()
            if idx:
                tk._selected_printer = printer_names[idx-1]
                return tk._selected_printer

        # Intento automático
        for name in printer_names:
            n = name.upper()
            if any(k in n for k in ["POS", "THERMAL", "80MM", "58MM", "XP-80", "PRINTER", "RECEIPT"]):
                tk._selected_printer = name
                return name
        
        tk._selected_printer = default
        return default
    except Exception:
        return None

# =============================================================================
# FUNCIONES DE SONIDO Y NOTIFICACIÓN
# =============================================================================

def play_sound_startup():
    """Sonido de bienvenida al iniciar la app."""
    try:
        winsound.PlaySound('SystemAsterisk', winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def play_sound_error():
    """Sonido para errores del sistema."""
    try:
        winsound.PlaySound('SystemHand', winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def play_sound_new_order():
    """Sonido suave para nuevos pedidos entrantes."""
    try:
        winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def play_sound_order_ready():
    """Sonido de campana para cuando un pedido está listo (MP3 nativo)."""
    try:
        import ctypes
        import sys
        import os
        sound_path = os.path.abspath(os.path.join('Imagenes', 'sonidos y alertas', 'Sonido de_ Campana de servicio.mp3'))
        if os.path.exists(sound_path) and sys.platform == "win32":
            from ctypes import wintypes
            buffer = ctypes.create_unicode_buffer(260)
            ctypes.windll.kernel32.GetShortPathNameW(sound_path, buffer, 260)
            short_path = buffer.value
            ctypes.windll.winmm.mciSendStringW('close myalert', None, 0, None)
            ctypes.windll.winmm.mciSendStringW(f'open {short_path} type mpegvideo alias myalert', None, 0, None)
            ctypes.windll.winmm.mciSendStringW('play myalert', None, 0, None)
        else:
            winsound.Beep(880, 150)
            winsound.Beep(1046, 300)
    except Exception as e:
        logging.error(f"Error en play_sound_order_ready: {e}")

def play_sound_delayed_order():
    """Sonido de atención para cuando el tiempo de preparación de un pedido se acaba."""
    try:
        import ctypes
        import sys
        import os
        sound_path = os.path.abspath(os.path.join('Imagenes', 'sonidos y alertas', 'sonido de atencion.mp3'))
        if os.path.exists(sound_path) and sys.platform == "win32":
            from ctypes import wintypes
            buffer = ctypes.create_unicode_buffer(260)
            ctypes.windll.kernel32.GetShortPathNameW(sound_path, buffer, 260)
            short_path = buffer.value
            ctypes.windll.winmm.mciSendStringW('close delayed_alert', None, 0, None)
            ctypes.windll.winmm.mciSendStringW(f'open {short_path} type mpegvideo alias delayed_alert', None, 0, None)
            ctypes.windll.winmm.mciSendStringW('play delayed_alert repeat', None, 0, None)
        else:
            winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP)
    except Exception as e:
        logging.error(f"Error en play_sound_delayed_order: {e}")

def stop_sound_delayed_order():
    """Detiene el sonido de atención de pedido retrasado."""
    try:
        import ctypes
        import sys
        if sys.platform == "win32":
            ctypes.windll.winmm.mciSendStringW('close delayed_alert', None, 0, None)
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception as e:
        pass

# =============================================================================
# FUNCIONES DE SEGURIDAD (ENCRIPTACIÓN AVANZADA - FASE 3)
# =============================================================================
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    logging.warning("bcrypt no instalado. Usando fallback a hashlib.")

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

class PasswordManager:
    """Gestión avanzada de contraseñas con múltiples capas (PBKDF2 + Bcrypt/Fallback)."""
    PBKDF2_ITERATIONS = 100000 
    BCRYPT_ROUNDS = 10
    PEPPER = os.environ.get('PIKTA_PEPPER', 'default_pepper_change_me')
    
    @staticmethod
    def hash_password_advanced(password: str) -> str:
        pwd_pepper = password + PasswordManager.PEPPER
        
        if not BCRYPT_AVAILABLE:
            # Fallback seguro usando solo PBKDF2
            salt = secrets.token_hex(32)
            hash_obj = hashlib.pbkdf2_hmac('sha256', pwd_pepper.encode(), salt.encode(), 
                                          PasswordManager.PBKDF2_ITERATIONS)
            return f"1|{salt}|{hash_obj.hex()}"
        
        # Implementación normal con bcrypt
        salt = os.urandom(32)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, 
                        iterations=PasswordManager.PBKDF2_ITERATIONS)
        pbkdf2_hash = kdf.derive(pwd_pepper.encode())
        bcrypt_hash = bcrypt.hashpw(pbkdf2_hash, bcrypt.gensalt(rounds=PasswordManager.BCRYPT_ROUNDS))
        return f"2|{salt.hex()}|{bcrypt_hash.decode()}"
    
    @staticmethod
    def verify_password_advanced(provided_password: str, stored_hash: str) -> bool:
        if not stored_hash:
            return False
        
        # Verificar formato legacy (hashlib fallback o antiguo)
        if stored_hash.startswith('1|'):
            try:
                _, salt, hash_value = stored_hash.split('|')
                pwd_pepper = provided_password + PasswordManager.PEPPER
                hash_obj = hashlib.pbkdf2_hmac('sha256', pwd_pepper.encode(), salt.encode(), 
                                              PasswordManager.PBKDF2_ITERATIONS)
                return hash_obj.hex() == hash_value
            except Exception:
                return False
        
        # Formato con bcrypt
        if stored_hash.startswith('2|'):
            if not BCRYPT_AVAILABLE:
                logging.error("Se requiere bcrypt para verificar este hash")
                return False
            
            try:
                version, salt_hex, bcrypt_hash = stored_hash.split('|')
                pwd_pepper = provided_password + PasswordManager.PEPPER
                salt = bytes.fromhex(salt_hex)
                kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                                iterations=PasswordManager.PBKDF2_ITERATIONS)
                pbkdf2_hash = kdf.derive(pwd_pepper.encode())
                return bcrypt.checkpw(pbkdf2_hash, bcrypt_hash.encode())
            except Exception:
                return False
        
        # Formato antiguo (solo hashlib, sin pepper)
        if ':' in stored_hash:
            try:
                salt, hash_value = stored_hash.split(':')
                hash_obj = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 100000)
                return hash_obj.hex() == hash_value
            except Exception:
                return False
        
        return False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

class DataEncryption:
    """Encriptación de datos sensibles en la base de datos (Ej: Teléfonos)."""
    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)
    
    def _get_or_create_key(self) -> bytes:
        key_file = 'encryption.key'
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f: return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f: f.write(key)
            if sys.platform != 'win32': os.chmod(key_file, 0o600)
            return key
            
    def encrypt(self, data: str) -> str:
        if not data: return data
        return base64.b64encode(self.cipher.encrypt(data.encode())).decode()
        
    def decrypt(self, encrypted_data: str) -> str:
        if not encrypted_data: return encrypted_data
        try:
            decoded = base64.b64decode(encrypted_data.encode())
            return self.cipher.decrypt(decoded).decode()
        except Exception: return "*** ENCRYPTED ***"

class PasswordPolicy:
    """Políticas estrictas de contraseñas para nuevos usuarios."""
    MIN_LENGTH = 4
    @classmethod
    def validate(cls, pwd: str) -> tuple[bool, str]:
        if len(pwd) < cls.MIN_LENGTH: return False, f"Mínimo {cls.MIN_LENGTH} caracteres"
        return True, "OK"

# Funciones puente para no romper código antiguo
def hash_password(password): 
    return PasswordManager.hash_password_advanced(password)

def verify_password(stored_password, provided_password): 
    return PasswordManager.verify_password_advanced(provided_password, stored_password)

# =============================================================================
# CONTROL DE ACCESO (RBAC - FASE 2)
# =============================================================================
from enum import Enum
from typing import Set
from functools import wraps

class Permissions(Enum):
    PRODUCT_MANAGE = "product:manage"
    USER_MANAGE = "user:manage"
    INVENTORY_MANAGE = "inventory:manage"
    SYSTEM_CONFIG = "system:config"

class Role(Enum):
    ADMIN = "Administrador"
    SUPERVISOR = "Supervisor"
    CASHIER = "Cajera"
    KITCHEN = "Cocina"
    WAITER = "Mesero"
    
    def get_permissions(self) -> Set[Permissions]:
        if self == Role.ADMIN:
            return {Permissions.PRODUCT_MANAGE, Permissions.USER_MANAGE, Permissions.INVENTORY_MANAGE, Permissions.SYSTEM_CONFIG}
        elif self == Role.SUPERVISOR:
            return {Permissions.PRODUCT_MANAGE, Permissions.INVENTORY_MANAGE}
        else:
            return set()

def require_permission(permission: Permissions):
    """Decorador para verificar permisos en métodos que tienen 'self.user'."""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            user = getattr(self, 'user', None)
            if not user and hasattr(self, 'master'):
                user = getattr(self.master, 'user', None)
                
            if not user:
                messagebox.showerror("Error", "Usuario no autenticado")
                return
                
            try:
                user_role = Role(user.get('rol', ''))
            except ValueError:
                messagebox.showerror("Acceso Denegado", "Rol no reconocido o sin privilegios.")
                return
            
            if permission not in user_role.get_permissions():
                messagebox.showerror("Acceso Denegado", f"No tiene permiso para la acción: {permission.value}")
                try: self.db.log_access(user['id'], user['username'], 'unauthorized_access', func.__name__)
                except Exception: pass
                return
            
            return func(self, *args, **kwargs)
        return wrapper
    return decorator

# =============================================================================
# GESTOR DE SESIONES
# =============================================================================

class SessionManager:
    """Gestiona las sesiones activas de los usuarios y su tiempo de expiración."""
    def __init__(self, timeout_seconds=1800): # 30 minutos por defecto
        self.sessions = {}
        self.session_timeout = timeout_seconds
    
    def create_session(self, user_data):
        """Crea una nueva sesión y devuelve el ID único."""
        session_id = secrets.token_urlsafe(32)
        self.sessions[session_id] = {
            'user': user_data,
            'created_at': datetime.now(),
            'last_activity': datetime.now()
        }
        return session_id
    
    def validate_session(self, session_id):
        """Verifica si la sesión es válida y no ha expirado."""
        if session_id not in self.sessions:
            return False
        session = self.sessions[session_id]
        # Verificar expiración por inactividad
        if (datetime.now() - session['last_activity']).total_seconds() > self.session_timeout:
            del self.sessions[session_id]
            return False
        # Actualizar última actividad
        session['last_activity'] = datetime.now()
        return True

    def get_user(self, session_id):
        """Retorna los datos del usuario de una sesión activa."""
        if self.validate_session(session_id):
            return self.sessions[session_id]['user']
        return None

    def close_session(self, session_id):
        """Elimina una sesión activa."""
        if session_id in self.sessions:
            del self.sessions[session_id]

# Instancia global del gestor de sesiones
session_manager = SessionManager()

# =============================================================================
# CONFIGURACIÓN DE REGISTRO DE ERRORES (LOGGING)
# =============================================================================
# Se registran todos los errores en 'error_log.txt' para facilitar el diagnóstico.
logging.basicConfig(filename='error_log.txt', filemode='a', level=logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')


# Manejador global de excepciones: asegura que errores no capturados se guarden en el archivo log.
def _log_uncaught_exceptions(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error('Excepción no capturada', exc_info=(exc_type, exc_value, exc_traceback))
    play_sound_error()

sys.excepthook = _log_uncaught_exceptions

# Manejador específico para errores en los callbacks de Tkinter.
def _tk_report_callback_exception(self, exc, val, tb):
    logging.error('Excepción en callback de Tkinter', exc_info=(exc, val, tb))
    play_sound_error()

tk.Tk.report_callback_exception = _tk_report_callback_exception

# =============================================================================
# CONFIGURACIÓN VISUAL Y CONSTANTES
# =============================================================================
# =============================================================================
# CONFIGURACIÓN DE CONEXIÓN (NUBE VS LOCAL)
# =============================================================================
USE_CLOUD = True  # True = conecta a Render (nube), False = solo local
CLOUD_URL = "https://pikta-pos.onrender.com"
CLOUD_TOKEN = os.environ.get('PIKTA_CLOUD_TOKEN', 'PIKTA_CLOUD_2025_SECURE_TOKEN')

DB_NAME = "PIk'TADB.db"  # Nombre del archivo de base de datos SQLite
BG = '#2b3e50'          # Color de fondo principal (Azul Petróleo Superhero)
PANEL = '#4e5d6c'       # Color de fondo para paneles y tarjetas
FG = '#ebebeb'          # Color de texto principal (blanco grisáceo)
ACCENT = '#df691a'      # Color de acento (Naranja Superhero)
INFO = '#5bc0de'        # Azul claro para información
OK = '#5cb85c'          # Color para acciones exitosas (verde)
WARN = '#f0ad4e'        # Color para advertencias (naranja)
ERR = '#d9534f'         # Color para errores críticos (rojo)
FONT_SIZE_L = 16        # Tamaño de fuente grande
FONT_SIZE_XL = 22       # Tamaño de fuente extra grande
FONT_SIZE_NORMAL = 12   # Tamaño de fuente normal
PREP_DURATION = timedelta(minutes=15)

# Intentar importar Pillow para soporte avanzado de imágenes (JPEG, redimensionamiento)
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

def load_image(path, size=None):
    """
    Carga una imagen desde el disco.
    Si Pillow está instalado, permite cambiar el tamaño (redimensionar).
    Si no, usa el PhotoImage básico de Tkinter (solo PNG/GIF).
    """
    if not os.path.exists(path):
        return None
    try:
        if PIL_AVAILABLE:
            from PIL import ImageOps
            img = Image.open(path)
            if size:
                # Convertir a RGBA para fondo transparente y usar pad para evitar deformación
                img = img.convert("RGBA")
                try:
                    img = ImageOps.pad(img, size, color=(255, 255, 255, 0))
                except AttributeError:
                    img = img.resize(size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        else:
            return tk.PhotoImage(file=path)
    except Exception as e:
        print(f"Error cargando imagen {path}: {e}")
        return None

def find_product_image_path(product_name):
    """Busca la imagen de un producto en la carpeta Imagenes usando coincidencia flexible (difflib)."""
    import os, re, difflib
    img_dir = 'Imagenes'
    if not os.path.exists(img_dir): return None
    
    def normalize(t): return re.sub(r'[^a-zA-Z0-9]', '', str(t)).lower()
    target = normalize(product_name)
    
    files_map = {}
    for f in os.listdir(img_dir):
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif')):
            base_name = os.path.splitext(f)[0]
            norm_f = normalize(base_name)
            files_map[norm_f] = os.path.join(img_dir, f)
            
    if target in files_map:
        return files_map[target]
        
    matches = difflib.get_close_matches(target, files_map.keys(), n=1, cutoff=0.5)
    if matches:
        return files_map[matches[0]]
            
    return None


def _parse_money(text):
    """Extrae un número decimal de una cadena que contiene un importe.
    Devuelve float o None si no se puede parsear.
    """
    if not text:
        return None
    # Buscar la primera ocurrencia de un número con decimales opcionales
    m = re.search(r"-?\d+[\.,]?\d*", str(text))
    if not m:
        return None
    num = m.group(0).replace(',', '.')
    try:
        return float(num)
    except Exception:
        return None


def center_window(win, width, height):
    """Calcula y aplica la posición central para una ventana en la pantalla."""
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 3 # Un poco más arriba del centro absoluto para mejor visibilidad
    win.geometry(f"{width}x{height}+{x}+{y}")


import requests

class RemoteRow:
    """Simula una fila de sqlite3 que permite acceso por nombre e índice."""
    def __init__(self, values, columns):
        self._values = values
        self._columns = columns
        # Crear un mapa para acceso por nombre
        self._data = {col: val for col, val in zip(columns, values)}
        
    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data.get(key)
    
    def get(self, key, default=None):
        return self._data.get(key, default)
    
    def keys(self):
        return self._columns

class RemoteCursor:
    def __init__(self, connection):
        self.connection = connection
        self.lastrowid = None
        self.rowcount = 0
        self._results = []
        self._index = 0

    def execute(self, query, params=None):
        if params is None: params = []
        # Convertir objetos complejos a string para JSON
        clean_params = []
        for p in params:
            if isinstance(p, (datetime, timedelta)): clean_params.append(str(p))
            else: clean_params.append(p)
            
        try:
            response = requests.post(
                f"{CLOUD_URL}/api/sql/proxy",
                json={
                    "token": CLOUD_TOKEN,
                    "query": query,
                    "params": clean_params,
                    "fetch": True
                },
                timeout=15
            )
            
            if response.status_code != 200:
                print(f"Error del servidor ({response.status_code}): {response.text}")
                logging.error(f"Error del servidor ({response.status_code}): {response.text[:500]}")
                raise Exception(f"Error del servidor: {response.status_code}")

            data = response.json()
            if data["status"] == "success":
                columns = data.get("columns", [])
                rows_raw = data.get("result", [])
                
                # Convertir cada lista de valores en un objeto RemoteRow usando las columnas
                self._results = [RemoteRow(row, columns) for row in rows_raw] if rows_raw else []
                
                self.lastrowid = data["lastrowid"]
                self.rowcount = data["changes"]
                self._index = 0
            else:
                raise Exception(data["message"])
        except requests.exceptions.JSONDecodeError:
            print(f"La nube no respondió JSON. Respuesta: {response.text[:200]}")
            logging.error(f"Respuesta no JSON: {response.text[:500]}")
            raise Exception("La nube respondió con un error (HTML). Revisa el log de PythonAnywhere.")
        except Exception as e:
            logging.error(f"Error en RemoteCursor.execute: {e}")
            raise e

    def fetchone(self):
        if self._index < len(self._results):
            row = self._results[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self):
        return self._results

    def __iter__(self):
        return iter(self._results)

class RemoteConnection:
    def __init__(self):
        pass
    def cursor(self):
        return RemoteCursor(self)
    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

class DatabaseManager:
    """
    Controlador de la base de datos (Soporta Nube y Local).
    """

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        if not USE_CLOUD:
            self.init_db()

    def get_connection(self):
        """Retorna una conexión local o el Proxy de la nube."""
        if USE_CLOUD:
            return RemoteConnection()
        return sqlite3.connect(self.db_name)

    def init_db(self):
        """Inicializa las tablas base y asegura que existan los campos necesarios."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            # Creación de tabla de usuarios
            cur.execute('''CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL,
                nombre_completo TEXT
            )''')

            # Creación de tabla de productos del menú
            cur.execute('''CREATE TABLE IF NOT EXISTS productos_menu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                precio REAL NOT NULL,
                categoria TEXT,
                emoji TEXT,
                disponible BOOLEAN DEFAULT 1
            )''')

            # Creación de tabla de pedidos
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
                created_at TEXT
            )''')

            # Creación de tabla de inventario
            cur.execute('''CREATE TABLE IF NOT EXISTS inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ingrediente TEXT NOT NULL UNIQUE,
                cantidad REAL NOT NULL DEFAULT 0,
                unidad TEXT NOT NULL,
                stock_minimo REAL NOT NULL DEFAULT 0
            )''')

            # Creación de tabla de auditoría (Registro de cambios en datos)
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

            # Migraciones: Asegurar columnas nuevas
            self._ensure_column('productos_menu', 'categoria', 'TEXT')
            self._ensure_column('productos_menu', 'emoji', 'TEXT')
            self._ensure_column('productos_menu', 'prep_duration', 'INTEGER')
            self._ensure_column('pedidos', 'canal', 'TEXT')
            self._ensure_column('pedidos', 'usuario_id', 'INTEGER')
            self._ensure_column('pedidos', 'sesion_id', 'INTEGER')
            self._ensure_column('pedidos', 'created_at', 'TEXT')
            self._ensure_column('usuarios', 'two_factor_secret', 'TEXT')
            # Columna para marcar el inicio de preparación (KDS)
            self._ensure_column('pedidos', 'preparacion_inicio', 'TEXT')
            self._ensure_column('pedidos', 'preparacion_duracion', 'INTEGER')

            # Migración de contraseñas a formato hash seguro (En segundo plano para no ralentizar el inicio)
            threading.Thread(target=self._migrate_passwords_safely, daemon=True).start()

            # Usuarios por defecto (con contraseñas ya encriptadas)
            seeds = [
                ("Davis", "1234", "Administrador", "Davis Admin"),
                ("Rommel", "1234", "Supervisor", "Rommel Supervisor"),
                ("Estefani", "1234", "Cajera", "Estefani Cajera"),
                ("cocina", "1234", "Cocina", "Personal de Cocina"),
                ("mesero", "1234", "Mesero", "Personal de Mesas"),
                ("admin", "admin", "Administrador", "Administrador Sistema")
            ]
            for u, p, r, n in seeds:
                try:
                    # Buscar si el usuario ya existe
                    cur.execute('SELECT id FROM usuarios WHERE username = ?', (u,))
                    if not cur.fetchone():
                        hashed_p = hash_password(p)
                        cur.execute('INSERT INTO usuarios (username, password, rol, nombre_completo) VALUES (?,?,?,?)', (u, hashed_p, r, n))
                except Exception as e:
                    logging.error(f"Error al insertar usuario base: {e}")

            # --- PRODUCTOS DE PRUEBA (CATEGORÍAS POR DEFECTO) ---
            test_products = [
                ("Hamburguesa Clásica", 8.50, "🍔 Combos", "🍔"),
                ("Pizza Pepperoni", 12.00, "🍔 Combos", "🍕"),
                ("Papas Fritas XL", 4.50, "🍟 Extras", "🍟"),
                ("Alitas BBQ (6 unidades)", 7.25, "🍟 Extras", "🍗"),
                ("Coca Cola 600ml", 2.00, "🥤 Bebidas", "🥤"),
                ("Jugo de Naranja Natural", 3.50, "🥤 Bebidas", "🍊")
            ]
            for n, p, c, e in test_products:
                try:
                    cur.execute('SELECT id FROM productos_menu WHERE nombre = ?', (n,))
                    if not cur.fetchone():
                        cur.execute('INSERT INTO productos_menu (nombre, precio, categoria, emoji, prep_duration) VALUES (?,?,?,?,?)', (n, p, c, e, 5))
                except Exception as e:
                    logging.error(f"Error al insertar producto de prueba: {e}")

            # Registro de logs de acceso (quién entra y sale del sistema)
            cur.execute('''CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT,
                details TEXT,
                created_at TEXT
            )''')

            # Sesiones de caja (apertura y cierre de caja)
            cur.execute('''CREATE TABLE IF NOT EXISTS caja_sesiones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                inicio TEXT,
                inicial REAL DEFAULT 0,
                monto_apertura REAL,
                estado TEXT DEFAULT 'ABIERTO',
                cierre_total REAL,
                cierre_at TEXT
            )''')

            conn.commit()

    def audit_log(self, tabla, accion, usuario=None, detalles='', prev=None, new=None):
        """Registra un evento en la tabla de auditoría."""
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('''INSERT INTO auditoria (tabla, accion, usuario, detalles, datos_previos, datos_nuevos) 
                             VALUES (?,?,?,?,?,?)''',
                            (tabla, accion, usuario, detalles, 
                             json.dumps(prev) if prev else None, 
                             json.dumps(new) if new else None))
                conn.commit()
        except Exception as e:
            logging.error(f"Error en log de auditoría: {e}")

    def audit_admin_action(self, accion, usuario, detalles='', level='INFO'):
        import socket
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
        except Exception:
            hostname, ip = "Unknown", "Unknown"
            
        full_details = f"[{hostname} - {ip}] {detalles}"
        self.audit_log('admin_action', accion, usuario, full_details)
        
        if level == 'CRITICAL':
            try:
                with open('security_alerts.log', 'a') as f:
                    f.write(f"{datetime.now().isoformat()} - CRITICAL: {usuario} - {accion} - {full_details}\n")
            except Exception:
                pass

    def create_backup(self):
        """Crea una copia de seguridad de la base de datos actual."""
        if not os.path.exists('Backups'):
            os.makedirs('Backups')
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join('Backups', f'PIkTA_DB_backup_{timestamp}.db')
        
        try:
            shutil.copy2(self.db_name, backup_path)
            # Limpiar backups antiguos (mantener solo los últimos 30 días)
            now = datetime.now()
            for f in os.listdir('Backups'):
                f_path = os.path.join('Backups', f)
                if os.path.isfile(f_path):
                    f_time = datetime.fromtimestamp(os.path.getctime(f_path))
                    if (now - f_time).days > 30:
                        os.remove(f_path)
            return backup_path
        except Exception as e:
            logging.error(f"Error al crear backup: {e}")
            return None

    def _migrate_passwords_safely(self, batch_size=50):
        import gc
        
        # Evitar múltiples ejecuciones
        if hasattr(self, '_migration_completed') and self._migration_completed:
            return
        self._migration_completed = True
        
        max_retries_per_user = 3
        retry_count = {}
        
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            while True:
                cur.execute("""
                    SELECT id, username, password FROM usuarios 
                    WHERE password NOT LIKE '2|%' 
                    AND password NOT LIKE '1|%'
                    AND password NOT LIKE 'FAILED_%'
                    LIMIT ?
                """, (batch_size,))
                
                batch = cur.fetchall()
                if not batch:
                    break
                
                for uid, uname, pwd in batch:
                    # Verificar reintentos
                    if retry_count.get(uid, 0) >= max_retries_per_user:
                        logging.error(f"Migración fallida para usuario {uid} después de {max_retries_per_user} intentos")
                        # Marcar como migrado con un marcador especial para no reintentar
                        cur.execute("UPDATE usuarios SET password = ? WHERE id = ?", 
                                   (f"FAILED_{pwd[:50]}", uid))
                        conn.commit()
                        continue
                    
                    try:
                        # Solo resetear si está claramente corrupto
                        if uname in ['Davis', 'Rommel', 'Estefani', 'cocina', 'mesero', 'admin']:
                            if not pwd or len(pwd) > 200 or pwd.startswith('FAILED_'):
                                pwd = '1234'
                                logging.warning(f"Reset password for default user {uname}")
                        
                        new_pwd = hash_password(pwd)
                        cur.execute("UPDATE usuarios SET password = ? WHERE id = ?", (new_pwd, uid))
                        conn.commit()
                        retry_count[uid] = 0
                        
                    except Exception as e:
                        retry_count[uid] = retry_count.get(uid, 0) + 1
                        logging.error(f"Error migrando usuario {uid} (intento {retry_count[uid]}): {e}")
                        continue
                    
                    # Limpiar memoria
                    del pwd, new_pwd
                
                del batch
                gc.collect()
                time.sleep(0.01)

    def _ensure_column(self, table, column, col_type):
        """Añade columna con validación estricta de nombres y prevención de inyección SQL en DDL."""
        import re
        ALLOWED_TABLES = {'productos_menu', 'pedidos', 'inventario', 'usuarios', 'auditoria', 'access_logs', 'caja_sesiones'}
        
        if table not in ALLOWED_TABLES:
            logging.error(f"Tabla no permitida: {table}")
            return
            
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column):
            logging.error(f"Nombre de columna inválido: {column}")
            return

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cur.fetchall()]
                if column not in cols:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists, ignore
            except Exception as e:
                logging.error(f"Error al añadir columna {column} a {table}: {e}")

    def log_access(self, user_id, username, action, details=''):
        """Guarda un evento de acceso en la tabla access_logs."""
        import socket
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
        except Exception:
            hostname, ip = "Unknown", "Unknown"
        
        full_details = f"[{hostname} - {ip}] {details}"
        
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute('INSERT INTO access_logs (user_id, username, action, details, created_at) VALUES (?,?,?,?,?)',
                            (user_id, username, action, full_details, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logging.exception(f'Error al registrar acceso: {e}')

    def fetch_all(self, query, params=()):
        """Ejecuta una consulta SELECT y devuelve todas las filas."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return cur.fetchall()

    def fetch_one(self, query, params=()):
        """Ejecuta una consulta SELECT y devuelve una sola fila."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return cur.fetchone()

    def execute(self, query, params=()):
        """Ejecuta INSERT, UPDATE o DELETE y confirma los cambios."""
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return cur
        except Exception as e:
            logging.exception(f'Error de ejecución DB: {e} - Query: {query}')
            raise


class AutoScrollbar(ttk.Scrollbar):
    """Un scrollbar que se oculta automáticamente cuando no es necesario."""
    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


class POSFrame(tk.Canvas):
    """
    Interfaz de Punto de Venta (Caja).
    Permite realizar ventas directas y cobrar pedidos de meseros.
    """
    def __init__(self, parent, db: DatabaseManager, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(parent, bg=BG, highlightthickness=0, *args, **kwargs)
        self.db = db
        self.user = user
        self.session_id = None
        self.cart = [] 
        self.editing_table_id = None # Para rastrear si estamos editando una mesa existente
        
        # Logo de Fondo en POS
        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path)
            self.last_bg_w, self.last_bg_h = 0, 0
            def draw_pos_bg(e):
                cw, ch = e.width, e.height
                if cw < 10 or ch < 10: return
                if cw == self.last_bg_w and ch == self.last_bg_h: return
                self.last_bg_w, self.last_bg_h = cw, ch
                self.delete("bg")
                img_res = self.bg_raw.resize((cw, ch), Image.LANCZOS)
                self.bg_photo = ImageTk.PhotoImage(img_res)
                self.create_image(cw//2, ch//2, image=self.bg_photo, tags="bg")
                self.tag_lower("bg")
            self.bind("<Configure>", draw_pos_bg)

        # --- Contenedores para Secciones (Sin main_container que tape todo) ---
        # Cabecera - Flota sobre el canvas
        self.header = ttk.Frame(self, bootstyle="info", padding=15)
        self.header_win = self.create_window(0, 0, window=self.header, anchor='nw', tags="header")
        
        # Cuerpo - Pestañas de POS
        self.body = ttk.Frame(self, padding=10)
        self.body_win = self.create_window(0, 70, window=self.body, anchor='nw', tags="body")
        
        def resize_pos_content(e):
            # Ajustar anchos de las ventanas del canvas
            self.itemconfig("header", width=e.width)
            self.itemconfig("body", width=e.width, height=e.height - 70)
            if hasattr(self, 'bg_raw'): draw_pos_bg(e)
            
        self.bind("<Configure>", resize_pos_content)

        # --- Contenido de la Cabecera ---
        # Icono decorativo del POS
        pos_img = load_image(os.path.join('Imagenes', 'pos.png'), size=(60, 60))
        if pos_img:
            lbl = ttk.Label(self.header, image=pos_img, bootstyle="inverse-info")
            lbl.image = pos_img
            lbl.pack(side='left', padx=10)
        
        ttk.Label(self.header, text='🛒 PUNTO DE VENTA (Caja)', font=(None, 24, 'bold'), bootstyle="inverse-info").pack(side='left', padx=10)
        
        ttk.Button(self.header, text='Regresar', command=lambda: self.master.select(0), bootstyle="secondary", cursor="hand2", padding=10).pack(side='right', padx=5)

        self.btn_open_caja = ttk.Button(self.header, text='Abrir Caja', command=self.open_caja, bootstyle="success", cursor="hand2", padding=10)
        self.btn_open_caja.pack(side='right', padx=5)
        self.btn_close_caja = ttk.Button(self.header, text='Cerrar Caja', command=self.cerrar_caja, bootstyle="danger", cursor="hand2", padding=10)
        self.btn_close_caja.pack(side='right', padx=5)

        # --- Contenedor de Pestañas Internas ---
        self.pos_notebook = ttk.Notebook(self.body)
        self.pos_notebook.pack(fill='both', expand=True, pady=10)

        # Pestaña 1: Venta Directa
        self.tab_venta = ttk.Frame(self.pos_notebook, padding=10)
        self.pos_notebook.add(self.tab_venta, text='🛒 Venta Directa')

        # Lado izquierdo de Venta Directa: Catálogo
        self.left_v = ttk.Frame(self.tab_venta)
        self.left_v.pack(side='left', fill='both', expand=True, padx=(0, 10))

        # Filtro de categorías dinámico
        self.categories = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        if not self.categories: self.categories = ['General']
        self.selected_category = tk.StringVar(value=self.categories[0])
        self.cat_frame = ttk.Frame(self.left_v)
        self.cat_frame.pack(fill='x', pady=(0, 15))
        self.refresh_category_buttons()

    def refresh_category_buttons(self):
        for child in self.cat_frame.winfo_children():
            child.destroy()
        
        # Recargar categorías de la BD
        self.categories = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        if not self.categories: self.categories = ['General']
        if self.selected_category.get() not in self.categories:
            self.selected_category.set(self.categories[0])

        for c in self.categories:
            ttk.Radiobutton(self.cat_frame, text=c, variable=self.selected_category, value=c, 
                           command=self.render_products, bootstyle="info-toolbutton", padding=10).pack(side='left', padx=5)

        # Contenedor con scroll para los productos
        products_wrapper = ttk.Frame(self.left_v)
        products_wrapper.pack(side="left", fill="both", expand=True)
        
        self.products_canvas = tk.Canvas(products_wrapper, bg=BG, highlightthickness=0)
        self.scrollbar = AutoScrollbar(products_wrapper, orient="vertical", command=self.products_canvas.yview)
        self.products_frame = ttk.Frame(self.products_canvas)

        self.products_frame.bind("<Configure>", lambda e: self.products_canvas.configure(scrollregion=self.products_canvas.bbox("all")))
        self.products_frame_id = self.products_canvas.create_window((0, 0), window=self.products_frame, anchor="nw")
        self.products_canvas.bind('<Configure>', lambda e: self.products_canvas.itemconfig(self.products_frame_id, width=e.width))
        self.products_canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.products_canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        products_wrapper.columnconfigure(0, weight=1)
        products_wrapper.rowconfigure(0, weight=1)

        # Lado derecho de Venta Directa: Carrito
        right_v = ttk.Frame(self.tab_venta, width=500, bootstyle="secondary")
        right_v.pack(side='right', fill='y')
        right_v.pack_propagate(False)
        
        ttk.Label(right_v, text='ORDEN ACTUAL', font=(None, 12, 'bold'), bootstyle="inverse-secondary", padding=10).pack(fill='x')
        
        # --- Treeview para Venta Directa ---
        tree_frame_venta = ttk.Frame(right_v)
        tree_frame_venta.pack(fill='both', expand=True, padx=10, pady=10)
        
        cols_venta = ('Producto', 'Cant', 'Precio', 'Subtotal')
        self.cart_tree_venta = ttk.Treeview(tree_frame_venta, columns=cols_venta, show='headings')
        for col in cols_venta:
            self.cart_tree_venta.heading(col, text=col)
            self.cart_tree_venta.column(col, anchor='center', width=60)
        self.cart_tree_venta.column('Producto', width=200, anchor='w')
        self.cart_tree_venta.pack(side='left', fill='both', expand=True)
        
        cart_scroll_venta = ttk.Scrollbar(tree_frame_venta, orient='vertical', command=self.cart_tree_venta.yview)
        self.cart_tree_venta.configure(yscrollcommand=cart_scroll_venta.set)
        cart_scroll_venta.pack(side='right', fill='y')
        
        self.cart_tree_venta.bind("<Double-1>", self.on_cart_venta_double_click)
        
        self.total_label = ttk.Label(right_v, text='Total: $0.00', font=(None, 14, 'bold'), bootstyle="inverse-secondary", padding=10)
        self.total_label.pack(fill='x')

        # Selector de canal en Caja (Para llevar o Consumo Local)
        chan_frame = ttk.Frame(right_v, bootstyle="secondary", padding=5)
        chan_frame.pack(fill='x')
        ttk.Label(chan_frame, text="Tipo de Pedido:", bootstyle="inverse-secondary").pack(side='left', padx=5)
        self.order_channel = tk.StringVar(value="CAJA") # Por defecto Caja
        ttk.Radiobutton(chan_frame, text="Llevar", variable=self.order_channel, value="LLEVAR", bootstyle="info-toolbutton").pack(side='left', padx=2)
        ttk.Radiobutton(chan_frame, text="Local", variable=self.order_channel, value="CAJA", bootstyle="info-toolbutton").pack(side='left', padx=2)

        ttk.Button(right_v, text='Quitar Item', command=self.remove_selected, bootstyle="danger", cursor="hand2").pack(fill='x', padx=10, pady=5)
        ttk.Button(right_v, text='CONFIRMAR PEDIDO', command=self.process_order, bootstyle="success", cursor="hand2", padding=10).pack(fill='x', padx=10, pady=10)

        # Pestaña 2: Cobrar Mesas
        self.tab_cobros = ttk.Frame(self.pos_notebook, padding=10)
        self.pos_notebook.add(self.tab_cobros, text='📋 Cobrar Mesas')
        
        self.build_cobros_tab()
        
        # Iniciar polling para notificaciones de KDS
        self.last_ready_orders = set()
        self.poll_ready_orders()

        # Cargar productos inicialmente (SOLO SI ES VISIBLE)
        # Se movió a open_pos para carga diferida.
        pass

    def poll_ready_orders(self):
        """Revisa la base de datos para ver si la cocina terminó nuevos pedidos y reproduce sonido."""
        try:
            rows = self.db.fetch_all("SELECT id FROM pedidos WHERE estado = 'LISTO' AND pagado = 0")
            current_ready = set(r[0] for r in rows)
            
            new_ready = current_ready - getattr(self, 'last_ready_orders', set())
            if new_ready and hasattr(self, 'last_ready_orders'):
                self.notify_order_ready("¡Nuevos Pedidos Listos!")
                if hasattr(self, 'refresh_unpaid_orders'):
                    self.refresh_unpaid_orders()
                    
            self.last_ready_orders = current_ready
        except Exception:
            pass
        finally:
            self.after(5000, self.poll_ready_orders)

    def build_cobros_tab(self):
        """Construye la interfaz para cobrar pedidos de meseros con teclado numérico y métodos de pago."""
        # Lado izquierdo: Lista de pedidos pendientes (Más angosto)
        left_c = ttk.Frame(self.tab_cobros, width=450)
        left_c.pack(side='left', fill='both', expand=False, padx=(0, 15))
        left_c.pack_propagate(False)
        
        ttk.Label(left_c, text='PEDIDOS PENDIENTES', font=(None, 14, 'bold')).pack(pady=10)
        
        # Tabla de pedidos pendientes (Ajustada al ancho)
        cols = ('ID', 'Número', 'Mesa', 'Total') # Quitamos fecha de aquí para ahorrar espacio
        self.unpaid_tree = ttk.Treeview(left_c, columns=cols, show='headings', bootstyle="info", height=15)
        
        self.unpaid_tree.heading('ID', text='ID')
        self.unpaid_tree.column('ID', width=40, anchor='center')
        
        self.unpaid_tree.heading('Número', text='Número')
        self.unpaid_tree.column('Número', width=120, anchor='w')
        
        self.unpaid_tree.heading('Mesa', text='Mesa')
        self.unpaid_tree.column('Mesa', width=120, anchor='center')
        
        self.unpaid_tree.heading('Total', text='Total')
        self.unpaid_tree.column('Total', width=80, anchor='center')
        
        self.unpaid_tree.pack(fill='both', expand=True)
        
        ttk.Button(left_c, text='Actualizar Lista', command=self.refresh_unpaid_orders, bootstyle="info-outline").pack(pady=10)
        
        # Lado derecho: Detalles y Cobro (Ahora ocupa el resto y se expande)
        right_c = ttk.Frame(self.tab_cobros, bootstyle="secondary")
        right_c.pack(side='right', fill='both', expand=True)
        
        ttk.Label(right_c, text='DETALLE DE CUENTA', font=(None, 16, 'bold'), bootstyle="inverse-secondary", padding=5).pack(fill='x')
        # Detalle tipo Excel (Treeview) - Reducido a 3 filas para dar más espacio
        cols_det = ('Producto', 'Cant', 'Precio', 'Subtotal')
        self.cart_tree = ttk.Treeview(right_c, columns=cols_det, show='headings', height=3)
        for col in cols_det:
            self.cart_tree.heading(col, text=col)
            self.cart_tree.column(col, anchor='center', width=80)
        
        self.cart_tree.column('Producto', width=220, anchor='w')
        self.cart_tree.column('Cant', width=70, anchor='center')
        self.cart_tree.pack(fill='x', expand=False)

        # Evento para editar cantidad (Doble clic)
        self.cart_tree.bind("<Double-1>", self.on_cart_double_click)

        cart_scroll = ttk.Scrollbar(right_c, orient='vertical', command=self.cart_tree.yview)
        self.cart_tree.configure(yscrollcommand=cart_scroll.set)
        
        self.total_cobro_label = ttk.Label(right_c, text='Total a Cobrar: $0.00', font=(None, 22, 'bold'), bootstyle="inverse-secondary", padding=5)
        self.total_cobro_label.pack(fill='x')

        # Botón para Agregar más productos a la mesa seleccionada
        ttk.Button(right_c, text='✚ AGREGAR PRODUCTOS A ESTA MESA', 
                  command=self.add_more_to_table, bootstyle="warning", cursor="hand2", padding=8).pack(fill='x', padx=10, pady=2)

        # --- Teclado Numérico y Métodos de Pago ---
        # Definir estilos para botones
        style = ttk.Style()
        style.configure("Large.secondary.TButton", font=(None, 18, 'bold'))
        style.configure("success.TButton", font=(None, 12, 'bold'))
        style.configure("info.TButton", font=(None, 12, 'bold'))
        style.configure("primary.TButton", font=(None, 12, 'bold'))

        pay_frame = ttk.Frame(right_c, bootstyle="secondary", padding=5)
        pay_frame.pack(fill='both', expand=True)
        
        # Configurar expansión de pay_frame
        pay_frame.columnconfigure(0, weight=1)
        pay_frame.columnconfigure(1, weight=1)
        pay_frame.columnconfigure(2, weight=1)
        pay_frame.rowconfigure(2, weight=1) 

        # Entrada de "Monto Recibido" y "Cambio" en la misma fila para ahorrar espacio y mejorar visibilidad
        ttk.Label(pay_frame, text="Monto Recibido $:", font=(None, 11, 'bold'), bootstyle="inverse-secondary").grid(row=0, column=0, sticky='w')
        ttk.Label(pay_frame, text="Cambio $:", font=(None, 11, 'bold'), bootstyle="inverse-secondary").grid(row=0, column=2, sticky='w', padx=(10, 0))

        self.pay_amount_var = tk.StringVar(value="0.00")
        self.pay_entry = ttk.Entry(pay_frame, textvariable=self.pay_amount_var, font=(None, 22, 'bold'), justify='right')
        self.pay_entry.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 5))

        # El label de Cambio ahora está al lado del monto de entrada
        self.change_label = ttk.Label(pay_frame, text='$0.00', font=(None, 22, 'bold'), bootstyle="inverse-secondary", anchor='e')
        self.change_label.grid(row=1, column=2, sticky='ew', padx=(10, 0), pady=(0, 5))

        # Teclado Numérico
        numpad = ttk.Frame(pay_frame, bootstyle="secondary")
        numpad.grid(row=2, column=0, columnspan=2, sticky='nsew')
        
        for col in range(3): numpad.columnconfigure(col, weight=1)
        for row in range(4): numpad.rowconfigure(row, weight=1)

        buttons = [
            '7', '8', '9',
            '4', '5', '6',
            '1', '2', '3',
            '0', '.', 'C'
        ]

        def press_key(key):
            curr = self.pay_amount_var.get()
            if key == 'C':
                self.pay_amount_var.set("0.00")
            elif key == '.':
                if '.' not in curr: self.pay_amount_var.set(curr + '.')
            else:
                if curr == "0.00": self.pay_amount_var.set(key)
                else: self.pay_amount_var.set(curr + key)

        for i, b in enumerate(buttons):
            btn = ttk.Button(numpad, text=b, style="Large.secondary.TButton", 
                            command=lambda x=b: press_key(x))
            btn.grid(row=i//3, column=i%3, padx=2, pady=2, sticky='nsew', ipady=5)

        # Métodos de Pago con Imágenes
        methods_frame = ttk.Frame(pay_frame, bootstyle="secondary")
        methods_frame.grid(row=2, column=2, padx=(10, 0), sticky='nsew')

        self.payment_method = tk.StringVar(value="EFECTIVO")
        
        methods = [
            ('EFECTIVO', 'efectivo.jpeg', 'success'),
            ('YAPPY', 'yappy.png', 'info'),
            ('TARJETA', 'visa.png', 'primary')
        ]

        ttk.Label(methods_frame, text="MÉTODOS", font=(None, 10, 'bold'), bootstyle="inverse-secondary").pack(pady=(0, 2))

        for i, (name, img_file, style_name) in enumerate(methods):
            m_btn_container = ttk.Frame(methods_frame, bootstyle="secondary")
            m_btn_container.pack(fill='both', expand=True, pady=1)
            
            img = load_image(os.path.join('Imagenes', img_file), size=(40, 40))
            
            btn_pay = ttk.Button(m_btn_container, text=f"{name}", 
                                image=img, compound='left',
                                command=lambda n=name: self.pay_with_method(n), 
                                bootstyle=f"{style_name}", cursor="hand2", padding=5)
            btn_pay.image = img
            btn_pay.pack(fill='both', expand=True)

        def update_change(*args):
            try:
                # Extraer solo el valor numérico del label (ej: "Total a Cobrar: $11.50")
                total_text = self.total_cobro_label.cget("text")
                total = _parse_money(total_text) or 0.0
                
                # Obtener el monto pagado
                paid_val = self.pay_amount_var.get()
                paid = _parse_money(paid_val) or 0.0
                
                change = paid - total
                self.change_label.config(text=f"${max(0, change):.2f}")
            except Exception:
                self.change_label.config(text="$0.00")
        
        self.pay_amount_var.trace_add("write", update_change)

        self.unpaid_tree.bind('<<TreeviewSelect>>', self.on_unpaid_select)
        self.refresh_unpaid_orders()

    def pay_with_method(self, method):
        """Asigna el método de pago y procesa la transacción inmediatamente."""
        self.payment_method.set(method)
        self.pay_order()

    def refresh_unpaid_orders(self):
        """Consulta pedidos de meseros y de caja (para llevar) que aún no han sido pagados."""
        for r in self.unpaid_tree.get_children(): self.unpaid_tree.delete(r)
        
        # Incluimos 'LLEVAR' y 'Móvil' en la consulta para que el cajero pueda cobrarlos
        query = "SELECT id, numero, mesa, total, created_at FROM pedidos WHERE pagado = 0 AND canal IN ('MESERO', 'LLEVAR', 'Móvil') ORDER BY created_at DESC"
        rows = self.db.fetch_all(query)
        for r in rows:
            # Si mesa es None (pedidos para llevar), mostrar 'PARA LLEVAR'
            # r = (id, numero, mesa, total, created_at)
            id_val, num, mesa, total, fecha = r
            if mesa is None: mesa = 'PARA LLEVAR'
            
            # Insertar solo los 4 campos que definimos en build_cobros_tab
            self.unpaid_tree.insert('', 'end', values=(id_val, num, mesa, total))

    def on_unpaid_select(self, event):
        """Muestra el detalle del pedido seleccionado en la tabla tipo Excel."""
        sel = self.unpaid_tree.selection()
        if not sel: return
        item = self.unpaid_tree.item(sel[0])
        order_id = item['values'][0]
        
        # Guardar el ID del pedido actual para referencia
        self.current_order_id = order_id
        
        order = self.db.fetch_one("SELECT items, total FROM pedidos WHERE id = ?", (order_id,))
        if order:
            items = json.loads(order[0])
            # Limpiar tabla de detalles
            for r in self.cart_tree.get_children(): self.cart_tree.delete(r)
            
            # Poblar tabla con items
            for it in items:
                nombre = it.get('nombre', 'N/A')
                precio = it.get('precio', it.get('precio_unitario', 0.0))
                qty = it.get('qty', it.get('cantidad', 1))
                subtotal = precio * qty
                self.cart_tree.insert('', 'end', values=(nombre, f"x{qty}", f"${precio:.2f}", f"${subtotal:.2f}"))
            
            self.total_cobro_label.config(text=f"Total a Cobrar: ${order[1]:.2f}")
            self.pay_amount_var.set(f"{order[1]:.2f}")

    def on_cart_double_click(self, event):
        """Permite editar la cantidad de un producto directamente en la celda."""
        region = self.cart_tree.identify_region(event.x, event.y)
        if region != "cell": return
        
        column = self.cart_tree.identify_column(event.x)
        if column != "#2": return # Solo permitir editar la columna 'Cant'
        
        item_id = self.cart_tree.identify_row(event.y)
        if not item_id: return
        
        # Obtener coordenadas de la celda para posicionar el Entry
        x, y, width, height = self.cart_tree.bbox(item_id, column)
        
        # Obtener valor actual
        curr_values = self.cart_tree.item(item_id, 'values')
        curr_qty = curr_values[1].replace('x', '')
        
        # Crear Entry flotante sobre la celda
        entry = ttk.Entry(self.cart_tree, justify='center')
        entry.insert(0, curr_qty)
        entry.select_range(0, 'end')
        entry.focus_set()
        
        # Posicionar el entry exactamente sobre la celda
        entry.place(x=x, y=y, width=width, height=height)
        
        def save_edit(event=None):
            try:
                new_qty = int(entry.get())
                if new_qty < 1: raise ValueError
                
                # Recalcular subtotal
                precio = float(curr_values[2].replace('$', ''))
                subtotal = precio * new_qty
                
                # Actualizar fila
                self.cart_tree.item(item_id, values=(curr_values[0], f"x{new_qty}", f"${precio:.2f}", f"${subtotal:.2f}"))
                
                # Recalcular total general y guardar en BD
                self.update_order_total_from_tree()
            except ValueError:
                pass # Si no es un número válido, ignorar
            finally:
                entry.destroy()

        # Atajos para el entry
        entry.bind('<Return>', save_edit)
        entry.bind('<FocusOut>', save_edit)
        entry.bind('<Escape>', lambda e: entry.destroy())

    def update_order_total_from_tree(self):
        """Recalcula el total y guarda los cambios en la base de datos."""
        total = 0.0
        updated_items = []
        for item_id in self.cart_tree.get_children():
            vals = self.cart_tree.item(item_id, 'values')
            nombre = vals[0]
            qty = int(vals[1].replace('x', ''))
            precio = float(vals[2].replace('$', ''))
            subtotal = precio * qty
            total += subtotal
            updated_items.append({'nombre': nombre, 'qty': qty, 'precio': precio})
        
        # Actualizar en la base de datos para no perder cambios al cambiar de selección
        if hasattr(self, 'current_order_id'):
            self.db.execute("UPDATE pedidos SET items=?, total=? WHERE id=?", 
                            (json.dumps(updated_items, ensure_ascii=False), total, self.current_order_id))
        
        self.total_cobro_label.config(text=f"Total a Cobrar: ${total:.2f}")
        self.pay_amount_var.set(f"{total:.2f}")
        
        # Refrescar la lista de pedidos para mostrar el nuevo total
        self.refresh_unpaid_orders()

    def pay_order(self):
        """Registra el pago del pedido seleccionado."""
        sel = self.unpaid_tree.selection()
        if not sel:
            messagebox.showwarning('Aviso', 'Seleccione un pedido para cobrar')
            return
        
        if not self.session_id:
            if messagebox.askyesno('Caja cerrada', 'No hay caja abierta. ¿Desea abrirla ahora?'):
                self.open_caja()
            if not self.session_id:
                return

        item = self.unpaid_tree.item(sel[0])
        order_id = item['values'][0]
        method = self.payment_method.get()
        
        total_amt = _parse_money(self.total_cobro_label.cget("text"))
        paid_amt = _parse_money(self.pay_amount_var.get()) or 0.0
        
        if total_amt is None or total_amt <= 0:
            messagebox.showerror('Error', 'Importe inválido, verifique el pedido')
            return

        if paid_amt < total_amt and method == 'EFECTIVO':
            messagebox.showwarning('Pago Insuficiente', f'El monto pagado (${paid_amt:.2f}) es menor al total (${total_amt:.2f})')
            return

        change = max(0, paid_amt - total_amt)
        confirm_msg = f'¿Confirmar el pago de ${total_amt:.2f} con {method}?'
        if method == 'EFECTIVO':
            confirm_msg += f'\n\nCambio a devolver: ${change:.2f}'

        if messagebox.askyesno('Confirmar Pago', confirm_msg):
            try:
                # 1. Obtener items finales de la tabla (por si se editaron cantidades)
                final_items = []
                for item_id in self.cart_tree.get_children():
                    vals = self.cart_tree.item(item_id, 'values')
                    nombre = vals[0]
                    qty = int(vals[1].replace('x', ''))
                    precio = float(vals[2].replace('$', ''))
                    final_items.append({'nombre': nombre, 'qty': qty, 'precio': precio})
                
                # 2. Actualizar el pedido en la BD (marcar pagado y guardar items/total finales)
                self.db.execute('UPDATE pedidos SET pagado = 1, sesion_id = ?, metodo_pago = ?, items = ?, total = ? WHERE id = ?', 
                                (self.session_id, method, json.dumps(final_items, ensure_ascii=False), total_amt, int(order_id)))
                
                # 3. Generar factura para mostrar e imprimir
                factura_text = self.generate_invoice_text(order_id, method, final_items, total_amt, paid_amt, change)
                
                # 3.5 Abrir cajón de dinero y mandar a imprimir automáticamente con logo
                self.open_cash_drawer()
                self.print_graphical_ticket(factura_text, "factura")

                messagebox.showinfo('Éxito', f'Pago procesado correctamente.\nCambio: ${change:.2f}')
                
                # Ya no se muestra el popup, solo se imprime el ticket gráfico automáticamente
                
                # 5. Limpiar y actualizar
                self.current_order_id = None
                self.refresh_unpaid_orders()
                for r in self.cart_tree.get_children(): self.cart_tree.delete(r)
                self.total_cobro_label.config(text="Total a Cobrar: $0.00")
                self.pay_amount_var.set("0.00")
                self.change_label.config(text="Cambio: $0.00")
            except Exception as e:
                logging.exception('Error al procesar pago')
                messagebox.showerror('Error', 'No se pudo procesar el pago')

    def generate_invoice_text(self, order_id, method, items, total, paid, change):
        """Genera el texto formateado de la factura."""
        res = self.db.fetch_one("SELECT numero, mesa, created_at FROM pedidos WHERE id=?", (order_id,))
        num, mesa, fecha = res if res else ("N/A", "N/A", datetime.now().strftime("%Y-%m-%d %H:%M"))
        
        # Formatear fecha si es objeto datetime o string ISO
        if isinstance(fecha, str) and 'T' in fecha:
            try:
                dt = datetime.fromisoformat(fecha)
                fecha = dt.strftime("%d/%m/%Y %H:%M")
            except Exception: pass

        W = 40
        factura = ""
        factura += "DGI".center(W) + "\n"
        factura += "RUC: 1675920-1-680848 DV 00".center(W) + "\n"
        factura += "PIK'TA GRILL".center(W) + "\n"
        factura += "C. Aristides Romero, David,".center(W) + "\n"
        factura += "Provincia de Chiriquí.".center(W) + "\n"
        factura += "62878787".center(W) + "\n"
        factura += "COMPROBANTE AUXILIAR DE FACTURA ELECTRONIC".center(W) + "\n"
        factura += "A".center(W) + "\n\n"
        factura += "Factura de Operación Interna".center(W) + "\n"
        factura += f"# {num}\n"
        factura += f"FECHA: {fecha}\n"
        factura += "SUCURSAL: 001\n"
        factura += "CAJA/PTO FACT: 001\n"
        factura += f"MESA: {mesa if mesa else 'PARA LLEVAR'}\n"
        factura += f"CAJERO: {self.user.get('nombre', 'Cajero')}\n"
        factura += "-" * W + "\n"
        factura += "RECEPTOR: Consumidor final\n"
        factura += f"CLIENTE: {mesa if mesa else 'CLIENTE GENÉRICO'}\n"
        factura += "-" * W + "\n"
        factura += f"{'DESCRIPCION':<28} {'MONTO':>11}\n"
        factura += "-" * W + "\n"
        
        for it in items:
            nombre = it['nombre'][:38] # Limitar a 38 caracteres
            qty = it['qty']
            precio = it['precio']
            sub = precio * qty
            
            factura += f"{nombre}\n"
            line2 = f"{float(qty):.2f} X {precio:.2f} (0%)"
            factura += f"{line2:<28} ${sub:>10.2f}\n"
            
        factura += "-" * W + "\n"
        factura += f"{'SUBTOTAL':<28} ${total:>10.2f}\n"
        factura += f"{'TOTAL':<28} ${total:>10.2f}\n"
        factura += "-" * W + "\n"
        factura += f"{method:<28} ${paid:>10.2f}\n"
        if float(change) > 0:
            factura += f"{'CAMBIO':<28} ${change:>10.2f}\n"
        factura += "-" * W + "\n\n"
        factura += "GRACIAS POR SU PREFERENCIA".center(W) + "\n"
        factura += "VUELVA PRONTO!".center(W) + "\n"
        return factura

    def open_cash_drawer(self, printer_name=None):
        """Envía el comando ESC/POS para abrir el cajón de dinero.
        Args:
            printer_name: Nombre de la impresora (opcional, usa default si es None).
        """
        try:
            if not printer_name:
                cfg = PrinterConfig()
                printer_name = cfg.resolve_printer("factura")
            if not printer_name:
                printer_name = win32print.GetDefaultPrinter()
            hPrinter = win32print.OpenPrinter(printer_name)
            try:
                # Comando ESC/POS: ESC p m t1 t2
                # \x1b\x70\x00\x19\xfa es el estándar para la mayoría de impresoras térmicas
                drawer_command = b'\x1b\x70\x00\x19\xfa' 
                win32print.StartDocPrinter(hPrinter, 1, ("Pikta Open Drawer", None, "RAW"))
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, drawer_command)
                win32print.EndPagePrinter(hPrinter)
                win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)
        except Exception as e:
            logging.error(f"No se pudo abrir el cajón de dinero: {e}")

    def print_graphical_ticket(self, text, tipo_doc="factura"):
        """Genera y envía a imprimir un ticket gráfico con el logo en alta resolución.
        Args:
            text: Texto del ticket a imprimir.
            tipo_doc: Tipo de documento (factura, comanda, cierre, etc).
        """
        try:
            if sys.platform == "win32":
                cfg = PrinterConfig()
                printer_name = cfg.resolve_printer(tipo_doc)
                if not printer_name:
                    printer_name = find_pos_printer()
                if not printer_name:
                    printer_name = win32print.GetDefaultPrinter()

                from PIL import Image, ImageDraw, ImageFont
                import os
                import win32ui
                import win32con
                from PIL import ImageWin

                hDC = win32ui.CreateDC()
                hDC.CreatePrinterDC(printer_name)
                
                # Calcular ancho basado en resolución (80mm = ~3.15 pulgadas)
                dpi_x = hDC.GetDeviceCaps(win32con.LOGPIXELSX)
                if dpi_x < 100: dpi_x = 203 # Fallback para impresoras genéricas
                
                img_width = int(3.15 * dpi_x)
                scale = img_width / 380.0 # Base scale assuming 380px was 1x

                lines = text.split('\n')
                
                # Cargar logo
                logo_path = os.path.join('Imagenes', 'pikata.png')
                logo_img = None
                logo_height = 0
                if os.path.exists(logo_path):
                    logo_img = Image.open(logo_path).convert("RGBA")
                    aspect_ratio = logo_img.height / logo_img.width
                    new_width = int(200 * scale)
                    new_height = int(new_width * aspect_ratio)
                    logo_img = logo_img.resize((new_width, new_height), Image.LANCZOS)
                    bg = Image.new("RGB", logo_img.size, (255, 255, 255))
                    if len(logo_img.split()) == 4:
                        bg.paste(logo_img, mask=logo_img.split()[3])
                    else:
                        bg.paste(logo_img)
                    logo_img = bg
                    logo_height = new_height

                line_spacing = int(20 * scale)
                img_height = logo_height + int(20 * scale) + (len(lines) * line_spacing)
                img = Image.new('RGB', (img_width, img_height), 'white')
                draw = ImageDraw.Draw(img)
                
                try:
                    font = ImageFont.truetype("cour.ttf", int(12 * scale))
                except Exception:
                    font = ImageFont.load_default()
                        
                y = int(10 * scale)
                if logo_img:
                    img.paste(logo_img, ((img_width - logo_img.width) // 2, y))
                    y += logo_height + int(10 * scale)
                    
                for line in lines:
                    draw.text((int(10 * scale), y), line, fill='black', font=font)
                    y += line_spacing
                    
                hDC.StartDoc("Ticket Pikta")
                hDC.StartPage()
                
                dib = ImageWin.Dib(img)
                dib.draw(hDC.GetHandleOutput(), (0, 0, img_width, img_height))
                
                hDC.EndPage()
                hDC.EndDoc()
                hDC.DeleteDC()
            else:
                import tempfile
                fd, path = tempfile.mkstemp(suffix=".txt")
                with os.fdopen(fd, 'w') as f:
                    f.write(text)
                messagebox.showinfo("Info", "Impresión gráfica solo disponible en Windows")
        except Exception as e:
            logging.error(f"No se pudo imprimir el ticket gráfico: {e}")

    def show_invoice_popup(self, text):
        """Muestra una ventana emergente con la factura y opción de imprimir."""
        win = tk.Toplevel(self)
        win.title("Factura de Venta")
        win.geometry("400x600")
        
        # Frame para logo
        logo_frame = ttk.Frame(win)
        logo_frame.pack(pady=10)
        
        logo = load_image(os.path.join('Imagenes', 'pikta2.png'), size=(100, 100))
        if logo:
            lbl_logo = ttk.Label(logo_frame, image=logo)
            lbl_logo.image = logo
            lbl_logo.pack()

        txt = tk.Text(win, font=("Courier", 10), padx=20, pady=10)
        txt.insert('end', text)
        txt.config(state='disabled')
        txt.pack(fill='both', expand=True)
        
        btn_frame = ttk.Frame(win, padding=10)
        btn_frame.pack(fill='x')
        
        def print_ticket():
            self.print_graphical_ticket(text, "factura")
            win.destroy()

        ttk.Button(btn_frame, text="IMPRIMIR TICKET", command=print_ticket, bootstyle="success").pack(side='left', fill='x', expand=True, padx=5)
        ttk.Button(btn_frame, text="CERRAR", command=win.destroy, bootstyle="danger").pack(side='right', fill='x', expand=True, padx=5)


    def add_more_to_table(self):
        """Prepara el sistema para agregar más productos a un pedido de mesa ya existente."""
        sel = self.unpaid_tree.selection()
        if not sel:
            messagebox.showwarning('Aviso', 'Seleccione una mesa primero')
            return
            
        item = self.unpaid_tree.item(sel[0])
        order_id = item['values'][0]
        mesa = item['values'][2]
        
        # Cambiar a la pestaña de Venta Directa
        self.pos_notebook.select(0)
        
        # Guardar en memoria que estamos editando una mesa
        self.editing_table_id = order_id
        messagebox.showinfo("Modo Edición", f"ESTÁ EDITANDO LA {mesa}.\n\nAgregue los productos extras y presione 'CONFIRMAR PEDIDO' para guardarlos en la cuenta de la mesa.")

    def update_existing_order(self):
        """Actualiza un pedido existente con los nuevos productos del carrito."""
        if not self.cart: return
        
        try:
            # Obtener items y estado actual
            res = self.db.fetch_one("SELECT items, mesa, estado FROM pedidos WHERE id=?", (self.editing_table_id,))
            if not res: return
            
            current_items = json.loads(res[0])
            mesa = res[1]
            estado_actual = res[2]
            
            # Añadir nuevos
            new_items = [{'id': item['product'][0], 'nombre': item['product'][1] + (" (EXTRA)" if estado_actual == 'LISTO' else ""), 'precio': item['product'][2], 'qty': item['qty']} for item in self.cart]
            updated_items = current_items + new_items
            new_total = sum(p.get('precio', p.get('precio_unitario', 0)) * p.get('qty', p.get('cantidad', 1)) for p in updated_items)
            
            self.db.execute("UPDATE pedidos SET items=?, subtotal=?, total=? WHERE id=?", 
                            (json.dumps(updated_items, ensure_ascii=False), new_total, new_total, self.editing_table_id))
            
            # Si el pedido ya fue despachado de cocina, crear un ticket oculto para que cocina vea los extras
            if estado_actual == 'LISTO':
                items_kds = json.dumps(new_items, ensure_ascii=False)
                numero = f"EX-{datetime.now().strftime('%H%M%S')}"
                created_at = datetime.now().isoformat()
                usuario_id = self.user.get('id') if self.user else None
                default_min = int(PREP_DURATION.total_seconds() // 60)
                try:
                    order_prep = max([(item['product'][5] if (len(item['product']) > 5 and item['product'][5] is not None) else default_min) for item in self.cart])
                except Exception:
                    order_prep = default_min

                # pagado=1 oculta este ticket de la caja, pero KDS lo verá porque estado='RECIBIDO'
                self.db.execute('INSERT INTO pedidos (numero, items, subtotal, total, estado, canal, usuario_id, created_at, mesa, pagado, preparacion_duracion) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                (numero, items_kds, 0, 0, 'RECIBIDO', 'EXTRA', usuario_id, created_at, mesa, 1, order_prep))
            
            # Registrar en auditoría
            self.db.audit_log('pedidos', 'UPDATE', self.user.get('username'), f'Productos extras añadidos a {mesa}', new=new_items)
            
            messagebox.showinfo("Éxito", f"Productos añadidos correctamente a la {mesa}.")
            
            # Limpiar estado
            self.cart.clear()
            self.update_cart_display()
            self.editing_table_id = None
            
            # Volver a pestaña de cobros
            self.pos_notebook.select(1)
            self.refresh_unpaid_orders()
            
        except Exception as e:
            logging.error(f"Error al actualizar mesa: {e}")
            messagebox.showerror("Error", "No se pudo actualizar la mesa.")

    def render_products(self):
        """Genera dinámicamente las tarjetas de productos según la categoría."""
        # Limpiar productos anteriores
        for w in self.products_frame.winfo_children():
            w.destroy()
        
        # Obtener productos de la base de datos
        products = self.db.fetch_all('SELECT id, nombre, precio, categoria, emoji, prep_duration FROM productos_menu')
        filtered = [p for p in products if (p[3] or '').strip() == self.selected_category.get()]
        
        if not filtered:
            ttk.Label(self.products_frame, text="No hay productos en esta categoría", padding=20).pack()
            return

        # Dibujar productos en un grid de 5 columnas para aprovechar el espacio horizontal
        cols = 5
        for i in range(cols): self.products_frame.columnconfigure(i, weight=1)
        product_btns = []
        for idx, p in enumerate(filtered):
            r, c = divmod(idx, cols)
            card = ttk.Frame(self.products_frame, bootstyle="light", padding=15)
            card.grid(row=r, column=c, padx=12, pady=12, sticky='nsew')
            
            img = None
            img_path = find_product_image_path(p[1])
            if img_path:
                img = load_image(img_path, size=(140, 110))
            
            if img:
                lbl = ttk.Label(card, image=img, bootstyle="inverse-light")
                lbl.image = img
                lbl.pack(pady=(0, 10))
            else:
                ttk.Label(card, text=p[4] or '🍽', font=(None, 60), bootstyle="inverse-light").pack(pady=(0, 10))
                
            # Para que el texto sea oscuro sobre el fondo claro
            tk.Label(card, text=p[1], font=(None, 12, 'bold'), fg='#2b3e50', bg='#f8f9fa', wraplength=120, justify='center').pack(pady=(5, 5))
            price_lbl = f"${p[2]:.2f}"
            if p[5]:
                price_lbl += f" • {p[5]}m"
            
            # Etiqueta de precio con fondo oscuro y texto blanco
            price_box = tk.Label(card, text=price_lbl, font=(None, 14, 'bold'), bg='#2b3e50', fg='white', padx=8, pady=2)
            price_box.pack(pady=(0, 10))
            
            # Botón para añadir al carrito (más grande)
            btn = ttk.Button(card, text='Añadir', command=lambda pid=p: self.add_product(pid), bootstyle="info", cursor="hand2", takefocus=True, padding=8)
            btn.pack(fill='x')
            btn.bind('<Return>', lambda e, pid=p: self.add_product(pid))
            product_btns.append(btn)
            
            # Navegación por flechas entre botones de productos
            def make_pos_nav(idx):
                def nav(e):
                    if e.keysym == 'Left' and idx > 0: product_btns[idx-1].focus_set()
                    elif e.keysym == 'Right' and idx < len(product_btns)-1: product_btns[idx+1].focus_set()
                    elif e.keysym == 'Up' and idx >= cols: product_btns[idx-cols].focus_set()
                    elif e.keysym == 'Down' and idx + cols < len(product_btns): product_btns[idx+cols].focus_set()
                return nav
            
            btn.bind("<Left>", make_pos_nav(idx))
            btn.bind("<Right>", make_pos_nav(idx))
            btn.bind("<Up>", make_pos_nav(idx))
            btn.bind("<Down>", make_pos_nav(idx))

        # Configurar pesos de columnas para que sean uniformes
        for i in range(cols):
            self.products_frame.columnconfigure(i, weight=1)

    def add_product(self, product):
        """Agrega un producto a la lista del carrito."""
        for item in self.cart:
            if item['product'][0] == product[0]: # mismo ID
                item['qty'] += 1
                self.update_cart_display()
                return
        self.cart.append({'product': product, 'qty': 1})
        self.update_cart_display()

    def remove_selected(self):
        """Elimina el producto seleccionado en la lista del carrito."""
        sel = self.cart_tree_venta.selection()
        if not sel: return
        item_id = sel[0]
        idx = self.cart_tree_venta.index(item_id)
        del self.cart[idx]
        self.update_cart_display()

    def on_cart_venta_double_click(self, event):
        """Permite editar la cantidad de un producto directamente en la celda de Venta Directa."""
        region = self.cart_tree_venta.identify_region(event.x, event.y)
        if region != "cell": return
        
        column = self.cart_tree_venta.identify_column(event.x)
        if column != "#2": return # Solo permitir editar la columna 'Cant'
        
        item_id = self.cart_tree_venta.identify_row(event.y)
        if not item_id: return
        
        idx = self.cart_tree_venta.index(item_id)
        x, y, width, height = self.cart_tree_venta.bbox(item_id, column)
        
        curr_qty = self.cart[idx]['qty']
        
        entry = ttk.Entry(self.cart_tree_venta, justify='center')
        entry.insert(0, str(curr_qty))
        entry.select_range(0, 'end')
        entry.focus_set()
        
        entry.place(x=x, y=y, width=width, height=height)
        
        def save_edit(event=None):
            try:
                new_qty = int(entry.get())
                if new_qty < 1: raise ValueError
                self.cart[idx]['qty'] = new_qty
                self.update_cart_display()
            except ValueError:
                pass
            finally:
                entry.destroy()

        entry.bind('<Return>', save_edit)
        entry.bind('<FocusOut>', save_edit)
        entry.bind('<Escape>', lambda e: entry.destroy())

    def update_cart_display(self):
        """Refresca la visualización de la lista del carrito y calcula el total."""
        for r in self.cart_tree_venta.get_children():
            self.cart_tree_venta.delete(r)
        
        total = 0
        for item in self.cart:
            p = item['product']
            qty = item['qty']
            subtotal = p[2] * qty
            self.cart_tree_venta.insert('', 'end', values=(p[1], f"x{qty}", f"${p[2]:.2f}", f"${subtotal:.2f}"))
            total += subtotal
            
        self.total_label.config(text=f'Total: ${total:.2f}')

    def process_order(self):
        """Guarda el pedido en la base de datos o actualiza uno existente si estamos en modo edición."""
        if not self.cart:
            messagebox.showinfo('Aviso', 'El carrito está vacío')
            return
        
        # Si estamos en modo edición de mesa, llamamos a la lógica de actualización
        if self.editing_table_id:
            self.update_existing_order()
            return
        
        # Preparar datos del pedido
        items_list = [{'id': item['product'][0], 'nombre': item['product'][1], 'precio': item['product'][2], 'qty': item['qty']} for item in self.cart]
        items = json.dumps(items_list, ensure_ascii=False)
        subtotal = sum((p.get('precio', 0) * p.get('qty', 1)) for p in items_list)
        total = subtotal
        
        try:
            # Generar número de pedido único basado en fecha/hora
            canal = self.order_channel.get()
            numero = f"{canal[:3].upper()}-{datetime.now().strftime('%H%M%S')}"
            created_at = datetime.now().isoformat()
            usuario_id = self.user.get('id') if self.user else None
            sesion_id = self.session_id
            # Según requerimiento: Venta Directa NO procesa pago, solo pedido pendiente
            pagado = 0
            metodo_pago = None

            # Si es CAJA pero no hay sesión abierta, solicitar abrir caja
            if canal == 'CAJA' and not sesion_id:
                if messagebox.askyesno('Abrir Caja', 'No hay caja abierta. ¿Desea abrirla ahora?'):
                    self.open_caja()
                    sesion_id = self.session_id

            # Calcular duración de preparación del pedido (max de items)
            default_min = int(PREP_DURATION.total_seconds() // 60)
            try:
                order_prep = max([(item['product'][5] if (len(item['product']) > 5 and item['product'][5] is not None) else default_min) for item in self.cart])
            except Exception:
                order_prep = default_min

            cliente_nombre = None
            cliente_telefono = None
            if canal == 'LLEVAR':
                cn = simpledialog.askstring('Cliente', 'Nombre del cliente (opcional):', parent=self)
                ct = simpledialog.askstring('Cliente', 'Teléfono del cliente (opcional):', parent=self)
                if cn or ct:
                    enc = DataEncryption()
                    if cn: cliente_nombre = enc.encrypt(cn)
                    if ct: cliente_telefono = enc.encrypt(ct)

            # Insertar en la base de datos, incluyendo estado de pago y duración de preparación
            self.db.execute('''INSERT INTO pedidos (numero, cliente_nombre, cliente_telefono, items, subtotal, descuento, total, estado, canal, usuario_id, sesion_id, metodo_pago, pagado, preparacion_duracion, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (numero, cliente_nombre, cliente_telefono, items, subtotal, 0.0, total, 'RECIBIDO', canal, usuario_id, sesion_id, metodo_pago, pagado, order_prep, created_at))
            
            # 4. Mandar a imprimir comanda (Ticket de Pedido) automáticamente
            try:
                comanda_text = f"*** COMANDA DE PEDIDO ***\n"
                comanda_text += f"ORDEN: {numero}\n"
                comanda_text += f"TIPO:  {canal}\n"
                comanda_text += f"MESA:  {'VENTA DIRECTA' if canal != 'LLEVAR' else 'PARA LLEVAR'}\n"
                comanda_text += f"FECHA: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
                comanda_text += "-" * 30 + "\n"
                for it in items_list:
                    comanda_text += f"{it['qty']:<3} {it['nombre'][:20]}\n"
                comanda_text += "-" * 30 + "\n"
                comanda_text += "    CONTROL DE PEDIDO\n"

                cfg = PrinterConfig()
                printer_name = cfg.resolve_printer("comanda")
                if printer_name and WIN32_PRINT_AVAILABLE:
                    import tempfile
                    fd, path = tempfile.mkstemp(suffix=".txt")
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(comanda_text)
                    win32api.ShellExecute(0, "printto", path, f'"{printer_name}"', ".", 0)
                else:
                    fd, path = tempfile.mkstemp(suffix=".txt")
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(comanda_text)
                    if sys.platform == "win32":
                        os.startfile(path, "print")
            except Exception as print_err:
                logging.error(f"Error al imprimir comanda: {print_err}")

            messagebox.showinfo('Éxito', f'Pedido {canal} procesado correctamente.\nNúmero: {numero}')

            # Registrar en auditoría
            self.db.audit_log('pedidos', 'INSERT', self.user.get('username'), f'Pedido {canal} creado: {numero}', new=items_list)
            
        except Exception as e:
            logging.error(f'Error al procesar pedido POS: {e}')
            messagebox.showerror('Error', 'No se pudo crear el pedido')
        
        # Limpiar carrito después de la venta
        self.cart.clear()
        self.update_cart_display()

    def open_caja(self):
        """Inicia una nueva sesión de caja con un monto inicial."""
        # Verificar primero en memoria
        if self.session_id:
            messagebox.showinfo('Caja', 'Ya hay una sesión de caja abierta')
            return
            
        # Verificar en base de datos por si acaso (evitar duplicados por reinicio)
        last_session = self.db.fetch_one(
            "SELECT id FROM caja_sesiones WHERE usuario_id = ? AND estado = 'ABIERTO' ORDER BY id DESC LIMIT 1",
            (self.user.get('id'),)
        )
        if last_session:
            self.session_id = last_session[0]
            messagebox.showinfo('Caja', 'Se ha recuperado su sesión de caja abierta anteriormente.')
            return

        inicial = simpledialog.askfloat('Abrir Caja', 'Monto inicial en caja:', minvalue=0.0)
        if inicial is None: return # El usuario canceló el diálogo
        
        usuario_id = self.user.get('id') if self.user else None
        inicio = datetime.now().isoformat()
        try:
            cur = self.db.execute('INSERT INTO caja_sesiones (usuario_id, inicio, inicial, monto_apertura, estado) VALUES (?,?,?,?,?)', 
                                (usuario_id, inicio, inicial, inicial, 'ABIERTO'))
            self.session_id = cur.lastrowid
            messagebox.showinfo('Caja', f'Caja abierta exitosamente (ID {self.session_id})')
        except Exception as e:
            logging.exception(f'Error al abrir caja: {e}')
            messagebox.showerror('Error', 'No se pudo abrir la caja')

    def cerrar_caja(self):
        """Finaliza la sesión de caja, calcula totales, muestra reporte y lo imprime."""
        if not self.session_id:
            messagebox.showwarning('Caja', 'No hay sesión de caja abierta')
            return
            
        try:
            # Reemplazar _ensure_column por un chequeo manual seguro
            try:
                columns = self.db.fetch_all("PRAGMA table_info(caja_sesiones)")
                col_names = [col[1] for col in columns]
                if 'ingresos_efectivo' not in col_names:
                    self.db.execute("ALTER TABLE caja_sesiones ADD COLUMN ingresos_efectivo REAL DEFAULT 0")
                if 'ingresos_otros' not in col_names:
                    self.db.execute("ALTER TABLE caja_sesiones ADD COLUMN ingresos_otros REAL DEFAULT 0")
                if 'reporte_texto' not in col_names:
                    self.db.execute("ALTER TABLE caja_sesiones ADD COLUMN reporte_texto TEXT")
            except Exception as e:
                logging.error(f"Error alterando caja_sesiones (seguro continuar): {e}")

            cierre_at = datetime.now().isoformat()
            # Obtener todas las ventas pagadas realizadas en esta sesión
            rows = self.db.fetch_all('SELECT numero, total, created_at, metodo_pago FROM pedidos WHERE sesion_id = ? AND pagado = 1', (self.session_id,))
            
            sum_efectivo = sum(float(r[1] or 0) for r in rows if r[3] == 'EFECTIVO')
            sum_otros = sum(float(r[1] or 0) for r in rows if r[3] != 'EFECTIVO')
            sum_total = sum_efectivo + sum_otros

            # Obtener monto inicial
            caja_row = self.db.fetch_one('SELECT inicial FROM caja_sesiones WHERE id = ?', (self.session_id,)) or (0.0,)
            inicial = float(caja_row[0] or 0)
            
            # Generar formato de Ticket idéntico al solicitado
            ahora = datetime.now()
            fecha_str = ahora.strftime('%d/%m/%Y %H:%M:%S')
            user_name = self.user.get('username', 'Cajero')
            user_id = self.user.get('id', 0)
            
            lines = []
            lines.append("*" * 42)
            lines.append("      INFORME DE CIERRE DE CAJA       ")
            lines.append("*" * 42)
            lines.append(f"Cierre:  {fecha_str}")
            lines.append(f"Cajero:  ID {user_id} - {user_name}")
            lines.append(f"Caja:    1")
            lines.append(f"Sesión:  {self.session_id}")
            lines.append("-" * 42)
            lines.append(f"{'TICKET':<15} {'FECHA':<20} {'TOTAL':>5}")
            lines.append("-" * 42)
            
            for r in rows:
                numero = r[0]
                total = float(r[1] or 0)
                try:
                    t_str = datetime.fromisoformat(r[2]).strftime('%H:%M:%S')
                except Exception:
                    t_str = "00:00:00"
                lines.append(f"{numero:<15} {t_str:<20} {total:>5.2f}")
                
            lines.append("-" * 42)
            lines.append(f"{'Total EFECTIVO':<36} {sum_efectivo:>5.2f}")
            if sum_otros > 0:
                lines.append(f"{'Total OTROS (Tarj/Transf)':<36} {sum_otros:>5.2f}")
            lines.append("=" * 42)
            lines.append(f"{'Monto Inicial:':<36} {inicial:>5.2f}")
            lines.append(f"{'Total Ventas Turno:':<36} {sum_total:>5.2f}")
            lines.append("-" * 42)
            lines.append(f"{'TOTAL EN CAJA (Efectivo):':<36} {sum_efectivo + inicial:>5.2f}")
            lines.append("=" * 42)
            lines.append(f"{'Nº Total de Tickets:':<36} {len(rows):>5}")
            lines.append("*" * 42)
            lines.append("      SISTEMA POS PIK'TA - 2026       ")
            lines.append("*" * 42)
            
            reporte_texto = "\n".join(lines)
            
            try:
                # Actualizar estado de la sesión a CERRADO y guardar reporte
                self.db.execute('UPDATE caja_sesiones SET estado = ?, cierre_total = ?, cierre_at = ?, reporte_texto = ?, ingresos_efectivo = ?, ingresos_otros = ? WHERE id = ?', 
                                ('CERRADO', sum_total, cierre_at, reporte_texto, sum_efectivo, sum_otros, self.session_id))
                
                # Abrir cajón al cierre
                self.open_cash_drawer()
                
                messagebox.showinfo('Caja', 'Caja cerrada exitosamente')
                
                # Mandar a imprimir el reporte automáticamente (solo si el cierre fue exitoso)
                if sys.platform == "win32":
                    try:
                        import tempfile
                        fd, path = tempfile.mkstemp(suffix=".txt")
                        with os.fdopen(fd, 'w', encoding='utf-8') as f:
                            f.write(reporte_texto)
                        cfg = PrinterConfig()
                        printer_name = cfg.resolve_printer("cierre")
                        if printer_name and WIN32_PRINT_AVAILABLE:
                            win32api.ShellExecute(0, "printto", path, f'"{printer_name}"', ".", 0)
                        else:
                            os.startfile(path, "print")
                    except Exception as print_err:
                        logging.error(f"Error al imprimir reporte de cierre: {print_err}")
            except Exception as e:
                logging.error(f"Error al actualizar la base de datos durante el cierre: {e}")
                messagebox.showerror('Error', f"Error al guardar cierre de caja: {e}")
        except Exception as e:
            logging.exception('Error general en cierre de caja o imprimir ticket')
            messagebox.showerror('Error', f'Error general al cerrar la caja: {str(e)}')

        # Limpiar la sesión actual (no mostramos el reporte en pantalla, solo se imprime y guarda)
        self.session_id = None

    def show_report(self, text):
        """Muestra una pantalla con el resumen del cierre de caja."""
        for w in self.products_frame.winfo_children():
            w.destroy()
        frm = ttk.Frame(self.products_frame, padding=20)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text="REPORTE DE CIERRE", font=(None, 14, 'bold')).pack(pady=10)
        t = tk.Text(frm, height=15, width=50)
        t.insert('1.0', text)
        t.config(state='disabled')
        t.pack(pady=10)
        ttk.Button(frm, text='Regresar al Menú', command=self.render_products, bootstyle="info").pack(pady=10)

    def notify_order_ready(self, text='¡Pedido Listo!'):
        """Muestra un indicador visual temporal en la cabecera para notificar pedidos listos y reproduce sonido."""
        try:
            # Reproducir sonido nativo en Windows
            try:
                import ctypes
                sound_path = os.path.abspath(os.path.join('Imagenes', 'sonidos y alertas', 'Sonido de_ Campana de servicio.mp3'))
                if os.path.exists(sound_path) and sys.platform == "win32":
                    ctypes.windll.winmm.mciSendStringW('close myalert', None, 0, None)
                    ctypes.windll.winmm.mciSendStringW(f'open "{sound_path}" type mpegvideo alias myalert', None, 0, None)
                    ctypes.windll.winmm.mciSendStringW('play myalert', None, 0, None)
            except Exception as e:
                logging.error(f"Error reproduciendo alerta: {e}")

            # Evitar duplicados
            if hasattr(self, '_notify_lbl') and getattr(self, '_notify_lbl') and str(getattr(self, '_notify_lbl')):
                try:
                    self._notify_lbl.config(text=text)
                    return
                except Exception: pass

            self._notify_lbl = ttk.Label(self.header, text=text, bootstyle='danger', padding=8)
            self._notify_lbl.pack(side='right', padx=5)

            # Parpadeo simple: alternar visibilidad un par de veces
            def blink(count=0):
                try:
                    if not hasattr(self, '_notify_lbl'): return
                    if count >= 6:
                        try:
                            self._notify_lbl.destroy()
                            delattr(self, '_notify_lbl')
                        except Exception:
                            pass
                        return
                    current = self._notify_lbl.winfo_viewable()
                    if current:
                        self._notify_lbl.pack_forget()
                    else:
                        self._notify_lbl.pack(side='right', padx=5)
                    self.after(400, lambda: blink(count+1))
                except Exception:
                    pass

            blink(0)
        except Exception:
            logging.exception('Error mostrando notificación visual en POS')


class MeseroFrame(tk.Canvas):
    """
    Interfaz para Meseros.
    Permite realizar pedidos asignados a mesas o para llevar.
    """
    def __init__(self, parent, db: DatabaseManager, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(parent, bg=BG, highlightthickness=0, *args, **kwargs)
        self.db = db
        self.user = user
        self.cart = []
        self.selected_mesa = tk.StringVar(value="Mesa 1")
        
        # Logo de Fondo en Mesero
        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path)
            def draw_mes_bg(e):
                self.delete("bg")
                cw, ch = e.width, e.height
                if cw < 10 or ch < 10: return
                img_res = self.bg_raw.resize((cw, ch), Image.LANCZOS)
                self.bg_photo = ImageTk.PhotoImage(img_res)
                self.create_image(cw//2, ch//2, image=self.bg_photo, tags="bg")
                self.tag_lower("bg")
            self.bind("<Configure>", draw_mes_bg)

        # --- Contenedores para Secciones ---
        # Cabecera
        self.header = ttk.Frame(self, bootstyle="warning", padding=15)
        self.header_win = self.create_window(0, 0, window=self.header, anchor='nw', tags="header")
        
        # Cuerpo
        self.body = ttk.Frame(self, padding=10)
        self.body_win = self.create_window(0, 70, window=self.body, anchor='nw', tags="body")
        
        def resize_mes_content(e):
            self.itemconfig("header", width=e.width)
            self.itemconfig("body", width=e.width, height=e.height - 70)
            if hasattr(self, 'bg_raw'): draw_mes_bg(e)
            
        self.bind("<Configure>", resize_mes_content)

        # --- Contenido de la Cabecera ---
        mesero_img = load_image(os.path.join('Imagenes', 'user.png'), size=(60, 60))
        if mesero_img:
            lbl = ttk.Label(self.header, image=mesero_img, bootstyle="inverse-warning")
            lbl.image = mesero_img
            lbl.pack(side='left', padx=10)
        
        ttk.Label(self.header, text='🍽️ MÓDULO DE MESERO', font=(None, 24, 'bold'), bootstyle="inverse-warning").pack(side='left', padx=10)
        ttk.Button(self.header, text='Regresar', command=lambda: self.master.select(0), bootstyle="secondary-outline", cursor="hand2", padding=10).pack(side='right', padx=5)

        # --- Cuerpo ---
        # Lado izquierdo: Mesas y Productos
        self.left = ttk.Frame(self.body)
        self.left.pack(side='left', fill='both', expand=True, padx=(0, 10))

        # Selección de Mesa / Para Llevar
        mesa_frame = tk.LabelFrame(self.left, text="Seleccionar Mesa / Destino", bg=BG, fg="white", font=(None, 11, "bold"))
        mesa_frame.pack(fill='x', pady=(0, 15), padx=10)
        
        mesas = ["Mesa 1", "Mesa 2", "Mesa 3", "Mesa 4", "Mesa 5", "Mesa 6", "Para Llevar"]
        for m in mesas:
            ttk.Radiobutton(mesa_frame, text=m, variable=self.selected_mesa, value=m, 
                           bootstyle="warning-toolbutton", padding=8).pack(side='left', padx=5)

        # Filtro de categorías dinámico
        self.categories = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        if not self.categories: self.categories = ['General']
        self.selected_category = tk.StringVar(value=self.categories[0])
        self.cat_frame = ttk.Frame(self.left)
        self.cat_frame.pack(fill='x', pady=(0, 15))
        self.refresh_category_buttons()

    def refresh_category_buttons(self):
        for child in self.cat_frame.winfo_children():
            child.destroy()
        
        # Recargar de la BD
        self.categories = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        if not self.categories: self.categories = ['General']
        if self.selected_category.get() not in self.categories:
            self.selected_category.set(self.categories[0])

        for c in self.categories:
            ttk.Radiobutton(self.cat_frame, text=c, variable=self.selected_category, value=c, 
                           command=self.render_products, bootstyle="warning-outline-toolbutton", padding=10).pack(side='left', padx=5)

        # Contenedor de productos
        products_wrapper = ttk.Frame(self.left)
        products_wrapper.pack(side="left", fill="both", expand=True)
        
        self.products_canvas = tk.Canvas(products_wrapper, bg=BG, highlightthickness=0)
        self.scrollbar = AutoScrollbar(products_wrapper, orient="vertical", command=self.products_canvas.yview)
        self.products_frame = ttk.Frame(self.products_canvas)

        self.products_frame.bind("<Configure>", lambda e: self.products_canvas.configure(scrollregion=self.products_canvas.bbox("all")))
        self.products_frame_id = self.products_canvas.create_window((0, 0), window=self.products_frame, anchor="nw")
        self.products_canvas.bind('<Configure>', lambda e: self.products_canvas.itemconfig(self.products_frame_id, width=e.width))
        self.products_canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.products_canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        products_wrapper.columnconfigure(0, weight=1)
        products_wrapper.rowconfigure(0, weight=1)

        # Lado derecho: Resumen
        right = ttk.Frame(self.body, width=500, bootstyle="secondary")
        right.pack(side='right', fill='y')
        right.pack_propagate(False)
        
        ttk.Label(right, text='PEDIDO ACTUAL', font=(None, 12, 'bold'), bootstyle="inverse-secondary", padding=10).pack(fill='x')
        
        # --- Treeview para Pedido de Mesero ---
        tree_frame_mesero = ttk.Frame(right)
        tree_frame_mesero.pack(fill='both', expand=True, padx=10, pady=10)
        
        cols_mesero = ('Producto', 'Cant', 'Precio', 'Subtotal')
        self.cart_tree_mesero = ttk.Treeview(tree_frame_mesero, columns=cols_mesero, show='headings')
        for col in cols_mesero:
            self.cart_tree_mesero.heading(col, text=col)
            self.cart_tree_mesero.column(col, anchor='center', width=60)
        self.cart_tree_mesero.column('Producto', width=200, anchor='w')
        self.cart_tree_mesero.pack(side='left', fill='both', expand=True)
        
        cart_scroll_mesero = ttk.Scrollbar(tree_frame_mesero, orient='vertical', command=self.cart_tree_mesero.yview)
        self.cart_tree_mesero.configure(yscrollcommand=cart_scroll_mesero.set)
        cart_scroll_mesero.pack(side='right', fill='y')
        
        self.cart_tree_mesero.bind("<Double-1>", self.on_cart_mesero_double_click)
        
        self.total_label = ttk.Label(right, text='Total: $0.00', font=(None, 14, 'bold'), bootstyle="inverse-secondary", padding=10)
        self.total_label.pack(fill='x')

        self.is_to_go = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Marcar productos como PARA LLEVAR", variable=self.is_to_go, bootstyle="warning-round-toggle").pack(pady=5, padx=10, anchor='w')

        ttk.Button(right, text='Quitar Item', command=self.remove_selected, bootstyle="danger", cursor="hand2").pack(fill='x', padx=10, pady=5)
        ttk.Button(right, text='ENVIAR A COCINA', command=self.process_order, bootstyle="warning", cursor="hand2", padding=10).pack(fill='x', padx=10, pady=10)

        self.render_products()

    def render_products(self):
        for w in self.products_frame.winfo_children(): w.destroy()
        products = self.db.fetch_all('SELECT id, nombre, precio, categoria, emoji, prep_duration FROM productos_menu')
        filtered = [p for p in products if (p[3] or '').strip() == self.selected_category.get()]
        # Usar 5 columnas para aprovechar el espacio horizontal y evitar scroll innecesario
        cols = 5
        for i in range(cols): self.products_frame.columnconfigure(i, weight=1)
        product_btns = []
        for idx, p in enumerate(filtered):
            r, c = divmod(idx, cols)
            card = ttk.Frame(self.products_frame, bootstyle="light", padding=15)
            card.grid(row=r, column=c, padx=12, pady=12, sticky='nsew')
            
            img = None
            img_path = find_product_image_path(p[1])
            if img_path:
                img = load_image(img_path, size=(140, 110))
            
            if img:
                lbl = ttk.Label(card, image=img, bootstyle="inverse-light")
                lbl.image = img
                lbl.pack(pady=(0, 10))
            else:
                ttk.Label(card, text=p[4] or '🍽', font=(None, 60), bootstyle="inverse-light").pack(pady=(0, 10))
                
            ttk.Label(card, text=p[1], font=(None, 14, 'bold'), bootstyle="inverse-light", wraplength=140, justify='center').pack()
            price_lbl = f"${p[2]:.2f}"
            if p[5]:
                price_lbl += f" • {p[5]}m"
            ttk.Label(card, text=price_lbl, font=(None, 16), bootstyle="warning").pack(pady=5)
            btn = ttk.Button(card, text='Añadir', command=lambda pid=p: self.add_product(pid), bootstyle="warning", cursor="hand2", padding=8, takefocus=True)
            btn.pack(fill='x')
            btn.bind('<Return>', lambda e, pid=p: self.add_product(pid))
            product_btns.append(btn)

            # Navegación por flechas entre botones de productos
            def make_mesero_nav(idx):
                def nav(e):
                    if e.keysym == 'Left' and idx > 0: product_btns[idx-1].focus_set()
                    elif e.keysym == 'Right' and idx < len(product_btns)-1: product_btns[idx+1].focus_set()
                    elif e.keysym == 'Up' and idx >= cols: product_btns[idx-cols].focus_set()
                    elif e.keysym == 'Down' and idx + cols < len(product_btns): product_btns[idx+cols].focus_set()
                return nav
            
            btn.bind("<Left>", make_mesero_nav(idx))
            btn.bind("<Right>", make_mesero_nav(idx))
            btn.bind("<Up>", make_mesero_nav(idx))
            btn.bind("<Down>", make_mesero_nav(idx))
            
        for i in range(cols): self.products_frame.columnconfigure(i, weight=1)

    def add_product(self, product):
        for item in self.cart:
            if item['product'][0] == product[0]: # mismo ID
                item['qty'] += 1
                self.update_cart_display()
                return
        self.cart.append({'product': product, 'qty': 1})
        self.update_cart_display()

    def remove_selected(self):
        sel = self.cart_tree_mesero.selection()
        if not sel: return
        item_id = sel[0]
        idx = self.cart_tree_mesero.index(item_id)
        del self.cart[idx]
        self.update_cart_display()

    def on_cart_mesero_double_click(self, event):
        """Permite editar la cantidad de un producto directamente en la celda."""
        region = self.cart_tree_mesero.identify_region(event.x, event.y)
        if region != "cell": return
        
        column = self.cart_tree_mesero.identify_column(event.x)
        if column != "#2": return # Solo permitir editar la columna 'Cant'
        
        item_id = self.cart_tree_mesero.identify_row(event.y)
        if not item_id: return
        
        idx = self.cart_tree_mesero.index(item_id)
        x, y, width, height = self.cart_tree_mesero.bbox(item_id, column)
        
        curr_qty = self.cart[idx]['qty']
        
        entry = ttk.Entry(self.cart_tree_mesero, justify='center')
        entry.insert(0, str(curr_qty))
        entry.select_range(0, 'end')
        entry.focus_set()
        
        entry.place(x=x, y=y, width=width, height=height)
        
        def save_edit(event=None):
            try:
                new_qty = int(entry.get())
                if new_qty < 1: raise ValueError
                self.cart[idx]['qty'] = new_qty
                self.update_cart_display()
            except ValueError:
                pass
            finally:
                entry.destroy()

        entry.bind('<Return>', save_edit)
        entry.bind('<FocusOut>', save_edit)
        entry.bind('<Escape>', lambda e: entry.destroy())

    def update_cart_display(self):
        for r in self.cart_tree_mesero.get_children():
            self.cart_tree_mesero.delete(r)
            
        total = 0
        for item in self.cart:
            p = item['product']
            qty = item['qty']
            subtotal = p[2] * qty
            self.cart_tree_mesero.insert('', 'end', values=(p[1], f"x{qty}", f"${p[2]:.2f}", f"${subtotal:.2f}"))
            total += subtotal
            
        self.total_label.config(text=f'Total: ${total:.2f}')

    def process_order(self):
        if not self.cart:
            messagebox.showinfo('Aviso', 'El pedido está vacío')
            return
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                mesa = self.selected_mesa.get()
                to_go_suffix = " (Para Llevar)" if hasattr(self, 'is_to_go') and self.is_to_go.get() else ""
                items_list = [{'id': item['product'][0], 'nombre': item['product'][1] + to_go_suffix, 
                              'precio': item['product'][2], 'qty': item['qty']} for item in self.cart]
                total = sum((p.get('precio', 0) * p.get('qty', 1)) for p in items_list)
                
                # Usar transacción con bloqueo
                with self.db.get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")  # Lock exclusivo
                    cur = conn.cursor()
                    
                    # Buscar pedido existente con bloqueo FOR UPDATE
                    if mesa != "Para Llevar":
                        cur.execute("""
                            SELECT id, items, total, estado FROM pedidos 
                            WHERE mesa=? AND pagado=0 AND estado != 'PAGADO' 
                            ORDER BY created_at DESC LIMIT 1 
                        """, (mesa,))
                        unpaid_order = cur.fetchone()
                    else:
                        unpaid_order = None
                    
                    if unpaid_order:
                        order_id = unpaid_order[0]
                        current_items = json.loads(unpaid_order[1])
                        estado_actual = unpaid_order[3]
                        
                        if estado_actual == 'LISTO':
                            for item in items_list:
                                item['nombre'] = item['nombre'] + " (EXTRA)"
                        
                        updated_items = current_items + items_list
                        new_total = sum(p.get('precio', p.get('precio_unitario', 0)) * p.get('qty', p.get('cantidad', 1)) for p in updated_items)
                        
                        cur.execute("""
                            UPDATE pedidos SET items=?, subtotal=?, total=? 
                            WHERE id=? AND estado=?
                        """, (json.dumps(updated_items, ensure_ascii=False), new_total, new_total, order_id, estado_actual))
                        
                        if cur.rowcount == 0:
                            # Conflicto - otro usuario modificó el pedido
                            conn.rollback()
                            if attempt < max_retries - 1:
                                time.sleep(0.1)
                                continue
                            else:
                                raise Exception("El pedido fue modificado por otro usuario. Intente nuevamente.")
                        
                        # Si está LISTO, crear ticket extra
                        if estado_actual == 'LISTO':
                            items_kds = json.dumps(items_list, ensure_ascii=False)
                            numero = f"EX-{datetime.now().strftime('%H%M%S')}"
                            created_at = datetime.now().isoformat()
                            usuario_id = self.user.get('id') if self.user else None
                            default_min = int(PREP_DURATION.total_seconds() // 60)
                            try:
                                order_prep = max([(item['product'][5] if (len(item['product']) > 5 and item['product'][5] is not None) else default_min) for item in self.cart])
                            except Exception:
                                order_prep = default_min
                            
                            cur.execute("""
                                INSERT INTO pedidos 
                                (numero, items, subtotal, total, estado, canal, usuario_id, created_at, mesa, pagado, preparacion_duracion) 
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            """, (numero, items_kds, 0, 0, 'RECIBIDO', 'EXTRA', usuario_id, created_at, mesa, 1, order_prep))
                        
                        conn.commit()
                        messagebox.showinfo('Éxito', f'Productos añadidos exitosamente al pedido existente de la {mesa}')
                        break
                        
                    else:
                        # Crear nuevo pedido (código existente)
                        numero = f"ME-{datetime.now().strftime('%H%M%S')}"
                        items = json.dumps(items_list, ensure_ascii=False)
                        created_at = datetime.now().isoformat()
                        usuario_id = self.user.get('id') if self.user else None
                        
                        # Calcular duración de preparación del pedido
                        default_min = int(PREP_DURATION.total_seconds() // 60)
                        try:
                            order_prep = max([(item['product'][5] if (len(item['product']) > 5 and item['product'][5] is not None) else default_min) for item in self.cart])
                        except Exception:
                            order_prep = default_min
            
                        cur.execute("""
                            INSERT INTO pedidos 
                            (numero, items, subtotal, total, estado, canal, usuario_id, created_at, mesa, pagado, preparacion_duracion) 
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """, (numero, items, total, total, 'RECIBIDO', 'MESERO', usuario_id, created_at, mesa, 0, order_prep))
                        
                        conn.commit()
                        messagebox.showinfo('Éxito', f'Pedido de {mesa} enviado a cocina')
                        break
                        
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))  # Backoff exponencial
                    continue
                messagebox.showerror('Error', f'Error de base de datos: {e}')
                return
            except Exception as e:
                logging.error(f'Error al procesar pedido mesero: {e}')
                messagebox.showerror('Error', 'No se pudo enviar el pedido')
                return
        
        # Limpiar carrito después de éxito
        if hasattr(self, 'is_to_go'):
            self.is_to_go.set(False)
        self.cart.clear()
        self.update_cart_display()
        
        # Notificar a POS
        try:
            app = self.winfo_toplevel()
            if hasattr(app, 'notebook'):
                for i in range(app.notebook.index('end')):
                    if app.notebook.tab(i, 'text') == 'Caja / POS':
                        pos_frame = app.nametowidget(app.notebook.tabs()[i])
                        if hasattr(pos_frame, 'refresh_unpaid_orders'):
                            pos_frame.refresh_unpaid_orders()
                        break
        except Exception:
            logging.exception('Error notificando POS')


class KDSFrame(tk.Canvas):
    """
    Monitor de Cocina (KDS).
    Visualiza los pedidos pendientes y permite marcarlos como listos.
    """
    def __init__(self, parent, db: DatabaseManager, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(parent, bg=BG, highlightthickness=0, *args, **kwargs)
        self.db = db
        self.user = user
        self.last_order_count = 0
        self.cards = {} # {order_id: dict_of_widgets}
        
        # Logo de Fondo en KDS
        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path)
            def draw_kds_bg(e):
                self.delete("bg")
                cw, ch = e.width, e.height
                if cw < 10 or ch < 10: return
                img_res = self.bg_raw.resize((cw, ch), Image.LANCZOS)
                self.bg_photo = ImageTk.PhotoImage(img_res)
                self.create_image(cw//2, ch//2, image=self.bg_photo, tags="bg")
                self.tag_lower("bg")
            self.bind("<Configure>", draw_kds_bg)

        # --- Contenedores para Secciones ---
        self.header = ttk.Frame(self, bootstyle="warning", padding=15)
        self.header_win = self.create_window(0, 0, window=self.header, anchor='nw', tags="header")
        
        self.body = ttk.Frame(self, padding=10)
        self.body_win = self.create_window(0, 70, window=self.body, anchor='nw', tags="body")
        
        def resize_kds_content(e):
            self.itemconfig("header", width=e.width)
            self.itemconfig("body", width=e.width, height=e.height - 70)
            if hasattr(self, 'bg_raw'): draw_kds_bg(e)
            
        self.bind("<Configure>", resize_kds_content)

        # --- Contenido de la Cabecera ---
        kds_img = load_image(os.path.join('Imagenes', 'cocina.jpeg'), size=(60,60))
        if kds_img:
            lbl = ttk.Label(self.header, image=kds_img, bootstyle="inverse-warning")
            lbl.image = kds_img
            lbl.pack(side='left', padx=10)
            
        ttk.Label(self.header, text='🍳 MONITOR DE COCINA (KDS)', font=(None, 24, 'bold'), bootstyle="inverse-warning").pack(side='left', padx=10)
        ttk.Button(self.header, text='Regresar', command=lambda: self.master.select(0), bootstyle="secondary-outline", cursor="hand2", padding=10).pack(side='right', padx=5)
        ttk.Button(self.header, text='Refrescar', command=self.refresh, bootstyle="light-outline", cursor="hand2", padding=10).pack(side='right', padx=5)
        
        # --- Contenedor Principal con Scroll para las Tarjetas ---
        self.canvas = tk.Canvas(self.body, bg=BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        def configure_canvas(event):
            self.canvas.itemconfig(self.canvas_window, width=event.width)
        self.canvas.bind("<Configure>", configure_canvas)

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.refresh()
        self.auto_refresh_loop()

    def auto_refresh_loop(self):
        """Ciclo automático para actualizar los temporizadores y buscar nuevos pedidos."""
        self.refresh()
        self.after(5000, self.auto_refresh_loop) # Actualiza cada 5 segundos para reducir lag en Nube

    def refresh(self):
        """Consulta la base de datos y actualiza o crea las tarjetas de los pedidos."""
        # Traer pedidos que NO tengan estado 'LISTO'
        rows = self.db.fetch_all("SELECT id, numero, items, estado, mesa, preparacion_inicio, preparacion_duracion FROM pedidos WHERE estado NOT IN ('LISTO', 'CANCELADO') ORDER BY id ASC LIMIT 50")
        
        # Reproducir sonido si hay pedidos nuevos
        if len(rows) > self.last_order_count:
            play_sound_new_order()
        self.last_order_count = len(rows)
        
        current_ids = set()
        any_order_delayed = False
        
        for r in rows:
            pid = r[0]
            current_ids.add(pid)
            
            # Verificar si el pedido está retrasado
            if r[3] == 'PREPARANDO' and r[5]:
                try:
                    started = datetime.fromisoformat(r[5])
                    elapsed = datetime.now() - started
                    dur_min = r[6] if r[6] is not None else int(PREP_DURATION.total_seconds()//60)
                    if (timedelta(minutes=dur_min) - elapsed).total_seconds() <= 0:
                        any_order_delayed = True
                except Exception: pass
            
            if pid not in self.cards:
                self._create_card(r)
            else:
                self._update_card(pid, r)
                
        # Lógica para la alarma continua
        if any_order_delayed and not getattr(self, 'is_playing_alert', False):
            try:
                play_sound_delayed_order()
                self.is_playing_alert = True
            except Exception: pass
        elif not any_order_delayed and getattr(self, 'is_playing_alert', False):
            try:
                stop_sound_delayed_order()
                self.is_playing_alert = False
            except Exception: pass
                
        # Limpiar tarjetas de pedidos que ya no están (ej. pasaron a LISTO)
        for pid in list(self.cards.keys()):
            if pid not in current_ids:
                self.cards[pid]['frame'].destroy()
                del self.cards[pid]
                
        # Reposicionar tarjetas en forma de Grid (Mosaico) para que sean más pequeñas
        col_count = 3 # Mostrar 3 tarjetas por fila
        sorted_pids = sorted(list(current_ids))
        for i, pid in enumerate(sorted_pids):
            row = i // col_count
            col = i % col_count
            self.cards[pid]['frame'].grid(row=row, column=col, padx=15, pady=15, sticky='nw')

    def _create_card(self, r):
        pid, numero, items_str, estado, mesa, prep_start, prep_dur = r
        
        # Parse items
        try:
            items_obj = json.loads(items_str) if items_str else []
            item_names = '\n'.join([f"• {it.get('qty', it.get('cantidad', 1))}x {it.get('nombre')}" for it in items_obj])
        except Exception:
            item_names = items_str or "Sin detalles"
            
        mesa_info = f"MESA: {mesa}" if mesa else "PARA LLEVAR / CAJA"
        
        # Usamos Frame normal para evitar errores de bootstyle en LabelFrame
        card_frame = ttk.Frame(self.scrollable_frame, bootstyle="secondary", padding=15)
        
        # Cabecera de la tarjeta
        header = ttk.Frame(card_frame, bootstyle="secondary")
        header.pack(fill='x', pady=(0, 10))
        
        lbl_title = ttk.Label(header, text=f"Pedido #{pid} | {mesa_info}", font=(None, 14, 'bold'), bootstyle="inverse-secondary")
        lbl_title.pack(side='left', fill='x', expand=True)
        
        lbl_timer = ttk.Label(header, text="", font=(None, 12, 'bold'), bootstyle="inverse-secondary")
        lbl_timer.pack(side='right', padx=(10, 0))
        
        # Separador visual
        ttk.Separator(card_frame).pack(fill='x', pady=5)
        
        # Cuerpo de la tarjeta (Items)
        lbl_items = ttk.Label(card_frame, text=item_names, font=(None, 12), justify="left", wraplength=280, bootstyle="inverse-secondary")
        lbl_items.pack(anchor='w', pady=5)
        
        # Botón de acción (Más pequeño y discreto)
        btn_action = ttk.Button(card_frame, padding=8, cursor="hand2")
        btn_action.pack(fill='x', pady=(10, 0))
        
        # Guardar referencias
        self.cards[pid] = {
            'frame': card_frame,
            'header': header,
            'lbl_title': lbl_title,
            'lbl_timer': lbl_timer,
            'lbl_items': lbl_items,
            'btn_action': btn_action
        }
        
        self._update_card(pid, r)

    def _update_card(self, pid, r):
        """Actualiza la apariencia y el temporizador de una tarjeta existente."""
        _, numero, items_str, estado, mesa, prep_start, prep_dur = r
        widgets = self.cards[pid]
        
        time_info = ""
        btn_text = ""
        btn_style = ""
        btn_cmd = None
        
        # En lugar de colorear toda la tarjeta, solo coloreamos la cabecera y el texto
        # para que no sea tan "escandaloso"
        header_style = "secondary"

        if estado in ('RECIBIDO', 'COBRADO'):
            header_style = "info" # Celeste suave para la cabecera
            time_info = "Esperando..."
            btn_text = "▶ INICIAR PREPARACIÓN"
            btn_style = "info-outline"
            btn_cmd = lambda: self._advance_single_order(pid, 'RECIBIDO', prep_dur)
            
        elif estado == 'PREPARANDO':
            header_style = "warning" # Amarillo suave para la cabecera
            btn_text = "✔ EN PREPARACIÓN - FINALIZAR"
            btn_style = "warning-outline"
            btn_cmd = lambda: self._advance_single_order(pid, 'PREPARANDO', prep_dur)
            
            if prep_start:
                try:
                    started = datetime.fromisoformat(prep_start)
                    elapsed = datetime.now() - started
                    dur_min = prep_dur if prep_dur is not None else int(PREP_DURATION.total_seconds()//60)
                    remaining = timedelta(minutes=dur_min) - elapsed
                    
                    if remaining.total_seconds() <= 0:
                        time_info = "¡TIEMPO AGOTADO!"
                        header_style = "danger"
                        btn_style = "danger-outline"
                    else:
                        mins = int(remaining.total_seconds() // 60)
                        secs = int(remaining.total_seconds() % 60)
                        time_info = f"⏳ Quedan: {mins}m {secs}s"
                except Exception:
                    time_info = "Error de tiempo"

        # Aplicar colores sutiles
        # Mantenemos el fondo de la tarjeta en secondary (oscuro normal)
        widgets['frame'].config(bootstyle="secondary")
        widgets['header'].config(bootstyle=header_style)
        widgets['lbl_title'].config(bootstyle=f"inverse-{header_style}")
        widgets['lbl_timer'].config(text=time_info, bootstyle=f"inverse-{header_style}")
        
        # Actualizar los items en caso de que se hayan agregado productos extra
        try:
            items_obj = json.loads(items_str) if items_str else []
            item_names = '\n'.join([f"• {it.get('qty', it.get('cantidad', 1))}x {it.get('nombre')}" for it in items_obj])
        except Exception:
            item_names = items_str or "Sin detalles"
            
        # Asegurarse de que el texto interior use el mismo color base oscuro y esté actualizado
        widgets['lbl_items'].config(text=item_names, bootstyle="inverse-secondary")
        
        # Botón
        widgets['btn_action'].config(text=btn_text, bootstyle=btn_style, command=btn_cmd)

    def _advance_single_order(self, pid, current_state, prep_dur):
        """Avanza un pedido individual a su siguiente estado."""
        try:
            # Prevenir doble clic robusto (Debounce)
            import time
            if not hasattr(self, '_last_click'): self._last_click = {}
            now = time.time()
            if pid in self._last_click and now - self._last_click[pid] < 1.5:
                return
            self._last_click[pid] = now

            if pid in self.cards:
                self.cards[pid]['btn_action'].config(state='disabled')
                self.after(1500, lambda p=pid: self.cards[p]['btn_action'].config(state='normal') if hasattr(self, 'cards') and p in self.cards else None)

            res = self.db.fetch_one("SELECT numero, estado FROM pedidos WHERE id=?", (pid,))
            if not res: return
            numero = res[0]
            actual_state = res[1]

            # Solo avanzar si el estado en la BD coincide (evita saltos por clics acumulados)
            if current_state == 'RECIBIDO' and actual_state in ('RECIBIDO', 'COBRADO'):
                new_state = 'PREPARANDO'
                started_at = datetime.now().isoformat()
                default_min = int(PREP_DURATION.total_seconds() // 60)
                dur_val = prep_dur if prep_dur is not None else default_min
                self.db.execute('UPDATE pedidos SET estado=?, preparacion_inicio=?, preparacion_duracion=? WHERE id=?', (new_state, started_at, dur_val, pid))
            
            elif current_state == 'PREPARANDO' and actual_state == 'PREPARANDO':
                new_state = 'LISTO'
                self.db.execute('UPDATE pedidos SET estado=? WHERE id=?', (new_state, pid))
                try: play_sound_order_ready()
                except Exception: pass
                
                # Notificar al POS
                try:
                    app = self.winfo_toplevel()
                    if hasattr(app, 'notebook'):
                        for i in range(app.notebook.index('end')):
                            if app.notebook.tab(i, 'text') == 'Caja / POS':
                                pos_frame = app.nametowidget(app.notebook.tabs()[i])
                                if hasattr(pos_frame, 'refresh_unpaid_orders'):
                                    pos_frame.refresh_unpaid_orders()
                                    if hasattr(pos_frame, 'notify_order_ready'):
                                        try: pos_frame.notify_order_ready(f'Pedido Listo: {numero}')
                                        except Exception: pass
                                    try: play_sound_order_ready()
                                    except Exception: pass
                                break
                except Exception as e:
                    logging.exception('Error notificando POS: ' + str(e))
            
            self.refresh()
        except Exception as e:
            logging.error(f"Error en _advance_single_order: {e}")
            play_sound_error()

    def advance_order_state(self):
        pass

    def mark_ready(self):
        pass

class WhatsAppFrame(tk.Canvas):
    """
    Módulo de WhatsApp Business PIK'TA.
    Se abre automáticamente en una ventana profesional integrada.
    """
    def __init__(self, parent, db: DatabaseManager, *args, **kwargs):
        super().__init__(parent, bg=BG, highlightthickness=0, *args, **kwargs)
        self.db = db
        self.wa_process = None
        
        # Logo de Fondo en WhatsApp
        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path)
            self.last_bg_w, self.last_bg_h = 0, 0
            def draw_wa_bg(e):
                cw, ch = e.width, e.height
                if cw < 10 or ch < 10: return
                if cw == self.last_bg_w and ch == self.last_bg_h: return
                self.last_bg_w, self.last_bg_h = cw, ch
                self.delete("bg")
                img_res = self.bg_raw.resize((cw, ch), Image.LANCZOS)
                self.bg_photo = ImageTk.PhotoImage(img_res)
                self.create_image(cw//2, ch//2, image=self.bg_photo, tags="bg")
                self.tag_lower("bg")
            self.bind("<Configure>", draw_wa_bg)

        # --- Contenedores para Secciones ---
        self.header = ttk.Frame(self, bootstyle="success", padding=15)
        self.header_win = self.create_window(0, 0, window=self.header, anchor='nw', tags="header")
        
        self.body = ttk.Frame(self, padding=10)
        self.body_win = self.create_window(0, 70, window=self.body, anchor='nw', tags="body")
        
        def resize_wa_content(e):
            self.itemconfig("header", width=e.width)
            self.itemconfig("body", width=e.width, height=e.height - 70)
            if hasattr(self, 'bg_raw'): draw_wa_bg(e)
            
        self.bind("<Configure>", resize_wa_content)

        # --- Contenido de la Cabecera ---
        wa_img = load_image(os.path.join('Imagenes', 'WhatsApp.jpg'), size=(60, 60))
        if wa_img:
            lbl = ttk.Label(self.header, image=wa_img, bootstyle="inverse-success")
            lbl.image = wa_img
            lbl.pack(side='left', padx=10)
        
        ttk.Label(self.header, text='💬 WHATSAPP BUSINESS PIK\'TA', font=(None, 24, 'bold'), bootstyle="inverse-success").pack(side='left', padx=10)
        ttk.Button(self.header, text='Regresar', command=lambda: self.master.select(0), bootstyle="secondary-outline", cursor="hand2", padding=10).pack(side='right', padx=5)

        # --- Cuerpo Informativo ---
        info_container = tk.Frame(self.body, bg=BG)
        info_container.place(relx=0.5, rely=0.5, anchor='center')
        
        ttk.Label(info_container, text="WhatsApp Business se está ejecutando de forma integrada.", 
                 font=(None, 16, 'bold'), bootstyle="inverse-dark").pack(pady=10)
        ttk.Label(info_container, text="Gestione sus pedidos y clientes desde la ventana profesional de WhatsApp.", 
                 font=(None, 12), bootstyle="inverse-dark").pack(pady=5)
        
        ttk.Button(info_container, text='REABRIR WHATSAPP INTEGRADO', bootstyle="success", 
                  command=self.connect_wa, padding=15).pack(pady=20)
        
        # --- PRUEBA DE SONIDOS ---
        test_frame = tk.LabelFrame(info_container, text="Pruebas de Sonido del Sistema", bg=BG, fg="white", font=(None, 11, "bold"))
        test_frame.pack(pady=10, fill='x')
        
        # Usamos un frame interno para el padding ya que LabelFrame no lo soporta directamente en algunas versiones
        inner_test = ttk.Frame(test_frame, padding=10)
        inner_test.pack(fill='both', expand=True)
        
        ttk.Button(inner_test, text="🔔 Probar Nuevo Pedido", command=play_sound_new_order, bootstyle="info-outline").pack(side='left', padx=5)
        ttk.Button(inner_test, text="📢 Probar Pedido Listo", command=play_sound_order_ready, bootstyle="warning-outline").pack(side='left', padx=5)
        ttk.Button(inner_test, text="❌ Probar Error", command=play_sound_error, bootstyle="danger-outline").pack(side='left', padx=5)

    def connect_wa(self):
        """Abre WhatsApp Web de forma integrada y silenciosa, evitando duplicados."""
        try:
            # Verificar si ya hay un proceso en ejecución
            if self.wa_process and self.wa_process.poll() is None:
                # El proceso sigue vivo, no lanzar otro
                logging.info("WhatsApp ya está abierto o conectando...")
                return

            script_path = os.path.join(os.getcwd(), 'whatsapp_launcher.py')
            if os.path.exists(script_path):
                import subprocess
                # CREATE_NO_WINDOW (0x08000000) evita el CMD negro
                # DETACHED_PROCESS (0x00000008) asegura independencia total
                self.wa_process = subprocess.Popen([sys.executable, script_path], 
                               creationflags=0x08000008, 
                               close_fds=True)
            else:
                webbrowser.open("https://web.whatsapp.com/")
        except Exception as e:
            logging.error(f"Error al lanzar WhatsApp silencioso: {e}")
            webbrowser.open("https://web.whatsapp.com/")

class AdminFrame(tk.Canvas):
    """
    Panel de Administración con sistema de tarjetas similar al principal.
    Permite gestionar el inventario, usuarios y seguridad.
    """
    def __init__(self, parent, db: DatabaseManager, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(parent, bg=BG, highlightthickness=0, *args, **kwargs)
        self.db = db
        self.user = user
        
        # Logo de Fondo en Admin
        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path)
            self.last_bg_w, self.last_bg_h = 0, 0
            def draw_adm_bg(e):
                cw, ch = e.width, e.height
                if cw < 10 or ch < 10: return
                if cw == self.last_bg_w and ch == self.last_bg_h: return
                self.last_bg_w, self.last_bg_h = cw, ch
                self.delete("bg")
                img_res = self.bg_raw.resize((cw, ch), Image.LANCZOS)
                self.bg_photo = ImageTk.PhotoImage(img_res)
                self.create_image(cw//2, ch//2, image=self.bg_photo, tags="bg")
                self.tag_lower("bg")
            self.bind("<Configure>", draw_adm_bg)

        # --- Contenedores para Secciones ---
        self.header = ttk.Frame(self, bootstyle="success", padding=15)
        self.header_win = self.create_window(0, 0, window=self.header, anchor='nw', tags="header")
        
        self.body = ttk.Frame(self, padding=10)
        self.body_win = self.create_window(0, 70, window=self.body, anchor='nw', tags="body")
        
        def resize_adm_content(e):
            self.itemconfig("header", width=e.width)
            self.itemconfig("body", width=e.width, height=e.height - 70)
            if hasattr(self, 'bg_raw'): draw_adm_bg(e)
            
        self.bind("<Configure>", resize_adm_content)

        # --- Contenido de la Cabecera ---
        img = load_image(os.path.join('Imagenes', 'admin.jpeg'), size=(60,60))
        if img:
            lbl = ttk.Label(self.header, image=img, bootstyle="inverse-success")
            lbl.image = img
            lbl.pack(side='left', padx=10)

        self.title_lbl = ttk.Label(self.header, text='📊 PANEL DE ADMINISTRACIÓN', font=(None, 24, 'bold'), bootstyle="inverse-success")
        self.title_lbl.pack(side='left', padx=10)
        
        # Insertar botones de mantenimiento al lado del título
        self.setup_admin_tools(self.header)
        
        self.btn_back_main = ttk.Button(self.header, text='Regresar', command=lambda: self.master.select(0), bootstyle="light-outline", cursor="hand2", padding=10)
        self.btn_back_main.pack(side='right', padx=5)
        self.btn_back_main.bind('<Return>', lambda e: self.master.select(0))
        
        self.btn_back_admin = ttk.Button(self.header, text='Volver al Admin', command=self.show_admin_menu, bootstyle="light-outline", cursor="hand2", padding=10)
        self.btn_back_admin.bind('<Return>', lambda e: self.show_admin_menu())

        # --- Contenedor Principal con Notebook Oculto en el cuerpo ---
        self.notebook = ttk.Notebook(self.body, style='Hidden.TNotebook')
        self.notebook.pack(fill='both', expand=True)

        # 1. Pestaña del Menú de Tarjetas (Cuadritos)
        self.menu_frame = ttk.Frame(self.notebook, padding=30)
        self.notebook.add(self.menu_frame, text='Menú Admin')
        self.setup_admin_menu()

        # 2. Pestaña de Inventario
        self.inv_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.inv_frame, text='Inventario')
        self.setup_inventory()

        # 3. Pestaña de Usuarios
        self.users_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.users_frame, text='Usuarios')
        self.setup_users()

        # 4. Pestaña de Seguridad
        self.security_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.security_frame, text='Seguridad')
        self.setup_security()

        # 5. Pestaña de Menú / Productos
        self.products_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.products_frame, text='Menú / Productos')
        self.setup_menu()

        # 6. Pestaña de Historial de Cierres
        self.cierre_history_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.cierre_history_frame, text='Historial de Cierres')
        self.setup_cierre_history()

        # 7. Pestaña de Historial de Facturas (Reimpresión)
        self.ventas_history_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.ventas_history_frame, text='Historial de Facturas')
        self.setup_ventas_history()

        # 8. Pestaña de Publicidad
        self.publicidad_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.publicidad_frame, text='Publicidad')
        self.setup_publicidad()

        self.show_admin_menu() # Mostrar el menú de cuadritos al inicio

    def setup_publicidad(self):
        """Crea la interfaz para gestionar las imágenes de publicidad."""
        # Top Header
        header = ttk.Frame(self.publicidad_frame)
        header.pack(fill='x', pady=(0, 20))
        ttk.Label(header, text='Gestión de Pantalla TV (Publicidad)', font=(None, FONT_SIZE_XL, 'bold')).pack(side='left')

        # Main Layout
        content = ttk.Frame(self.publicidad_frame)
        content.pack(fill='both', expand=True)

        # Left side: List of images
        left_panel = tk.LabelFrame(content, text="Imágenes Actuales", bg=BG, fg="white", font=(None, 11, "bold"))
        left_panel.pack(side='left', fill='both', expand=True, padx=(0, 10), ipadx=15, ipady=15)

        # Treeview para imágenes
        columns = ('nombre', 'tamano', 'fecha')
        self.ads_tree = ttk.Treeview(left_panel, columns=columns, show='headings', bootstyle="info")
        self.ads_tree.heading('nombre', text='Nombre de Archivo')
        self.ads_tree.heading('tamano', text='Tamaño (KB)')
        self.ads_tree.heading('fecha', text='Fecha Modificación')
        self.ads_tree.column('nombre', width=200)
        self.ads_tree.column('tamano', width=100)
        self.ads_tree.column('fecha', width=150)
        
        vsb = ttk.Scrollbar(left_panel, orient="vertical", command=self.ads_tree.yview)
        self.ads_tree.configure(yscrollcommand=vsb.set)
        self.ads_tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='left', fill='y')

        # Right side: Actions & Preview
        right_panel = ttk.Frame(content, width=300)
        right_panel.pack(side='right', fill='y')
        right_panel.pack_propagate(False)

        actions_frame = tk.LabelFrame(right_panel, text="Acciones", bg=BG, fg="white", font=(None, 11, "bold"))
        actions_frame.pack(fill='x', pady=(0, 20), ipadx=15, ipady=15)

        ttk.Button(actions_frame, text='Cargar Nueva Imagen', bootstyle="success",
                   command=self.upload_ad_image, width=20).pack(pady=5, fill='x')
        ttk.Button(actions_frame, text='Eliminar Seleccionada', bootstyle="danger",
                   command=self.delete_ad_image, width=20).pack(pady=5, fill='x')
        ttk.Button(actions_frame, text='Refrescar Lista', bootstyle="secondary",
                   command=self.refresh_ads_list, width=20).pack(pady=5, fill='x')

        self.ad_preview_lbl = ttk.Label(right_panel, text="[Vista Previa]", anchor='center')
        self.ad_preview_lbl.pack(fill='both', expand=True)

        self.ads_tree.bind('<<TreeviewSelect>>', self.on_ad_select)
        
        # Cargar lista inicial
        self.refresh_ads_list()

    def refresh_ads_list(self):
        """Lee la carpeta de publicidad (o la nube si USE_CLOUD es True) y actualiza el Treeview."""
        for row in self.ads_tree.get_children():
            self.ads_tree.delete(row)
        
        self.ad_preview_lbl.config(image='', text='[Vista Previa]')
        self.ad_preview_lbl.image = None

        if USE_CLOUD:
            def fetch_cloud_ads():
                try:
                    response = requests.get(f"{CLOUD_URL}/api/publicidad", timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('status') == 'success':
                            images = data.get('images', [])
                            def populate():
                                for f in images:
                                    # Para la nube mostramos un indicador de tamaño y fecha
                                    self.ads_tree.insert('', 'end', values=(f, "Nube", "Remoto"))
                            self.after(0, populate)
                        else:
                            print(f"Error de la nube: {data.get('message')}")
                    else:
                        print(f"Error HTTP: {response.status_code}")
                except Exception as e:
                    print(f"Excepción obteniendo publicidad remota: {e}")
            
            threading.Thread(target=fetch_cloud_ads, daemon=True).start()
        else:
            ads_dir = os.path.abspath(os.path.join('Imagenes', 'publicidad'))
            if not os.path.exists(ads_dir):
                try:
                    os.makedirs(ads_dir)
                except Exception:
                    return

            for f in os.listdir(ads_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif', '.mp4', '.avi', '.mov')):
                    f_path = os.path.join(ads_dir, f)
                    size_kb = round(os.path.getsize(f_path) / 1024, 1)
                    mtime = datetime.fromtimestamp(os.path.getmtime(f_path)).strftime('%Y-%m-%d %H:%M')
                    self.ads_tree.insert('', 'end', values=(f, size_kb, mtime))

    @require_permission(Permissions.PRODUCT_MANAGE)
    def upload_ad_image(self):
        """Abre un diálogo para seleccionar y copiar una imagen o video."""
        from tkinter import filedialog
        file_path = filedialog.askopenfilename(
            title="Seleccionar Archivo (Imagen o Video)",
            filetypes=(("Archivos Multimedia", "*.png;*.jpg;*.jpeg;*.webp;*.avif;*.mp4;*.avi;*.mov"), ("Todos", "*.*"))
        )
        if file_path:
            filename = os.path.basename(file_path)
            
            # Copiar localmente primero para mantener el proyecto local actualizado
            ads_dir = os.path.abspath(os.path.join('Imagenes', 'publicidad'))
            if not os.path.exists(ads_dir): os.makedirs(ads_dir)
            dest_path = os.path.join(ads_dir, filename)
            try:
                shutil.copy2(file_path, dest_path)
            except Exception as e:
                print(f"Aviso: No se pudo copiar localmente: {e}")

            if USE_CLOUD:
                # Subir a la nube en segundo plano para no congelar la app
                def upload_thread():
                    try:
                        with open(file_path, 'rb') as f:
                            files = {'file': (filename, f)}
                            response = requests.post(f"{CLOUD_URL}/api/publicidad/upload", files=files, timeout=30)
                        
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('status') == 'success':
                                self.after(0, lambda: messagebox.showinfo("Éxito", f"Archivo '{filename}' subido correctamente a la nube y guardado localmente."))
                            else:
                                self.after(0, lambda: messagebox.showerror("Error", f"Error de la nube: {data.get('message')}"))
                        else:
                            self.after(0, lambda: messagebox.showerror("Error", f"Error del servidor HTTP: {response.status_code}"))
                        self.after(0, self.refresh_ads_list)
                    except Exception as e:
                        self.after(0, lambda: messagebox.showerror("Error", f"Error al subir a la nube:\n{e}"))
                
                threading.Thread(target=upload_thread, daemon=True).start()
            else:
                messagebox.showinfo("Éxito", f"Archivo '{filename}' cargado localmente con éxito.")
                self.refresh_ads_list()

    @require_permission(Permissions.PRODUCT_MANAGE)
    def delete_ad_image(self):
        """Elimina el archivo de publicidad seleccionado."""
        selected = self.ads_tree.selection()
        if not selected:
            messagebox.showwarning("Advertencia", "Seleccione un archivo para eliminar.")
            return
            
        filename = self.ads_tree.item(selected[0])['values'][0]
        
        if messagebox.askyesno("Confirmar", f"¿Seguro que desea eliminar '{filename}'?"):
            # Eliminar localmente primero
            ads_dir = os.path.abspath(os.path.join('Imagenes', 'publicidad'))
            file_path = os.path.join(ads_dir, filename)
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception as e: print(f"Aviso: No se pudo borrar localmente: {e}")
                
            if USE_CLOUD:
                # Eliminar de la nube en segundo plano
                def delete_thread():
                    try:
                        import urllib.parse
                        response = requests.delete(f"{CLOUD_URL}/api/publicidad/delete/{urllib.parse.quote(filename)}", timeout=10)
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('status') == 'success':
                                self.after(0, lambda: messagebox.showinfo("Éxito", f"Archivo '{filename}' eliminado correctamente de la nube y localmente."))
                            else:
                                self.after(0, lambda: messagebox.showerror("Error", f"Error de la nube: {data.get('message')}"))
                        else:
                            self.after(0, lambda: messagebox.showerror("Error", f"Error del servidor HTTP: {response.status_code}"))
                        self.after(0, self.refresh_ads_list)
                    except Exception as e:
                        self.after(0, lambda: messagebox.showerror("Error", f"Error al eliminar de la nube:\n{e}"))
                
                threading.Thread(target=delete_thread, daemon=True).start()
            else:
                messagebox.showinfo("Éxito", f"Archivo '{filename}' eliminado localmente.")
                self.refresh_ads_list()

    def on_ad_select(self, event):
        """Muestra una vista previa del archivo seleccionado (descarga si es necesario)."""
        selected = self.ads_tree.selection()
        if selected:
            filename = self.ads_tree.item(selected[0])['values'][0]
            ads_dir = os.path.abspath(os.path.join('Imagenes', 'publicidad'))
            file_path = os.path.join(ads_dir, filename)
            
            if filename.lower().endswith(('.mp4', '.avi', '.mov')):
                self.ad_preview_lbl.config(image='', text='[Video: Sin Vista Previa]')
                self.ad_preview_lbl.image = None
            else:
                if not os.path.exists(file_path) and USE_CLOUD:
                    # Descargar vista previa de la nube en segundo plano
                    def download_preview():
                        try:
                            import urllib.parse
                            url = f"{CLOUD_URL}/api/publicidad/{urllib.parse.quote(filename)}"
                            response = requests.get(url, timeout=5)
                            if response.status_code == 200:
                                if not os.path.exists(ads_dir): os.makedirs(ads_dir)
                                with open(file_path, 'wb') as f:
                                    f.write(response.content)
                                
                                def show():
                                    img = load_image(file_path, size=(250, 250))
                                    if img:
                                        self.ad_preview_lbl.config(image=img, text='')
                                        self.ad_preview_lbl.image = img
                                self.after(0, show)
                        except Exception as e:
                            print(f"Error descargando vista previa: {e}")
                    
                    self.ad_preview_lbl.config(image='', text='[Cargando Vista Previa...]')
                    threading.Thread(target=download_preview, daemon=True).start()
                else:
                    img = load_image(file_path, size=(250, 250))
                    if img:
                        self.ad_preview_lbl.config(image=img, text='')
                        self.ad_preview_lbl.image = img
                    else:
                        self.ad_preview_lbl.config(image='', text='[Error de Imagen]')

    def setup_admin_menu(self):
        """Crea el dashboard interno de administración con cuadritos e imágenes."""

        cards_wrap = ttk.Frame(self.menu_frame)
        cards_wrap.pack(fill='both', expand=True)

        def make_admin_card(parent, img_name, title, desc, cmd, color="success"):
            # Reutilizamos el estilo de 'pop-out' del dashboard principal
            card = ttk.Frame(parent, bootstyle="secondary", padding=2, cursor="hand2", takefocus=True, width=200, height=170)
            card.pack_propagate(False)
            
            inner = ttk.Frame(card, padding=8) 
            inner.pack(fill='both', expand=True)

            # Carga de imagen o emoji por defecto
            img = None
            if img_name:
                path = os.path.join('Imagenes', img_name)
                img = load_image(path, size=(50, 50))
            
            if img:
                lbl = ttk.Label(inner, image=img)
                lbl.image = img
                lbl.pack(pady=(5, 5))
            else:
                # Si no hay imagen, usar un emoji genérico según el título
                emoji = '📦'
                if 'Usuarios' in title: emoji = '👥'
                if 'Seguridad' in title: emoji = '🛡️'
                if 'Publicidad' in title: emoji = '📺'
                ttk.Label(inner, text=emoji, font=(None, 40)).pack(pady=(5, 5))

            ttk.Label(inner, text=title, font=(None, 14, 'bold'), wraplength=180, justify='center').pack(pady=(0, 2))
            ttk.Label(inner, text=desc, wraplength=170, justify='center', font=(None, 9)).pack(pady=2, fill='both', expand=True)

            def on_enter(e):
                card.configure(bootstyle=color, padding=5)
                inner.configure(bootstyle="light")
            def on_leave(e):
                card.configure(bootstyle="secondary", padding=2)
                inner.configure(bootstyle="default")

            def on_click(e): cmd()
            
            def bind_events(widget):
                widget.bind("<Button-1>", on_click)
                widget.bind("<Return>", on_click)
                for child in widget.winfo_children():
                    bind_events(child)
                    
            bind_events(card)
            
            # Hover events just for card and inner
            card.bind("<Enter>", on_enter); card.bind("<Leave>", on_leave)
            card.bind("<FocusIn>", lambda e: on_enter(None)); card.bind("<FocusOut>", lambda e: on_leave(None))
            
            return card

        # Tarjetas del Admin con sus imágenes correspondientes
        all_admin_cards = [
            ('inventario.jpg', 'Inventario', 'Control de stock y materia prima.', lambda: self.open_section(1, "GESTIÓN DE INVENTARIO"), ['Administrador', 'Supervisor']),
            ('user.png', 'Usuarios', 'Gestión de personal y accesos.', lambda: self.open_section(2, "GESTIÓN DE USUARIOS"), ['Administrador']),
            ('seguridad.png', 'Seguridad', 'Auditoría y respaldos de DB.', lambda: self.open_section(3, "SEGURIDAD Y AUDITORÍA"), ['Administrador']),
            ('pos.png', 'Menú / Productos', 'Gestión de productos y precios.', lambda: self.open_section(4, "GESTIÓN DE MENÚ"), ['Administrador', 'Supervisor']),
            ('efectivo.jpeg', 'Cierres de Caja', 'Historial de reportes de cierre.', lambda: self.open_section(5, "HISTORIAL DE CIERRES"), ['Administrador', 'Supervisor']),
            ('pos.png', 'Historial Ventas', 'Búsqueda y reimpresión de facturas.', lambda: self.open_section(6, "HISTORIAL DE FACTURAS"), ['Administrador', 'Supervisor']),
            ('', 'Publicidad TV', 'Cargar videos e imágenes para la TV.', lambda: self.open_section(7, "GESTIÓN DE PUBLICIDAD"), ['Administrador', 'Supervisor']),
        ]

        user_role = self.user.get('rol', '') if self.user else ''
        filtered_cards = [c for c in all_admin_cards if user_role in c[4]]

        admin_cards = []
        for i, data in enumerate(filtered_cards):
            card = make_admin_card(cards_wrap, data[0], data[1], data[2], data[3])
            row = i // 4
            col = i % 4
            card.grid(row=row, column=col, padx=10, pady=10)
            admin_cards.append(card)


        # Navegación por flechas para las tarjetas de Admin (Fila de 4)
        def nav_admin(idx, e):
            if e.keysym == 'Left' and idx > 0: admin_cards[idx-1].focus_set()
            elif e.keysym == 'Right' and idx < len(admin_cards)-1: admin_cards[idx+1].focus_set()

        for i, card in enumerate(admin_cards):
            card.bind("<Left>", lambda e, idx=i: nav_admin(idx, e))
            card.bind("<Right>", lambda e, idx=i: nav_admin(idx, e))

        num_cols = min(len(filtered_cards), 4)
        if num_cols == 0: num_cols = 1
        for i in range(num_cols): cards_wrap.columnconfigure(i, weight=1)



    def open_section(self, index, title):
        """Abre una sección específica y actualiza la cabecera."""
        self.notebook.select(index)
        self.title_lbl.config(text=f"📊 {title}")
        self.btn_back_main.pack_forget() # Ocultar botón principal
        self.btn_back_admin.pack(side='right', padx=5) # Mostrar botón volver al admin
        self.refresh()

    def show_admin_menu(self):
        """Vuelve al menú de cuadritos del admin."""
        self.notebook.select(0)
        self.title_lbl.config(text='📊 PANEL DE ADMINISTRACIÓN')
        self.btn_back_admin.pack_forget()
        self.btn_back_main.pack(side='right', padx=5)

    def _check_section_permission(self, idx):
        """Verifica si el usuario tiene permiso para ver la sección."""
        # El índice 0 es el Menú principal de Admin (las tarjetas), que siempre debe ser accesible
        # si el usuario ya logró entrar al AdminFrame
        if idx == 0:
            return True
            
        user_role = self.user.get('rol', '')
        
        permissions = {
            'Administrador': [1, 2, 3, 4, 5, 6, 7],
            'Supervisor': [1, 4, 5, 6, 7],
            'Cajera': [],
            'Cocina': [],
            'Mesero': [],
            'Publicidad': []
        }
        
        if idx not in permissions.get(user_role, []):
            messagebox.showerror("Acceso Denegado", "No tiene permiso para acceder a esta sección")
            self.show_admin_menu()
            return False
        return True

    def refresh(self):
        """Refresca la sección activa con verificación de permisos."""
        try:
            idx = self.notebook.index('current')
            
            # Verificar permisos antes de refrescar
            if not self._check_section_permission(idx):
                return
            
            if idx == 1: 
                self.refresh_inventory()
            elif idx == 2: 
                self.refresh_users()
            elif idx == 3: 
                self.refresh_security()
            elif idx == 4: 
                self.refresh_menu()
            elif idx == 5: 
                self.refresh_cierres()
            elif idx == 6:
                self.refresh_ventas_history()
            elif idx == 7:
                self.refresh_ads_list()
        except Exception as e:
            logging.error(f"Error en refresh: {e}")

    def setup_inventory(self):
        """Prepara la estructura visual de la sección de inventario con una tabla moderna."""
        # Contenedor superior para controles
        controls = ttk.Frame(self.inv_frame, padding=(0, 0, 0, 20))
        controls.pack(fill='x')
        
        ttk.Label(controls, text="Control de Materia Prima e Ingredientes", font=(None, 16, 'bold')).pack(side='left')
        ttk.Button(controls, text='Actualizar Lista', command=self.refresh_inventory, bootstyle="success", padding=10).pack(side='right')

        # Tabla de Inventario (Treeview)
        cols = ('ID', 'Ingrediente', 'Stock Actual', 'Unidad', 'Mínimo')
        self.inv_tree = ttk.Treeview(self.inv_frame, columns=cols, show='headings', bootstyle="success", height=15)
        
        # Configurar cabeceras y anchos de columna
        for c in cols:
            self.inv_tree.heading(c, text=c)
            self.inv_tree.column(c, anchor='center', width=150)
        
        self.inv_tree.column('Ingrediente', anchor='w', width=300)
        self.inv_tree.pack(fill='both', expand=True)

        # Panel de acciones rápidas (Ajuste de stock)
        actions = tk.LabelFrame(self.inv_frame, text="Acciones de Ajuste Rápido", bg=BG, fg="white", font=(None, 11, "bold"))
        actions.pack(fill='x', pady=(20, 0), padx=10)
        
        ttk.Label(actions, text="Seleccione un ingrediente de la tabla y use los botones para ajustar:", font=(None, 11)).pack(side='left', padx=10)
        
        btn_add = ttk.Button(actions, text="Añadir +1", command=lambda: self.adjust_selected_stock(1), bootstyle="success-outline", padding=10, width=15)
        btn_add.pack(side='left', padx=5)
        
        btn_rem = ttk.Button(actions, text="Quitar -1", command=lambda: self.adjust_selected_stock(-1), bootstyle="warning-outline", padding=10, width=15)
        btn_rem.pack(side='left', padx=5)

    def adjust_selected_stock(self, amount):
        """Ajusta el stock del elemento seleccionado en la tabla."""
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Por favor, seleccione un ingrediente de la tabla.")
            return
        
        item_id = self.inv_tree.item(sel[0])['values'][0]
        self.add_stock(item_id, amount)

    def setup_users(self):
        """Prepara la estructura visual de la sección de usuarios con tabla profesional."""
        ttk.Label(self.users_frame, text="Gestión de Personal y Accesos", font=(None, 16, 'bold')).pack(anchor='w', pady=(0, 10))
        
        cols = ('id', 'username', 'rol', 'nombre')
        # Tabla para mostrar usuarios existentes
        self.user_tree = ttk.Treeview(self.users_frame, columns=cols, show='headings', bootstyle="success", height=10)
        for c in cols:
            self.user_tree.heading(c, text=c.capitalize())
            self.user_tree.column(c, anchor='center', width=100)
        
        self.user_tree.column('nombre', anchor='w', width=250)
        self.user_tree.pack(fill='both', expand=True, pady=10)
        
        # Formulario para agregar nuevos usuarios
        form = tk.LabelFrame(self.users_frame, text='Registrar Nuevo Usuario', bg=BG, fg="white", font=(None, 11, "bold"))
        form.pack(fill='x', pady=10, padx=10)
        
        inputs = ttk.Frame(form)
        inputs.pack(fill='x')
        
        ttk.Label(inputs, text='Usuario:').grid(row=0, column=0, padx=5, pady=5)
        self.e_user = ttk.Entry(inputs)
        self.e_user.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Clave:').grid(row=0, column=2, padx=5, pady=5)
        self.e_pass = ttk.Entry(inputs, show='*')
        self.e_pass.grid(row=0, column=3, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Rol:').grid(row=1, column=0, padx=5, pady=5)
        self.e_rol = ttk.Combobox(inputs, values=['Administrador', 'Supervisor', 'Cajera', 'Cocina', 'Mesero', 'Publicidad'])
        self.e_rol.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Nombre:').grid(row=1, column=2, padx=5, pady=5)
        self.e_nombre = ttk.Entry(inputs)
        self.e_nombre.grid(row=1, column=3, padx=5, pady=5, sticky='ew')
        
        inputs.columnconfigure((1, 3), weight=1)
        
        ttk.Button(form, text='CREAR USUARIO', command=self.create_user, bootstyle="success").pack(pady=10)

    def setup_security(self):
        """Configura el panel de seguridad y métricas con un diseño limpio."""
        # --- Métricas de Seguridad ---
        metrics_frame = tk.LabelFrame(self.security_frame, text="Estado de Seguridad del Sistema", bg=BG, fg="white", font=(None, 11, "bold"))
        metrics_frame.pack(fill='x', pady=(0, 20), padx=10)
        
        # Grid para métricas
        m_inner = ttk.Frame(metrics_frame)
        m_inner.pack(fill='x')
        
        self.lbl_failed = ttk.Label(m_inner, text="🚨 Intentos fallidos hoy: 0", font=(None, 14), bootstyle="danger")
        self.lbl_failed.grid(row=0, column=0, padx=30)
        
        self.lbl_sessions = ttk.Label(m_inner, text="👥 Sesiones activas: 0", font=(None, 14), bootstyle="info")
        self.lbl_sessions.grid(row=0, column=1, padx=30)
        
        btn_backup = ttk.Button(m_inner, text="💾 Generar Respaldo DB", command=self.manual_backup, bootstyle="success", padding=10)
        btn_backup.grid(row=0, column=2, padx=30)
        
        btn_test_print = ttk.Button(m_inner, text="🖨 Probar Impresora/Cajón", command=self.test_printer, bootstyle="info", padding=10)
        btn_test_print.grid(row=0, column=3, padx=30)

        # --- Tabla de Auditoría ---
        ttk.Label(self.security_frame, text="Historial de Auditoría (Últimas Actividades)", font=(None, 16, 'bold')).pack(anchor='w', pady=15)
        
        cols = ('Fecha', 'Usuario', 'Acción', 'Tabla', 'Detalles')
        self.audit_tree = ttk.Treeview(self.security_frame, columns=cols, show='headings', bootstyle="info", height=12)
        for c in cols:
            self.audit_tree.heading(c, text=c)
            self.audit_tree.column(c, anchor='center', width=120)
        
        self.audit_tree.column('Fecha', width=180)
        self.audit_tree.column('Detalles', anchor='w', width=400)
        self.audit_tree.pack(fill='both', expand=True)
        
        self.refresh_security()

    @require_permission(Permissions.SYSTEM_CONFIG)
    def manual_backup(self):
        """Ejecuta un backup manual desde la interfaz."""
        path = self.db.create_backup()
        if path:
            messagebox.showinfo("Backup Exitoso", f"Copia de seguridad creada en:\n{path}")
        else:
            messagebox.showerror("Error", "No se pudo crear la copia de seguridad")

    def test_printer(self):
        """Realiza una prueba de impresión y apertura de cajón."""
        printer_name = find_pos_printer()
        if not printer_name:
            messagebox.showerror("Error", "No se detectó ninguna impresora térmica POS instalada.")
            return
            
        confirm = messagebox.askyesno("Prueba", f"¿Desea probar la impresora:\n{printer_name}?\n\nSe enviará un ticket de prueba y se abrirá el cajón.")
        if not confirm: return
        
        test_text = """
    ====================================
    *       PRUEBA DE IMPRESION        *
    *         PIK'TA SOFT              *
    ====================================
    FECHA:       {}
    ESTADO:      CORRECTO
    ------------------------------------
    SISTEMA OPERATIVO: {}
    IMPRESORA DETECTADA:
    {}
    ------------------------------------
    ¡PRUEBA EXITOSA!
    ====================================
    
    
    
    """.format(datetime.now().strftime('%d/%m/%Y %H:%M:%S'), sys.platform, printer_name)
        
        # 1. Abrir Cajón
        try:
            hPrinter = win32print.OpenPrinter(printer_name)
            raw_data = b'\x1b\x70\x00\x19\xfa'
            win32print.StartDocPrinter(hPrinter, 1, ("Prueba Cajon", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, raw_data)
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
            win32print.ClosePrinter(hPrinter)
        except Exception as e:
            messagebox.showerror("Error Cajón", f"No se pudo abrir el cajón: {e}")
            
        # 2. Imprimir Ticket (PIL)
        from PIL import Image, ImageDraw, ImageFont
        img_width = 380
        lines = test_text.split('\n')
        img_height = 100 + (len(lines) * 25)
        img = Image.new('RGB', (img_width, img_height), 'white')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arialbd.ttf", 14)
        except Exception:
            font = ImageFont.load_default()
            
        y = 20
        for line in lines:
            draw.text((10, y), line, fill='black', font=font)
            y += 25
            
        filename = "test_print.png"
        img.save(filename)
        try:
            win32api.ShellExecute(0, "printto", filename, f'"{printer_name}"', ".", 0)
            messagebox.showinfo("Éxito", "Prueba de impresión enviada y cajón abierto.")
        except Exception as e:
            import os
            os.startfile(filename, "print")
            messagebox.showinfo("Prueba", f"Enviado a imprimir por defecto (ShellExecute falló: {e})")

    def setup_cierre_history(self):
        """Configura la pestaña para ver el historial de cierres de caja."""
        for w in self.cierre_history_frame.winfo_children():
            w.destroy()

        # Logo Pik'ta en la cabecera
        logo_path = os.path.join('Imagenes', 'pikta2.png')
        if os.path.exists(logo_path):
            img = load_image(logo_path, size=(80, 80))
            if img:
                lbl = ttk.Label(self.cierre_history_frame, image=img)
                lbl.image = img
                lbl.pack(pady=5)

        ttk.Label(self.cierre_history_frame, text="📊 HISTORIAL DE CIERRES DE CAJA", font=(None, 18, 'bold')).pack(pady=10)

        # Botones de Acción con estilo profesional (Empacados al fondo PRIMERO para que nunca se oculten)
        btn_f = ttk.Frame(self.cierre_history_frame)
        btn_f.pack(side='bottom', fill='x', pady=20, padx=10)
        
        # Frame interior para centrar los botones
        btn_inner = ttk.Frame(btn_f)
        btn_inner.pack(anchor='center')

        main_c = ttk.Frame(self.cierre_history_frame)
        main_c.pack(side='top', fill='both', expand=True)

        # Lista de cierres (Lado Izquierdo)
        left = ttk.Frame(main_c, width=300)
        left.pack(side='left', fill='y', padx=10)

        ttk.Label(left, text="Seleccione un Cierre:", font=(None, 11, 'bold')).pack(pady=5)

        self.cierre_list = ttk.Treeview(left, columns=('ID', 'Fecha', 'Total'), show='headings', height=15)
        self.cierre_list.heading('ID', text='ID')
        self.cierre_list.heading('Fecha', text='Fecha')
        self.cierre_list.heading('Total', text='Total')
        self.cierre_list.column('ID', width=50)
        self.cierre_list.column('Fecha', width=150)
        self.cierre_list.column('Total', width=80)
        self.cierre_list.pack(fill='both', expand=True)

        # Vista del Reporte (Lado Derecho)
        right = ttk.Frame(main_c)
        right.pack(side='right', fill='both', expand=True, padx=10)

        ttk.Label(right, text="Vista del Reporte:", font=(None, 11, 'bold')).pack(pady=5)
        self.cierre_view = tk.Text(right, font=("Courier", 11), bg="#f0f0f0", state='disabled')
        self.cierre_view.pack(fill='both', expand=True)

        def on_cierre_select(e):
            sel = self.cierre_list.selection()
            if not sel: return
            cid = self.cierre_list.item(sel[0])['values'][0]

            res = self.db.fetch_one("SELECT reporte_texto FROM caja_sesiones WHERE id = ?", (cid,))
            if res:
                self.cierre_view.config(state='normal')
                self.cierre_view.delete('1.0', 'end')
                self.cierre_view.insert('1.0', res[0] or "Sin texto de reporte")
                self.cierre_view.config(state='disabled')

        self.cierre_list.bind('<<TreeviewSelect>>', on_cierre_select)

        def print_historical():
            txt = self.cierre_view.get('1.0', 'end-1c')
            if not txt.strip():
                messagebox.showwarning("Aviso", "Seleccione un cierre de la lista primero.")
                return
            
            import os
            temp_dir = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'PiktaInvoices')
            if not os.path.exists(temp_dir): os.makedirs(temp_dir)
            
            base_name = f"cierre_historial_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            filename = os.path.join(temp_dir, base_name)
            with open(filename, "w", encoding="utf-8") as f:
                f.write(txt)
            
            try:
                import sys
                if sys.platform == "win32":
                    cfg = PrinterConfig()
                    printer_name = cfg.resolve_printer("cierre")
                    if printer_name and WIN32_PRINT_AVAILABLE:
                        win32api.ShellExecute(0, "printto", filename, f'"{printer_name}"', ".", 0)
                    else:
                        os.startfile(filename, "print")
                else:
                    messagebox.showinfo("Info", "Impresión nativa solo disponible en Windows.")
                messagebox.showinfo("Impresión", "Reporte de cierre enviado a la impresora exitosamente.")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo imprimir el reporte: {e}")

        ttk.Button(btn_inner, text="🔄 ACTUALIZAR LISTA", command=self.refresh_cierres, bootstyle="info-outline", padding=(20, 10), cursor="hand2").pack(side='left', padx=15)
        ttk.Button(btn_inner, text="🖨 REIMPRIMIR CIERRE SELECCIONADO", command=print_historical, bootstyle="primary", padding=(20, 10), cursor="hand2").pack(side='left', padx=15)

        self.refresh_cierres()

    def refresh_cierres(self):
        """Actualiza la lista de cierres de caja."""
        if hasattr(self, 'cierre_list'):
            self.cierre_list.delete(*self.cierre_list.get_children())
            rows = self.db.fetch_all("SELECT id, cierre_at, cierre_total FROM caja_sesiones WHERE estado='CERRADO' ORDER BY id DESC")
            for r in rows:
                try:
                    raw_date = r[1]
                    if raw_date and isinstance(raw_date, str):
                        # Intentar convertir si es un string ISO
                        fecha = datetime.fromisoformat(raw_date).strftime('%d/%m/%Y %H:%M')
                    else:
                        fecha = str(raw_date) if raw_date else "N/A"
                except Exception:
                    fecha = str(r[1]) if r[1] else "Error Fecha"
                    
                self.cierre_list.insert('', 'end', values=(r[0], fecha, f"${r[2]:.2f}"))

    def setup_ventas_history(self):
        """Prepara la interfaz para buscar y reimprimir facturas individuales."""
        ttk.Label(self.ventas_history_frame, text="📄 HISTORIAL DE FACTURACIÓN Y VENTAS", font=(None, 18, 'bold')).pack(pady=10)
        
        # Filtros
        filters = ttk.Frame(self.ventas_history_frame, padding=10)
        filters.pack(fill='x')
        
        ttk.Label(filters, text="Fecha (YYYY-MM-DD):").pack(side='left', padx=5)
        self.ent_ventas_fecha = ttk.Entry(filters, width=15)
        self.ent_ventas_fecha.insert(0, datetime.now().strftime('%Y-%m-%d'))
        self.ent_ventas_fecha.pack(side='left', padx=5)
        
        ttk.Label(filters, text="Factura/Mesa:").pack(side='left', padx=5)
        self.ent_ventas_search = ttk.Entry(filters, width=20)
        self.ent_ventas_search.pack(side='left', padx=5)
        
        ttk.Button(filters, text="🔍 BUSCAR", command=self.refresh_ventas_history, bootstyle="info").pack(side='left', padx=10)
        
        # Tabla de facturas
        main_v = ttk.Frame(self.ventas_history_frame)
        main_v.pack(fill='both', expand=True, pady=10)
        
        cols = ('ID', 'Número', 'Fecha', 'Mesa', 'Total', 'Método')
        self.ventas_tree = ttk.Treeview(main_v, columns=cols, show='headings', height=12)
        for c in cols:
            self.ventas_tree.heading(c, text=c)
            self.ventas_tree.column(c, anchor='center', width=100)
        self.ventas_tree.column('Fecha', width=150)
        self.ventas_tree.pack(side='left', fill='both', expand=True)
        
        vsb = ttk.Scrollbar(main_v, orient="vertical", command=self.ventas_tree.yview)
        self.ventas_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        
        # Botones inferiores
        btns = ttk.Frame(self.ventas_history_frame, padding=10)
        btns.pack(fill='x')
        ttk.Button(btns, text="🖨 REIMPRIMIR FACTURA SELECCIONADA", command=self.reprint_selected_invoice, 
                   bootstyle="success", padding=12).pack(anchor='center')

    def refresh_ventas_history(self):
        """Consulta la BD con los filtros aplicados."""
        for r in self.ventas_tree.get_children(): self.ventas_tree.delete(r)
        
        fecha = self.ent_ventas_fecha.get().strip()
        search = self.ent_ventas_search.get().strip()
        
        query = "SELECT id, numero, created_at, mesa, total, metodo_pago, items FROM pedidos WHERE pagado = 1"
        params = []
        
        if fecha:
            query += " AND created_at LIKE ?"
            params.append(f"{fecha}%")
        if search:
            query += " AND (numero LIKE ? OR mesa LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
            
        query += " ORDER BY created_at DESC LIMIT 200"
        
        rows = self.db.fetch_all(query, params)
        for r in rows:
            try:
                # r[2] es la fecha
                fec = str(r[2]).replace('T', ' ')[:16] if r[2] else 'N/A'
                # r[4] es el total
                try:
                    total_val = float(r[4]) if r[4] is not None else 0.0
                except (ValueError, TypeError):
                    total_val = 0.0
                
                # r[6] son los items (tags)
                items_tag = str(r[6]) if r[6] else ""
                
                self.ventas_tree.insert('', 'end', 
                                       values=(r[0], r[1], fec, r[3], f"${total_val:.2f}", r[5]), 
                                       tags=(items_tag,))
            except Exception as e:
                logging.error(f"Error procesando fila de venta: {e}")
                continue

    def reprint_selected_invoice(self):
        """Obtiene los datos de la factura seleccionada y la envía a la impresora."""
        sel = self.ventas_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Seleccione una factura de la lista.")
            return
            
        item = self.ventas_tree.item(sel[0])
        vals = item['values']
        # Recuperar items del tag (guardados como JSON)
        try:
            items_raw = item['tags'][0]
            pedido_data = {
                'numero': vals[1],
                'created_at': vals[2],
                'mesa': vals[3],
                'total': float(vals[4].replace('$', '')),
                'metodo_pago': vals[5],
                'items': json.loads(items_raw)
            }
            
            self._print_windows_invoice(pedido_data)
            messagebox.showinfo("Éxito", f"Factura {pedido_data['numero']} enviada a la impresora.")
        except Exception as e:
            logging.error(f"Error al reimprimir factura: {e}")
            messagebox.showerror("Error", f"No se pudo procesar la reimpresión: {e}")

    def _get_escpos_image(self, image_path, width=384):
        """Convierte una imagen a comandos ESC/POS (GS v 0)."""
        try:
            from PIL import Image
            img = Image.open(image_path).convert('L')
            w_percent = (width / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((width, h_size), Image.LANCZOS)
            img = img.point(lambda x: 0 if x < 128 else 255, '1')
            pixels = list(img.getdata())
            w, h = img.size
            bytes_per_row = (w + 7) // 8
            raster_data = bytearray()
            for y in range(h):
                for x in range(bytes_per_row):
                    byte = 0
                    for bit in range(8):
                        pixel_x = x * 8 + bit
                        if pixel_x < w:
                            if pixels[y * w + pixel_x] == 0: byte |= (1 << (7 - bit))
                    raster_data.append(byte)
            header = bytes([29, 118, 48, 0, bytes_per_row % 256, bytes_per_row // 256, h % 256, h // 256])
            return header + raster_data
        except Exception: return b""

    def _print_windows_invoice(self, data):
        """Impresión usando el diálogo estándar de Windows."""
        import datetime
        fecha = datetime.datetime.now().strftime('%d/%m/%Y %I:%M %p')
        num_fac = str(data.get('numero', '000000')).zfill(8)
        total = float(data.get('total', 0))
        
        # Construir el ticket para el archivo de texto
        ticket = f"\n"
        ticket += "      PIK'TA GRILL SOLUTIONS    \n"
        ticket += "      RUC: 8-765-4321 DV 01     \n"
        ticket += "--------------------------------\n"
        ticket += "  COMPROBANTE AUXILIAR DE       \n"
        ticket += "     FACTURA ELECTRÓNICA        \n"
        ticket += "--------------------------------\n"
        ticket += f" FACTURA: {num_fac}\n"
        ticket += f" FECHA:   {fecha}\n"
        ticket += "--------------------------------\n"
        ticket += f" CLIENTE: {data.get('cliente', 'CLIENTE GENERAL')}\n"
        ticket += "--------------------------------\n"
        
        for it in data.get('items', []):
            cant = it.get('cantidad', it.get('qty', 1))
            nom = str(it.get('nombre', '')).upper()
            pre = float(it.get('precio_unitario', it.get('precio', 0)))
            sub = pre * cant
            ticket += f"{nom[:32]}\n"
            ticket += f" {cant:.2f} X {pre:>8.2f}    {sub:>8.2f}\n"
            
        ticket += "--------------------------------\n"
        ticket += f" TOTAL                   B/.{total:>8.2f}\n"
        ticket += "--------------------------------\n"
        ticket += "¡GRACIAS POR SU PREFERENCIA!\n\n"
        
        try:
            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(ticket)

            cfg = PrinterConfig()
            printer_name = cfg.resolve_printer("factura")
            if printer_name:
                win32api.ShellExecute(0, "printto", path, f'"{printer_name}"', ".", 0)
            else:
                win32api.ShellExecute(0, "print", path, None, ".", 0)
            
            # Esperar un poco antes de borrar (opcional)
            # time.sleep(2)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el diálogo de impresión: {e}")

    def setup_admin_tools(self, parent):
        """Herramientas especiales para el administrador reubicadas en la cabecera."""
        tools_frame = ttk.Frame(parent, bootstyle="success")
        tools_frame.pack(side='left', fill='y', padx=20)
        
        # Botones con iconos compactos para cabecera
        btn_clear = ttk.Button(tools_frame, text="🧹 Reiniciar Cocina", 
                  command=self.clear_all_orders, bootstyle="danger", padding=8)
        btn_clear.pack(side='left', padx=5, pady=5)
        
        btn_reset = ttk.Button(tools_frame, text="📦 Reiniciar Inventario", 
                  command=self.reset_inventory, bootstyle="warning", padding=8)
        btn_reset.pack(side='left', padx=5, pady=5)
        
        btn_backup = ttk.Button(tools_frame, text="💾 Backup DB", 
                  command=self.manual_backup, bootstyle="light", padding=8)
        btn_backup.pack(side='left', padx=5, pady=5)
        
        btn_printers = ttk.Button(tools_frame, text="🖨 Config. Impresoras", 
                  command=self.open_printer_config, bootstyle="info", padding=8)
        btn_printers.pack(side='left', padx=5, pady=5)

    @require_permission(Permissions.SYSTEM_CONFIG)
    def clear_all_orders(self):
        """Elimina todos los pedidos de la base de datos para empezar de cero."""
        if messagebox.askyesno("Confirmar Limpieza", "¿Está seguro de eliminar TODOS los pedidos? Esta acción no se puede deshacer."):
            try:
                with self.db.get_connection() as conn:
                    conn.execute("DELETE FROM pedidos")
                messagebox.showinfo("Éxito", "Todos los pedidos han sido eliminados. La cocina está limpia.")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo limpiar los pedidos: {e}")

    @require_permission(Permissions.SYSTEM_CONFIG)
    def reset_inventory(self):
        """Reinicia los valores de inventario a cero."""
        if messagebox.askyesno("Confirmar Reinicio", "¿Desea poner todas las existencias de inventario en cero?"):
            try:
                with self.db.get_connection() as conn:
                    conn.execute("UPDATE inventario SET cantidad = 0")
                messagebox.showinfo("Éxito", "Inventario reiniciado correctamente.")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo reiniciar el inventario: {e}")

    def refresh_security(self):
        try:
            # Usar DATE() en lugar de LIKE para mayor seguridad
            today = datetime.now().strftime('%Y-%m-%d')
            failed = self.db.fetch_one(
                "SELECT COUNT(*) FROM access_logs WHERE action='failed_login' AND DATE(created_at) = DATE(?)",
                (today,)
            )
            self.lbl_failed.config(text=f"Intentos fallidos hoy: {failed[0] if failed else 0}")
            
            # Sesiones activas
            active = len(session_manager.sessions)
            self.lbl_sessions.config(text=f"Sesiones activas: {active}")
            
            # Logs de auditoría con paginación
            for r in self.audit_tree.get_children():
                self.audit_tree.delete(r)
            
            # Limitar a 100 registros y usar ORDER BY seguro
            logs = self.db.fetch_all("""
                SELECT fecha, usuario, accion, tabla, detalles 
                FROM auditoria 
                ORDER BY fecha DESC 
                LIMIT 100
            """)
            
            for log in logs:
                self.audit_tree.insert('', 'end', values=log)
                
        except Exception as e:
            logging.error(f"Error al refrescar seguridad: {e}")
            self.lbl_failed.config(text="Error al cargar métricas")

    def refresh_inventory(self):
        """Consulta y actualiza la tabla de inventario con formato limpio."""
        # Limpiar tabla actual
        for r in self.inv_tree.get_children():
            self.inv_tree.delete(r)
            
        rows = self.db.fetch_all('SELECT id, ingrediente, cantidad, unidad, stock_minimo FROM inventario')
        
        for r in rows:
            # Formatear el stock a 2 decimales para que no se vea como 2.1999999999
            stock_fmt = f"{r[2]:.2f}"
            
            # Insertar en la tabla
            item_id = self.inv_tree.insert('', 'end', values=(r[0], r[1], stock_fmt, r[3], r[4]))
            
            # Aplicar color si el stock está bajo el mínimo
            if r[2] <= r[4]:
                self.inv_tree.tag_configure('low_stock', foreground='#ef4444') # Rojo
                self.inv_tree.item(item_id, tags=('low_stock',))
            else:
                self.inv_tree.tag_configure('normal_stock', foreground='#10b981') # Verde
                self.inv_tree.item(item_id, tags=('normal_stock',))

    def refresh_users(self):
        """Actualiza la tabla de usuarios registrados."""
        for r in self.user_tree.get_children(): self.user_tree.delete(r)
        rows = self.db.fetch_all('SELECT id, username, rol, nombre_completo FROM usuarios')
        for row in rows: self.user_tree.insert('', 'end', values=row)

    @require_permission(Permissions.USER_MANAGE)
    def create_user(self):
        """Valida e inserta un nuevo usuario en la base de datos aplicando políticas."""
        u, p, r, n = self.e_user.get().strip(), self.e_pass.get().strip(), self.e_rol.get().strip(), self.e_nombre.get().strip()
        if not u or not p:
            messagebox.showwarning('Error', 'El usuario y la contraseña son obligatorios')
            return
            
        is_valid, msg = PasswordPolicy.validate(p)
        if not is_valid:
            messagebox.showwarning('Contraseña Débil', msg)
            return
            
        try:
            hashed_p = hash_password(p)
            self.db.execute('INSERT INTO usuarios (username, password, rol, nombre_completo) VALUES (?,?,?,?)', (u, hashed_p, r or 'Cajera', n or u))
            
            # Registrar en auditoría
            self.db.audit_admin_action('CREAR_USUARIO', 'Admin', f'Usuario creado: {u}', level='CRITICAL')
            
            messagebox.showinfo('Éxito', 'Usuario creado correctamente')
            # Limpiar campos después de crear
            for e in (self.e_user, self.e_pass, self.e_nombre): e.delete(0, 'end')
            self.refresh_users()
        except sqlite3.IntegrityError:
            messagebox.showerror('Error', f"El usuario '{u}' ya existe")
        except Exception as e:
            messagebox.showerror('Error', f"No se pudo crear el usuario: {e}")

    @require_permission(Permissions.INVENTORY_MANAGE)
    def add_stock(self, id, amount):
        """Incrementa o decrementa la cantidad de un ingrediente específico."""
        try:
            # Obtener datos previos para auditoría
            prev = self.db.fetch_one("SELECT ingrediente, cantidad FROM inventario WHERE id=?", (id,))
            
            self.db.execute('UPDATE inventario SET cantidad = cantidad + ? WHERE id = ?', (amount, id))
            
            # Registrar en auditoría
            self.db.audit_log('inventario', 'UPDATE', 'Admin', f'Stock ajustado: {prev[0]} ({amount})', prev={'cantidad': prev[1]}, new={'cantidad': prev[1]+amount})
            
            self.refresh_inventory()
        except Exception as e:
            messagebox.showerror('Error', 'No se pudo actualizar el stock')

    def setup_menu(self):
        """Prepara la interfaz para gestionar los productos del menú."""
        ttk.Label(self.products_frame, text="Gestión de Menú y Productos", font=(None, 16, 'bold')).pack(anchor='w', pady=(0, 10))
        
        # Tabla de productos
        cols = ('ID', 'Nombre', 'Categoría', 'Precio', 'Emoji', 'Disponible', 'Prep (min)')
        self.menu_tree = ttk.Treeview(self.products_frame, columns=cols, show='headings', bootstyle="info", height=10)
        for c in cols:
            self.menu_tree.heading(c, text=c)
            self.menu_tree.column(c, anchor='center', width=100)
        
        self.menu_tree.column('Nombre', anchor='w', width=200)
        self.menu_tree.pack(fill='both', expand=True, pady=10)

        # Formulario para nuevo producto
        form = tk.LabelFrame(self.products_frame, text='Añadir Nuevo Producto', bg=BG, fg="white", font=(None, 11, "bold"))
        form.pack(fill='x', side='bottom', pady=10, padx=10)
        
        # Botón de refrescar en la cabecera
        btn_refresh = ttk.Button(self.products_frame, text="🔄 REFRESCAR", command=self.refresh_menu, bootstyle="outline-info")
        btn_refresh.place(relx=0.85, rely=0.01)
        
        inputs = ttk.Frame(form)
        inputs.pack(fill='x', padx=10, pady=10)
        
        ttk.Label(inputs, text='Nombre:').grid(row=0, column=0, padx=5, pady=5)
        self.e_prod_name = ttk.Entry(inputs)
        self.e_prod_name.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Precio:').grid(row=0, column=2, padx=5, pady=5)
        self.e_prod_price = ttk.Entry(inputs)
        self.e_prod_price.grid(row=0, column=3, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Categoría:').grid(row=1, column=0, padx=5, pady=5)
        # Cargar categorías dinámicamente de la BD
        current_cats = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        self.e_prod_cat = ttk.Combobox(inputs, values=current_cats if current_cats else ['🍔 Combos', '🍟 Extras', '🥤 Bebidas'])
        self.e_prod_cat.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Emoji:').grid(row=1, column=2, padx=5, pady=5)
        self.e_prod_emoji = ttk.Entry(inputs)
        self.e_prod_emoji.grid(row=1, column=3, padx=5, pady=5, sticky='ew')

        ttk.Label(inputs, text='Prep (min):').grid(row=2, column=0, padx=5, pady=5)
        self.e_prod_prep = ttk.Entry(inputs)
        self.e_prod_prep.grid(row=2, column=1, padx=5, pady=5, sticky='ew')
        
        ttk.Label(inputs, text='Imagen:').grid(row=2, column=2, padx=5, pady=5)
        self.img_frame = ttk.Frame(inputs)
        self.img_frame.grid(row=2, column=3, padx=5, pady=5, sticky='ew')
        
        self.lbl_selected_img = ttk.Label(self.img_frame, text="Ninguna", foreground="gray")
        self.lbl_selected_img.pack(side='left', padx=(0, 5))
        
        ttk.Button(self.img_frame, text="Examinar...", command=self.select_product_image, bootstyle="secondary-outline").pack(side='left')
        self.selected_image_path = None
        
        inputs.columnconfigure((1, 3), weight=1)
        
        btn_frame = ttk.Frame(form)
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text='CREAR PRODUCTO', command=self.create_product, bootstyle="info").pack(side='left', padx=5)
        ttk.Button(btn_frame, text='ELIMINAR SELECCIONADO', command=self.delete_product, bootstyle="danger-outline").pack(side='left', padx=5)

    def select_product_image(self):
        from tkinter import filedialog
        import mimetypes
        
        path = filedialog.askopenfilename(
            title="Seleccionar Imagen de Producto",
            filetypes=[
                ("Imágenes", "*.png *.jpg *.jpeg *.webp *.avif"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("WebP", "*.webp")
            ]
        )
        
        if not path:
            return
        
        # Verificar tamaño (máx 5MB)
        try:
            import os
            file_size = os.path.getsize(path)
            max_size = 5 * 1024 * 1024  # 5MB
            
            if file_size > max_size:
                messagebox.showerror(
                    "Error", 
                    f"La imagen es demasiado grande.\n"
                    f"Tamaño máximo: 5MB\n"
                    f"Tamaño del archivo: {file_size / (1024*1024):.2f}MB"
                )
                return
            
            # Verificar que sea una imagen válida
            import imghdr
            img_type = imghdr.what(path)
            if img_type not in ['png', 'jpeg', 'webp']:
                messagebox.showerror("Error", "Formato de imagen no soportado. Use PNG, JPEG o WebP.")
                return
            
            self.selected_image_path = path
            self.lbl_selected_img.config(text=os.path.basename(path), foreground="blue")
            
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo validar la imagen: {e}")

    def refresh_menu(self):
        """Actualiza la tabla de productos del menú."""
        for r in self.menu_tree.get_children(): self.menu_tree.delete(r)
        rows = self.db.fetch_all('SELECT id, nombre, categoria, precio, emoji, disponible, prep_duration FROM productos_menu')
        for r in rows:
            disp = "SÍ" if r[5] else "NO"
            prep = r[6] if r[6] is not None else ''
            self.menu_tree.insert('', 'end', values=(r[0], r[1], r[2], f"${r[3]:.2f}", r[4], disp, prep))
        
        # Aprovechar para refrescar categorías en el combo
        current_cats = [row[0] for row in self.db.fetch_all('SELECT DISTINCT categoria FROM productos_menu WHERE categoria IS NOT NULL')]
        if hasattr(self, 'e_prod_cat'):
            self.e_prod_cat['values'] = current_cats

    @require_permission(Permissions.PRODUCT_MANAGE)
    def create_product(self):
        """Inserta un nuevo producto en el menú."""
        n, p, c, e = self.e_prod_name.get().strip(), self.e_prod_price.get().strip(), self.e_prod_cat.get().strip(), self.e_prod_emoji.get().strip()
        prep_val = self.e_prod_prep.get().strip() if hasattr(self, 'e_prod_prep') else ''
        try:
            prep_int = int(prep_val) if prep_val else None
        except Exception:
            prep_int = None
        if not n or not p:
            messagebox.showwarning('Error', 'Nombre y precio son obligatorios')
            return
        try:
            self.db.execute('INSERT INTO productos_menu (nombre, precio, categoria, emoji, prep_duration) VALUES (?,?,?,?,?)', (n, float(p), c, e or '🍽', prep_int))
            
            # Copiar imagen si fue seleccionada
            if hasattr(self, 'selected_image_path') and self.selected_image_path:
                import shutil, os, re
                img_dir = 'Imagenes'
                if not os.path.exists(img_dir):
                    os.makedirs(img_dir)
                    
                ext = os.path.splitext(self.selected_image_path)[1]
                def normalize(t): return re.sub(r'[^a-zA-Z0-9]', '', str(t)).lower()
                norm_n = normalize(n)
                dest_path = os.path.join(img_dir, f"{norm_n}{ext}")
                
                try:
                    shutil.copy2(self.selected_image_path, dest_path)
                except Exception as img_err:
                    logging.error(f"Error al copiar imagen: {img_err}")
                    messagebox.showwarning("Advertencia", f"Producto creado pero no se pudo copiar la imagen: {img_err}")
                    
                self.selected_image_path = None
                if hasattr(self, 'lbl_selected_img'):
                    self.lbl_selected_img.config(text="Ninguna", foreground="gray")
                    
            messagebox.showinfo('Éxito', 'Producto añadido al menú')
            for entry in (self.e_prod_name, self.e_prod_price, self.e_prod_emoji, getattr(self, 'e_prod_prep', None)):
                if entry:
                    try:
                        entry.delete(0, 'end')
                    except Exception:
                        pass
            self.refresh_menu()
        except Exception as err:
            logging.error(f"Error al crear el producto: {err}")
            play_sound_error()
            messagebox.showerror('Error', f"No se pudo crear el producto: {err}")

    @require_permission(Permissions.PRODUCT_MANAGE)
    def delete_product(self):
        """Elimina el producto seleccionado."""
        sel = self.menu_tree.selection()
        if not sel: return
        item_id = self.menu_tree.item(sel[0])['values'][0]
        if messagebox.askyesno('Confirmar', '¿Eliminar este producto del menú?'):
            try:
                self.db.execute('DELETE FROM productos_menu WHERE id = ?', (item_id,))
                self.refresh_menu()
            except Exception as e:
                messagebox.showerror('Error', f"No se pudo eliminar: {e}")

    @require_permission(Permissions.SYSTEM_CONFIG)
    def open_printer_config(self):
        """Abre el diálogo de configuración de impresoras por tipo de documento."""
        try:
            dialog = PrinterConfigDialog(self)
            dialog.show()
        except Exception as e:
            logging.error(f"Error al abrir configuración de impresoras: {e}")
            messagebox.showerror("Error", f"No se pudo abrir la configuración: {e}")


class LoginWindow(ttk.Toplevel):
    """
    Ventana de Inicio de Sesión.
    Controla el acceso al sistema mediante credenciales.
    """
    def __init__(self, master, db):
        super().__init__(master)
        self.withdraw() # Ocultar inmediatamente mientras se construye y redimensiona
        
        self.db = db
        self.user = None # Guardará los datos del usuario si el login es exitoso
        
        # Bloqueo temporal por intentos fallidos (Seguridad contra Fuerza Bruta)
        self.locked_until = None
        self.failed_attempts = {} # username -> failed attempts count
        self.client_id = self._get_client_identifier()
        
        self.title('Login - SISTEMA POS PIK\'TA')
        self.resizable(False, False)
        # Aumentar tamaño de la ventana de login para que se aprecie mejor
        center_window(self, 500, 700)
        self.grab_set() # Bloquea interacción con la ventana principal hasta que se cierre esta
        
        # Fondo con Logo (Igual que en el APK)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        
        bg_logo_path = os.path.join('Imagenes', 'pikata.png')
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_raw = Image.open(bg_logo_path).convert('RGBA')
            self.bg_res = self.bg_raw.resize((500, 700), Image.LANCZOS)
            
            self.bg_photo = ImageTk.PhotoImage(self.bg_res)
            self.canvas.create_image(250, 350, image=self.bg_photo)
        else:
            self.canvas.config(bg='#2b3e50')

        # Usar el canvas directamente para los textos (para que tengan fondo transparente)
        self.canvas.create_text(250, 150, text='Bienvenido', font=("Helvetica", 28, 'bold'), fill="white")
        self.canvas.create_text(250, 200, text='Ingrese sus credenciales', font=("Helvetica", 16), fill="white")

        # Campo de Usuario
        self.username = ttk.Entry(self, font=(None, 16), bootstyle="info")
        self.username.insert(0, 'Usuario')
        self.username.bind('<FocusIn>', lambda e: self.username.delete(0, 'end') if self.username.get() == 'Usuario' else None)
        self.canvas.create_window(250, 260, window=self.username, width=360, height=45)

        # Campo de Contraseña
        self.password = ttk.Entry(self, show='*', font=(None, 16), bootstyle="info")
        self.canvas.create_window(250, 330, window=self.password, width=360, height=45)

        # Botones de login y cancelación
        self.btn_login = ttk.Button(self, text='INICIAR SESIÓN', bootstyle="info", command=self.try_login, cursor="hand2")
        self.canvas.create_window(250, 410, window=self.btn_login, width=360, height=50)
        
        btn_cancel = ttk.Button(self, text='Cancelar', bootstyle="secondary-outline", command=self.cancel, cursor="hand2")
        self.canvas.create_window(250, 480, window=btn_cancel, width=360, height=45)

        # Pie de página con Derechos de Autor
        self.canvas.create_text(250, 560, text='© YAFA SOLUTIONS', font=("Helvetica", 10, 'bold'), fill="#e0e0e0")

        # Atajos de teclado para login
        self.bind('<Return>', lambda e: self.try_login())
        self.bind('<Tab>', lambda e: "continue") # Asegurar que Tab funcione para saltar campos

        # Configuración de foco inicial y atajos de teclado
        self.username.focus_set()
        self.bind('<Return>', lambda e: self.try_login()) # Enter para loguear
        self.bind('<Escape>', lambda e: self.cancel())    # Escape para cerrar
        
        # Manejar el cierre por la "X" de la ventana
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        
        # Mostrar la ventana final ya renderizada
        self.deiconify()

    def _get_client_identifier(self):
        """Obtener identificador único del cliente para bloqueos estrictos."""
        import uuid, getpass, socket
        try:
            identifiers = [socket.gethostname(), getpass.getuser(), str(uuid.getnode())]
            return hashlib.sha256(''.join(identifiers).encode()).hexdigest()
        except Exception:
            return "unknown_client"

    def try_login(self):
        """Verifica el usuario y contraseña contra la base de datos (con control Anti-Fuerza Bruta)."""
        u = self.username.get().strip()
        p = self.password.get().strip()
        if not u or not p or u == 'Usuario':
            messagebox.showwarning('Aviso', 'Por favor, ingrese su usuario y contraseña')
            return
            
        # Verificar bloqueo temporal general
        if self.locked_until and datetime.now() < self.locked_until:
            remaining = (self.locked_until - datetime.now()).seconds // 60
            messagebox.showwarning(
                "Cuenta Temporalmente Bloqueada",
                f"Demasiados intentos fallidos en este dispositivo.\n"
                f"Espere {remaining} minutos antes de intentar nuevamente."
            )
            return
        
        # Consulta de validación
        row = self.db.fetch_one('SELECT id, username, password, rol, nombre_completo, two_factor_secret FROM usuarios WHERE username = ?', (u,))
        if not row:
            self._handle_failed_login(None, u, 'Usuario no encontrado')
            return
        
        stored_password = row[2]
        if verify_password(stored_password, p):
            if row[3] == 'Administrador':
                import pyotp
                secret = row[5]
                if not secret:
                    secret = pyotp.random_base32()
                    self.db.execute("UPDATE usuarios SET two_factor_secret = ? WHERE id = ?", (secret, row[0]))
                    self._show_qr_setup(secret, u)
                
                code = simpledialog.askstring("Autenticación 2FA", "Ingrese el código de Google Authenticator / Authy:", parent=self)
                totp = pyotp.TOTP(secret)
                if not code or not totp.verify(code):
                    messagebox.showerror("Acceso Denegado", "Código 2FA incorrecto.")
                    self._handle_failed_login(row[0], u, '2FA fallido')
                    return

            # Login exitoso
            self.failed_attempts[u] = 0 # Resetear contador
            self.locked_until = None
            
            user_data = {'id': row[0], 'username': row[1], 'rol': row[3], 'nombre_completo': row[4]}
            
            # Guardar en el master antes de destruir la ventana
            self.master.user = user_data
            self.master.session_token = session_manager.create_session(user_data)
            
            try:
                self.db.log_access(user_data['id'], user_data['username'], 'login')
            except Exception:
                logging.exception('Error registrando login')
            self.destroy()
        else:
            self._handle_failed_login(row[0], u, 'Contraseña incorrecta')

    def _show_qr_setup(self, secret, username):
        import pyotp, qrcode, io, tempfile
        from PIL import Image, ImageTk
        
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=username, issuer_name="PIKTA POS")
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        temp_dir = tempfile.gettempdir()
        qr_path = os.path.join(temp_dir, f"{username}_qr.png")
        img.save(qr_path)
        
        top = tk.Toplevel(self)
        top.title("Configurar 2FA")
        top.geometry("450x550")
        top.grab_set()
        
        ttk.Label(top, text="1. Escanee este código con Google Authenticator o Authy", font=(None, 11, 'bold')).pack(pady=10)
        
        photo = ImageTk.PhotoImage(file=qr_path)
        lbl = ttk.Label(top, image=photo)
        lbl.image = photo
        lbl.pack()
        
        ttk.Label(top, text=f"2. O ingrese este código manualmente:\n{secret}", font=(None, 12, "bold"), justify="center").pack(pady=10)
        
        ttk.Button(top, text="Entendido", command=top.destroy, bootstyle="success", padding=10).pack(pady=10)
        self.wait_window(top)

    def _handle_failed_login(self, user_id, username, reason):
        """Procesa un intento fallido y bloquea si es necesario."""
        try: self.db.log_access(user_id, username, 'failed_login', reason)
        except Exception: pass
        
        attempts = self.failed_attempts.get(username, 0) + 1
        self.failed_attempts[username] = attempts
        
        if attempts >= 5:
            self.locked_until = datetime.now() + timedelta(minutes=15)
            try: self.db.log_access(user_id, username, 'account_locked', f'IP/Client: {self.client_id}')
            except Exception: pass
            messagebox.showwarning(
                "Demasiados Intentos",
                "Ha excedido el número de intentos permitidos.\n"
                "Su dispositivo ha sido bloqueado por 15 minutos por motivos de seguridad."
            )
        else:
            messagebox.showerror('Error', f'Usuario o contraseña incorrectos.\nIntentos restantes: {5 - attempts}')

    def cancel(self):
        """Cierra el login sin autenticar."""
        self.user = None
        self.destroy()


from collections import OrderedDict

class LRUImageCache:
    """Caché LRU (Least Recently Used) para prevenir fugas de memoria por imágenes no liberadas."""
    def __init__(self, max_size=50):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key, load_func, *args, **kwargs):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        
        img = load_func(*args, **kwargs)
        if img:
            self.cache[key] = img
            if len(self.cache) > self.max_size:
                # Elimina el menos recientemente usado
                removed_key, removed_img = self.cache.popitem(last=False)
                del removed_img # Forzar GC
        return img
        
    def clear(self):
        self.cache.clear()

class PublicidadTVFrame(tk.Canvas):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, bg='black', highlightthickness=0, *args, **kwargs)
        self.media_list = []
        self.current_idx = 0
        self.is_playing = False
        self.video_cap = None
        self.after_id = None
        self.image_photo = None
        self.width = 800
        self.height = 600
        
        import os
        self.ads_dir = os.path.abspath(os.path.join('Imagenes', 'publicidad'))
        self.refresh_media_list()
        
        self.bind("<Configure>", self.on_resize)
        
        self.btn_salir = ttk.Button(self, text="✖ Cerrar Sesión", bootstyle="danger", command=self.exit_tv, padding=10)
        self.create_window(20, 20, window=self.btn_salir, anchor='nw', tags="ui")
        
    def refresh_media_list(self):
        import os
        self.media_list = []
        if os.path.exists(self.ads_dir):
            for f in os.listdir(self.ads_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif', '.mp4', '.avi', '.mov')):
                    self.media_list.append(os.path.join(self.ads_dir, f))
                    
    def on_resize(self, event):
        self.width = event.width
        self.height = event.height
        if not self.is_playing and self.media_list:
            self.is_playing = True
            self.play_next()
            
    def play_next(self):
        if not self.media_list: return
        file_path = self.media_list[self.current_idx]
        self.current_idx = (self.current_idx + 1) % len(self.media_list)
        
        if file_path.lower().endswith(('.mp4', '.avi', '.mov')):
            self.play_video(file_path)
        else:
            self.show_image(file_path)
            
    def show_image(self, file_path):
        try:
            from PIL import Image, ImageTk
            img = Image.open(file_path)
            img.thumbnail((self.width, self.height), Image.Resampling.LANCZOS)
            bg = Image.new('RGB', (self.width, self.height), (0, 0, 0))
            offset = ((self.width - img.width) // 2, (self.height - img.height) // 2)
            bg.paste(img, offset)
            
            self.image_photo = ImageTk.PhotoImage(bg)
            self.delete("media")
            self.create_image(self.width//2, self.height//2, image=self.image_photo, tags="media")
            self.tag_lower("media")
        except Exception as e:
            import logging
            logging.error(f"Error cargando imagen TV: {e}")
            
        self.after_id = self.after(10000, self.play_next)
        
    def play_video(self, file_path):
        try:
            if not CV2_AVAILABLE:
                self.show_image(file_path) 
                return
            
            import cv2
            self.video_cap = cv2.VideoCapture(file_path)
            fps = self.video_cap.get(cv2.CAP_PROP_FPS) or 30
            self.delay = max(10, int(1000 / fps))
            self.update_video_frame()
        except Exception as e:
            import logging
            logging.error(f"Error iniciando video TV: {e}")
            self.play_next()
            
    def update_video_frame(self):
        if not getattr(self, 'video_cap', None): return
        ret, frame = self.video_cap.read()
        if ret:
            try:
                import cv2
                from PIL import Image, ImageTk
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                img.thumbnail((self.width, self.height), Image.Resampling.LANCZOS)
                bg = Image.new('RGB', (self.width, self.height), (0, 0, 0))
                offset = ((self.width - img.width) // 2, (self.height - img.height) // 2)
                bg.paste(img, offset)
                
                self.image_photo = ImageTk.PhotoImage(bg)
                self.delete("media")
                self.create_image(self.width//2, self.height//2, image=self.image_photo, tags="media")
                self.tag_lower("media")
                self.after_id = self.after(self.delay, self.update_video_frame)
            except Exception as e:
                import logging
                logging.error(f"Error frame video: {e}")
                if getattr(self, 'video_cap', None):
                    self.video_cap.release()
                    self.video_cap = None
                self.play_next()
        else:
            if getattr(self, 'video_cap', None):
                self.video_cap.release()
                self.video_cap = None
            self.play_next()
            
    def exit_tv(self):
        if getattr(self, 'after_id', None):
            self.after_cancel(self.after_id)
        if getattr(self, 'video_cap', None):
            self.video_cap.release()
            
        app = self.winfo_toplevel()
        if hasattr(app, 'logout'):
            app.logout()

class App(tk.Tk):
    """
    Clase Principal de la Aplicación.
    Gestiona el ciclo de vida del programa, el login persistente y el dashboard principal.
    """
    def __init__(self):
        # Iniciar ventana principal y ocultarla INMEDIATAMENTE antes de cargar temas
        super().__init__()
        self.withdraw()
        
        # Cargar el tema superhero
        self.style = ttk.Style(theme="superhero")
        self.withdraw() # Ocultar ventana principal al inicio
        
        self.title('SISTEMA POS PIK\'TA - Gestión de Restaurante')
        self.db = DatabaseManager()
        self.user = None
        self.session_token = None

        play_sound_startup() # Sonido de bienvenida

        # --- Bucle de Login Persistente ---
        self.run_login_loop()

        # Construir la interfaz mientras la ventana está oculta (withdraw)
        self.build()
        
        # Configurar navegación global por teclado
        self.bind_all('<Return>', self._on_global_return)
        
        # Iniciar verificación periódica de sesión (cada 1 minuto)
        self.after(60000, self._check_session_periodically)
        
        # Ahora que está construida, posicionar y mostrar
        self.geometry("1280x800")
        center_window(self, 1280, 800)
        self.state('zoomed')
        self.deiconify()
        self.attributes('-alpha', 1.0) # Restaurar opacidad
        
        # Pie de página global con Derechos de Autor
        footer = ttk.Frame(self, bootstyle="secondary", padding=5)
        footer.pack(fill='x', side='bottom')
        ttk.Label(footer, text='SISTEMA POS PIK\'TA | Desarrollado por YAFA SOLUTIONS © 2026', 
                  font=(None, 10, 'bold'), bootstyle="inverse-secondary").pack()

    def _on_tab_changed(self, event):
        """Asegura que al cambiar de pestaña, el widget principal reciba el foco."""
        try:
            tab_id = self.notebook.select()
            if not tab_id: return
            frame = self.nametowidget(tab_id)
            
            # Si el frame tiene un listbox (como KDS), darle foco directamente
            if hasattr(frame, 'listbox'):
                # Esperar un momento a que el frame se renderice completamente
                self.after(200, lambda: self._focus_kds_list(frame))
            # Para otros casos, buscar el primer widget que acepte foco
            else:
                frame.focus_set()
        except Exception:
            pass

    def _focus_kds_list(self, frame):
        """Asistente para dar foco al listbox de KDS con selección inicial."""
        try:
            frame.listbox.focus_set()
            if frame.listbox.size() > 0:
                if not frame.listbox.curselection():
                    frame.listbox.selection_set(0)
                    frame.listbox.activate(0)
        except Exception:
            pass

    def run_login_loop(self):
        """Maneja el proceso de inicio de sesión hasta que sea exitoso o se cierre la ventana."""
        while not self.user:
            login = LoginWindow(self, self.db)
            self.wait_window(login)
            if not self.user:
                # Si el usuario es None, significa que cerró la ventana o canceló
                self.destroy()
                sys.exit(0) # Salida total inmediata
                return

    def _check_session_periodically(self):
        """Verifica si la sesión sigue siendo válida."""
        if self.session_token and not session_manager.validate_session(self.session_token):
            messagebox.showwarning("Sesión Expirada", "Su sesión ha expirado por inactividad. Por favor, inicie sesión de nuevo.")
            self.logout()
        else:
            # Seguir verificando cada minuto
            self.after(60000, self._check_session_periodically)

    def _on_global_return(self, event):
        """Manejador global para la tecla ENTER para mejorar accesibilidad."""
        w = self.focus_get()
        if not w: return
        
        # Si es un botón, ejecutarlo
        if isinstance(w, (ttk.Button, tk.Button)):
            w.invoke()
            return

        try:
            if not hasattr(self, 'notebook') or not self.notebook.winfo_exists(): return
            tab_id = self.notebook.select()
            if not tab_id: return
        except Exception:
            return
        
        # Obtener el nombre de la pestaña actual
        tab_text = self.notebook.tab(self.notebook.select(), "text")
        
        # Caso especial para Cocina (KDS)
        if "Cocina" in tab_text:
            frame = self.nametowidget(tab_id)
            if hasattr(frame, 'advance_order_state'):
                frame.advance_order_state()
                return

        # Comportamiento por defecto para otros widgets
        if isinstance(w, tk.Listbox):
            w.event_generate('<Return>')
        elif isinstance(w, (ttk.Radiobutton, tk.Radiobutton)):
            w.invoke()
        elif hasattr(w, '_card_cmd'):
            w._card_cmd()

    def build(self):
        """Crea el diseño general."""
        # --- Cabecera Superior (Más grande y clara) ---
        header = ttk.Frame(self, padding=(30, 20), bootstyle="secondary")
        header.pack(fill='x')
        
        user_info = ttk.Frame(header, bootstyle="secondary")
        user_info.pack(side='left')
        ttk.Label(user_info, text=f"Bienvenido(a), {self.user.get('nombre_completo')}", font=(None, 14), bootstyle="inverse-secondary").pack(anchor='w')
        ttk.Label(user_info, text='SISTEMA POS PIK\'TA', font=(None, 26, 'bold'), bootstyle="inverse-secondary").pack(anchor='w')

        # Botón para salir (más grande)
        self.btn_logout = ttk.Button(header, text='Cerrar Sesión', command=self.logout, bootstyle="danger", cursor="hand2", padding=12)
        self.btn_logout.pack(side='right', pady=10)
        self.btn_logout.bind('<Return>', lambda e: self.logout())

        # --- Contenedor de Pestañas (Navegación Principal) ---
        style = ttk.Style()
        style.layout('Hidden.TNotebook.Tab', []) 
        # Configurar el Notebook para que no tenga bordes ni fondos que tapen el logo
        style.configure('Hidden.TNotebook', borderwidth=0, highlightthickness=0, background=BG)
        style.configure('Hidden.TNotebook.Tab', background=BG)
        
        # Usamos un Frame maestro para el fondo que contenga el Notebook
        self.master_bg = tk.Frame(self, bg=BG)
        self.master_bg.pack(fill='both', expand=True)

        # IMPORTANTE: Para transparencia, los frames deben heredar el fondo del Label o ser transparentes
        # Como Tkinter no tiene transparencia real de widgets, cada módulo dibuja su propio logo.
        self.notebook = ttk.Notebook(self.master_bg, style='Hidden.TNotebook')
        self.notebook.place(x=0, y=0, relwidth=1, relheight=1)
        
        # Vincular evento de cambio de pestaña para asegurar el foco correcto
        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed)

        role = self.user.get('rol', '').lower()
        
        if role == 'publicidad':
            header.pack_forget() # Ocultar cabecera superior para pantalla completa
            tv_frame = PublicidadTVFrame(self.notebook)
            self.notebook.add(tv_frame, text='TV')
            self.notebook.select(tv_frame)
            return

        bg_logo_path = os.path.join('Imagenes', 'pikta2.png')

        # --- Dashboard (Pestaña Inicial) ---
        # Usamos un Canvas como base para permitir el logo de fondo real
        home = tk.Canvas(self.notebook, bg=BG, highlightthickness=0)
        self.notebook.add(home, text='Inicio')
        self.notebook.select(home)

        # Variables para control de renderizado y caché
        self.last_dash_w, self.last_dash_h = 0, 0
        self.image_cache = LRUImageCache(max_size=50)
        if os.path.exists(bg_logo_path) and PIL_AVAILABLE:
            self.bg_image_raw = Image.open(bg_logo_path)
        else:
            self.bg_image_raw = None

        def render_dashboard(e=None):
            cw = home.winfo_width()
            ch = home.winfo_height()
            if cw < 50 or ch < 50: # Esperar a tener dimensiones reales
                home.after(100, render_dashboard)
                return
            
            if cw == self.last_dash_w and ch == self.last_dash_h: return
            
            self.last_dash_w, self.last_dash_h = cw, ch
            home.delete("all")

            # 1. Dibujar el logo de fondo (si está disponible)
            if self.bg_image_raw:
                img_res = self.bg_image_raw.resize((cw, ch), Image.LANCZOS)
                home.bg_img = ImageTk.PhotoImage(img_res)
                home.create_image(cw//2, ch//2, image=home.bg_img, tags="bg")

            # 2. Dibujar las tarjetas (Simuladas en el Canvas para transparencia real)
            all_cards_data = [
                ('WhatsApp.jpg', 'WhatsApp Web', 'Gestión de clientes.', self.open_whatsapp, SUCCESS, ['Administrador', 'Supervisor', 'Cajera']),
                ('pos.png', 'Caja / POS', 'Ventas y cobros.', self.open_pos, INFO, ['Administrador', 'Supervisor', 'Cajera']),
                ('user.png', 'Mesero', 'Pedidos a mesa.', self.open_mesero, WARNING, ['Administrador', 'Supervisor', 'Cajera', 'Mesero']),
                ('cocina.jpeg', 'Cocina (KDS)', 'Gestión de órdenes.', self.open_kds, DANGER, ['Administrador', 'Supervisor', 'Cocina']),
                ('admin.jpeg', 'Admin', 'Configuración.', self.open_admin, PRIMARY, ['Administrador', 'Supervisor'])
            ]
            
            user_role = self.user.get('rol', '') if self.user else ''
            # Filtrar tarjetas permitidas para este rol (ignorar el último elemento de permisos)
            cards_data = [c[:5] for c in all_cards_data if user_role in c[5]]

            # Mapeo de bootstyles a colores reales para el canvas (Superhero Theme)
            color_map = {
                SUCCESS: ("#5cb85c", "#1e3d23"), # (Borde, Fondo hover muy tenue)
                INFO: ("#5bc0de", "#1b3a42"),
                WARNING: ("#f0ad4e", "#42321b"),
                DANGER: ("#d9534f", "#3d1e1e"),
                PRIMARY: ("#df691a", "#42241b")
            }

            n_cards = len(cards_data)
            card_w, card_h = 220, 260
            gap = 30
            total_w = (card_w * n_cards) + (gap * (n_cards - 1))
            start_x = (cw - total_w) // 2
            start_y = (ch - card_h) // 2

            # Color de borde por defecto
            DEFAULT_OUTLINE = "#7975A0"

            # Contenedor para botones invisibles que permiten navegación TAB en el Canvas
            if not hasattr(home, 'tab_focus_frame'):
                home.tab_focus_frame = ttk.Frame(home, width=0, height=0)
            else:
                for w in home.tab_focus_frame.winfo_children(): w.destroy()
            
            home.tab_focus_frame.place(x=-100, y=-100) # Fuera de vista
            focus_buttons = []

            for i, (img_name, title, desc, cmd, bootstyle_name) in enumerate(cards_data):
                x = start_x + (card_w + gap) * i
                y = start_y
                
                # Colores reales desde el mapeo
                hover_color, hover_bg = color_map.get(bootstyle_name, ("#ffffff", "#333333"))
                
                # Tags únicos para cada tarjeta y sus elementos
                tag = f"card_{i}"
                bg_tag = f"bg_{i}"
                rect_tag = f"rect_{i}"

                # Crear un botón invisible para capturar el foco TAB
                btn_focus = ttk.Button(home.tab_focus_frame, command=cmd)
                btn_focus.pack()
                focus_buttons.append(btn_focus)
                
                # Eventos de foco para navegación por teclado
                def on_focus(e, rt=rect_tag, bt=bg_tag, col=hover_color, bg=hover_bg):
                    home.itemconfig(rt, outline=col, width=6)
                    home.itemconfig(bt, fill=bg)
                
                def on_blur(e, rt=rect_tag, bt=bg_tag):
                    home.itemconfig(rt, outline=DEFAULT_OUTLINE, width=2)
                    home.itemconfig(bt, fill="")

                btn_focus.bind("<FocusIn>", on_focus)
                btn_focus.bind("<FocusOut>", on_blur)
                
                # Navegación por flechas entre botones de foco
                def make_arrow_nav(idx):
                    def nav(e):
                        if e.keysym == 'Left' and idx > 0:
                            focus_buttons[idx-1].focus_set()
                        elif e.keysym == 'Right' and idx < len(focus_buttons)-1:
                            focus_buttons[idx+1].focus_set()
                    return nav
                
                btn_focus.bind("<Left>", make_arrow_nav(i))
                btn_focus.bind("<Right>", make_arrow_nav(i))
                
                # 1. Rectángulo de fondo para el hover (inicialmente transparente/oculto)
                home.create_rectangle(x, y, x + card_w, y + card_h, 
                                    fill="", outline="", width=0, tags=(tag, bg_tag))
                
                # 2. Dibujar borde de la tarjeta
                home.create_rectangle(x, y, x + card_w, y + card_h, 
                                    outline=DEFAULT_OUTLINE, width=2, 
                                    tags=(tag, rect_tag, "card_rect"))
                
                # Cargar e insertar imagen (usando caché)
                img_path = os.path.join('Imagenes', img_name)
                card_icon = self.image_cache.get(img_path, load_image, img_path, size=(90, 90))
                if card_icon:
                    # Guardar referencia para que no se pierda
                    if not hasattr(home, 'icons'): home.icons = {}
                    home.icons[tag] = card_icon
                    home.create_image(x + card_w//2, y + 60, image=card_icon, tags=(tag, "icon"))
                
                # Título
                home.create_text(x + card_w//2, y + 140, text=title, fill="#287F1E",
                               font=(None, 18, 'bold'), width=200, justify='center', tags=(tag, "title"))
                
                # Descripción
                home.create_text(x + card_w//2, y + 200, text=desc, fill="#287F1E",
                               font=(None, 14), width=180, justify='center', tags=(tag, "desc"))

                # Hacer que toda la zona sea interactiva
                home.tag_bind(tag, "<Button-1>", lambda e, c=cmd: c())
                home.tag_bind(tag, "<Return>", lambda e, c=cmd: c())
                # Hover: cambia borde y el fondo completo para que se parezca al admin
                home.tag_bind(tag, "<Enter>", lambda e, rt=rect_tag, bt=bg_tag, col=hover_color, bg=hover_bg: (
                    home.itemconfig(rt, outline=col, width=6), 
                    home.itemconfig(bt, fill=bg),
                    home.config(cursor="hand2")
                ))
                home.tag_bind(tag, "<Leave>", lambda e, rt=rect_tag, bt=bg_tag: (
                    home.itemconfig(rt, outline=DEFAULT_OUTLINE, width=2), 
                    home.itemconfig(bt, fill=""),
                    home.config(cursor="")
                ))

        home.bind("<Configure>", lambda e: render_dashboard())
        # Llamada inicial proactiva
        home.after(100, render_dashboard)

        # --- Carga Dinámica de Pestañas según Rol (OPTIMIZADO) ---
        # No cargamos el contenido de las pestañas aquí para que el inicio sea instantáneo.
        # Las pestañas se crearán bajo demanda en los métodos open_...
        pass

    def open_whatsapp(self):
        """Cambia a la pestaña de WhatsApp Web y lanza automáticamente la ventana integrada."""
        idx, frame = self._get_or_create_tab('WhatsApp Web', WhatsAppFrame)
        self.notebook.select(idx)
        # Lanzar WhatsApp automáticamente al entrar
        if hasattr(frame, 'connect_wa'):
            frame.connect_wa()

    def open_pos(self):
        """Cambia a la pestaña del Punto de Venta."""
        idx, frame = self._get_or_create_tab('Caja / POS', POSFrame)
        self.notebook.select(idx)
        
        # --- Sincronizar sesión activa desde la DB si no está cargada ---
        if hasattr(frame, 'session_id') and not frame.session_id:
            last_session = self.db.fetch_one(
                "SELECT id FROM caja_sesiones WHERE usuario_id = ? AND estado = 'ABIERTO' ORDER BY id DESC LIMIT 1",
                (self.user.get('id'),)
            )
            if last_session:
                frame.session_id = last_session[0]
                logging.info(f"Sesión de caja #{frame.session_id} recuperada para {self.user.get('username')}")

        # Solo renderizar si no hay hijos o para refrescar datos importantes
        if hasattr(frame, 'products_frame') and not frame.products_frame.winfo_children():
            if hasattr(frame, 'render_products'): frame.render_products()
        if hasattr(frame, 'refresh_unpaid_orders'): frame.refresh_unpaid_orders()

    def open_mesero(self):
        """Cambia a la pestaña de Mesero."""
        idx, frame = self._get_or_create_tab('Mesero', MeseroFrame)
        self.notebook.select(idx)
        if hasattr(frame, 'products_frame') and not frame.products_frame.winfo_children():
            if hasattr(frame, 'render_products'): frame.render_products()

    def open_kds(self):
        """Cambia a la pestaña de Cocina."""
        idx, frame = self._get_or_create_tab('Cocina (KDS)', KDSFrame)
        self.notebook.select(idx)
        if hasattr(frame, 'refresh'): frame.refresh()

    def open_admin(self):
        """Cambia a la pestaña de Administración."""
        if self.user.get('rol') not in ['Administrador', 'Supervisor']:
            messagebox.showerror('Acceso Denegado', 'Solo el Administrador o Supervisor pueden ingresar a este módulo.')
            return
            
        idx, frame = self._get_or_create_tab('Admin', AdminFrame)
        self.notebook.select(idx)
        if hasattr(frame, 'refresh'): frame.refresh()

    def _get_or_create_tab(self, name, frame_class):
        """Busca una pestaña por nombre o la crea si no existe (Lazy Loading)."""
        for i in range(self.notebook.index('end')):
            if self.notebook.tab(i, 'text') == name:
                return i, self.notebook.nametowidget(self.notebook.tabs()[i])
        
        # Si no existe, crearla dinámicamente
        if frame_class in (POSFrame, MeseroFrame, KDSFrame, AdminFrame):
            frame = frame_class(self.notebook, self.db, user=self.user)
        else:
            frame = frame_class(self.notebook, self.db)
            
        self.notebook.add(frame, text=name)
        return self.notebook.index('end') - 1, frame

    def logout(self):
        """Cierra la sesión del usuario y regresa a la pantalla de login con limpieza profunda de memoria."""
        import gc
        try:
            if self.session_token:
                session_manager.close_session(self.session_token)
            
            if getattr(self, 'user', None):
                # Registrar el evento de salida en los logs
                self.db.log_access(self.user.get('id'), self.user.get('username'), 'logout')
        except Exception:
            logging.exception('Error al registrar logout')
        
        # Ocultar ventana y resetear estado
        self.withdraw()
        self.user = None
        self.session_token = None
        
        # 1. Limpiar caché de imágenes LRU
        if hasattr(self, 'image_cache'):
            self.image_cache.clear()
            
        # 2. Limpiar referencias directas a imágenes en Canvas/Notebook
        if hasattr(self, 'icons'):
            self.icons.clear()
        
        # Eliminar todos los widgets actuales para reconstruir desde cero al re-loguear
        for widget in self.winfo_children():
            widget.destroy()
            
        # 3. Forzar Garbage Collection profundo antes de re-iniciar
        gc.collect()
            
        # Reiniciar el bucle de login
        self.run_login_loop()
        
        # Si el login fue exitoso, reconstruir y mostrar de nuevo
        self.build()
        self.deiconify()
        self.state('zoomed')
        
        # Re-iniciar el pie de página
        footer = ttk.Frame(self, bootstyle="secondary", padding=5)
        footer.pack(fill='x', side='bottom')
        ttk.Label(footer, text='SISTEMA POS PIK\'TA | Desarrollado por YAFA SOLUTIONS © 2026', 
                  font=(None, 10, 'bold'), bootstyle="inverse-secondary").pack()


def check_dependencies():
    """Verifica dependencias críticas antes de iniciar."""
    missing = []
    
    try:
        import bcrypt
    except ImportError:
        missing.append("bcrypt")
    
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        missing.append("cryptography")
    
    try:
        import PIL
    except ImportError:
        missing.append("Pillow")
        
    try:
        import cv2
    except ImportError:
        missing.append("opencv-python")
    
    if missing:
        print("=" * 60)
        print("ERROR: Faltan dependencias críticas:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nInstale con:")
        print(f"  pip install {' '.join(missing)}")
        print("=" * 60)
        sys.exit(1)

# =============================================================================
# PUNTO DE ENTRADA DEL PROGRAMA
# =============================================================================
if __name__ == '__main__':
    check_dependencies()
    multiprocessing.freeze_support()
    
    # ---------------------------------------------------------
    # SEGURIDAD: Prevenir ejecución múltiple (Single Instance)
    # ---------------------------------------------------------
    import ctypes
    # Se crea un Mutex con nombre único para el sistema
    mutex_name = "PIKTA_POS_SYSTEM_MUTEX_UNIQUE_2026"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    last_error = ctypes.windll.kernel32.GetLastError()
    
    if last_error == 183: # ERROR_ALREADY_EXISTS
        import tkinter as tk
        from tkinter import messagebox
        import sys
        
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Alerta de Seguridad", 
            "El SISTEMA POS PIK'TA ya se encuentra en ejecución en este equipo.\n\n"
            "Por razones de seguridad y para evitar conflictos con la base de datos, "
            "no se permite abrir múltiples sesiones al mismo tiempo."
        )
        root.destroy()
        sys.exit(0)
    # ---------------------------------------------------------

    # Iniciar Servidor API para APK Móvil (Opción B)
    if api_server is not None:
        api_thread = threading.Thread(target=api_server.run_server, daemon=True)
        api_thread.start()
        print("Servidor API (Móvil) iniciado en segundo plano.")

    # Crear e iniciar la aplicación principal
    app = App()
    app.mainloop()
