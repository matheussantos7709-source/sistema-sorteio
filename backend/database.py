import os
import psycopg2
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse

DATABASE_URL = os.getenv("DATABASE_URL")

def conectar():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não definida.")

    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


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
            confirmado BOOLEAN DEFAULT FALSE,
            bloqueado BOOLEAN DEFAULT FALSE,
            data_sorteio TIMESTAMP,
            prioridade INTEGER
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS historico_sorteios (
            id SERIAL PRIMARY KEY,
            participante_id INTEGER REFERENCES participantes(id),
            nome TEXT,
            email TEXT,
            data_sorteio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()


def registrar_vencedor(participante_id, nome, email, conn=None):
    close_conn = False

    if conn is None:
        conn = conectar()
        close_conn = True

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO historico_sorteios (participante_id, nome, email)
        VALUES (%s, %s, %s)
    """, (participante_id, nome, email))

    if close_conn:
        conn.commit()
        conn.close()


def caminho_db():
    return DATABASE_URL