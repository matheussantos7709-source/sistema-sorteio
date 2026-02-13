import os
import ssl
from urllib.parse import urlparse, parse_qs

import pg8000


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def _parse_database_url(url: str):
    """
    Aceita:
      postgres://user:pass@host:port/db?sslmode=require
      postgresql://...
    """
    if not url:
        raise RuntimeError("DATABASE_URL não definida.")

    # Render/Heroku às vezes usa postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    u = urlparse(url)
    qs = parse_qs(u.query)

    host = u.hostname or ""
    port = u.port or 5432
    user = u.username or ""
    password = u.password or ""
    database = (u.path or "").lstrip("/")

    # sslmode: require/disable/prefer...
    sslmode = (qs.get("sslmode", ["require"])[0] or "require").lower()

    return host, port, user, password, database, sslmode


def conectar():
    host, port, user, password, database, sslmode = _parse_database_url(DATABASE_URL)

    ssl_ctx = None
    if sslmode != "disable":
        ssl_ctx = ssl.create_default_context()

    # pg8000 é DB-API: cursor() / execute() / fetchall()
    conn = pg8000.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
        ssl_context=ssl_ctx,
        timeout=30,
    )
    return conn


def criar_tabela():
    conn = conectar()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS participantes (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            email TEXT UNIQUE,
            cpf TEXT UNIQUE,
            whatsapp TEXT,
            curso TEXT,
            perfil TEXT,
            semestre TEXT,
            status TEXT DEFAULT 'INSCRITO',
            bloqueado BOOLEAN DEFAULT FALSE,
            confirmado BOOLEAN DEFAULT FALSE,
            data_sorteio TIMESTAMP,
            prioridade INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS historico_sorteios (
            id SERIAL PRIMARY KEY,
            participante_id INTEGER,
            nome TEXT,
            email TEXT,
            data_sorteio TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    conn.close()


def registrar_vencedor(id_participante, nome, email, conn=None):
    owns = False
    if conn is None:
        conn = conectar()
        owns = True

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO historico_sorteios (participante_id, nome, email)
        VALUES (%s, %s, %s)
        """,
        (id_participante, nome, email),
    )

    if owns:
        conn.commit()
        conn.close()