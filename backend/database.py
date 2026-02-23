import os
import ssl
from urllib.parse import urlparse, parse_qs

import pg8000


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
        url = "postgresql://" + url[len("postgres://") :]

    u = urlparse(url)
    qs = parse_qs(u.query)

    host = u.hostname or ""
    port = u.port or 5432
    user = u.username or ""
    password = u.password or ""
    database = (u.path or "").lstrip("/")

    # sslmode: require/disable/prefer/verify-full/verify-ca...
    sslmode = (qs.get("sslmode", ["require"])[0] or "require").lower()

    return host, port, user, password, database, sslmode


def _make_ssl_context(sslmode: str):
    """
    Render costuma exigir SSL, mas pode falhar a validação do certificado no pg8000.
    Para 'require/prefer' usamos SSL sem verificação.
    """
    sslmode = (sslmode or "require").lower()

    if sslmode == "disable":
        return None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def conectar():
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    host, port, user, password, database, sslmode = _parse_database_url(database_url)

    ssl_ctx = _make_ssl_context(sslmode)

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

    # participantes (SEM unique em email/cpf; a chave é a única)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS participantes (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            email TEXT,
            cpf TEXT,
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
        """
    )

    # garante coluna chave (para DBs antigos)
    cur.execute("ALTER TABLE participantes ADD COLUMN IF NOT EXISTS chave TEXT")

    # índice/unique da chave (idempotente)
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS participantes_chave_uidx ON participantes(chave)"
    )

    # histórico
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS historico_sorteios (
            id SERIAL PRIMARY KEY,
            participante_id INTEGER,
            nome TEXT,
            email TEXT,
            data_sorteio TIMESTAMP DEFAULT NOW()
        )
        """
    )

    # bloqueio permanente (nunca apagamos)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bloqueados_permanentes (
            chave TEXT PRIMARY KEY,
            nome TEXT,
            email TEXT,
            data_confirmacao TIMESTAMP DEFAULT NOW()
        )
        """
    )

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
