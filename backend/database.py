import sqlite3
from datetime import datetime
from pathlib import Path

# caminho absoluto: sempre backend/sorteio.db
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sorteio.db"


def caminho_db() -> str:
    """Retorna o caminho absoluto do banco (string)."""
    return str(DB_PATH)


def conectar():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # espera o lock liberar ao invés de falhar na hora
    conn.execute("PRAGMA busy_timeout = 30000;")  # 30s

    # melhora concorrência de leitura/escrita
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")

    return conn

def _add_coluna_se_nao_existir(cursor, tabela: str, coluna_def: str):
    try:
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna_def}")
    except sqlite3.OperationalError:
        # coluna já existe (ou alteração não permitida)
        pass


def criar_tabela():
    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS participantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE,
            cpf TEXT UNIQUE,
            whatsapp TEXT,
            curso TEXT,
            perfil TEXT,
            status TEXT DEFAULT 'INSCRITO',
            bloqueado INTEGER DEFAULT 0,
            semestre TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_sorteios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participante_id INTEGER,
            nome TEXT,
            email TEXT,
            data_sorteio TEXT
        )
    """)

    # migrações seguras
    _add_coluna_se_nao_existir(cursor, "participantes", "confirmado INTEGER DEFAULT 0")
    _add_coluna_se_nao_existir(cursor, "participantes", "data_sorteio TEXT")
    _add_coluna_se_nao_existir(cursor, "participantes", "prioridade INTEGER")

    conexao.commit()
    conexao.close()

def registrar_vencedor(id_participante, nome, email, conn=None):
    """
    Se conn for passado, usa a MESMA conexão (evita lock).
    Se conn não for passado, abre/fecha uma conexão própria.
    """
    owns_conn = False
    if conn is None:
        conn = conectar()
        owns_conn = True

    cursor = conn.cursor()
    data = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO historico_sorteios (participante_id, nome, email, data_sorteio)
        VALUES (?, ?, ?, ?)
    """, (id_participante, nome, email, data))

    if owns_conn:
        conn.commit()
        conn.close()