from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple
from io import BytesIO

import sqlite3
from openpyxl import Workbook, load_workbook

try:
    from backend.database import conectar, registrar_vencedor
except Exception:
    from database import conectar, registrar_vencedor


# -----------------------------
# CADASTRAR / ATUALIZAR (UPSERT)
# -----------------------------

def cadastrar_participante(nome, email, cpf, whatsapp, curso, perfil, semestre):
    conexao = conectar()
    cursor = conexao.cursor()

    nome = (nome or "").strip()
    email = (email or "").strip()
    cpf = (cpf or "").strip()
    whatsapp = (whatsapp or "").strip()
    curso = (curso or "").strip()
    perfil = (perfil or "").strip()
    semestre = (semestre or "").strip()

    if not nome:
        conexao.close()
        raise ValueError("Nome é obrigatório.")

    existe = None

    # procura por email/cpf SOMENTE se estiver preenchido
    if email:
        cursor.execute("SELECT id FROM participantes WHERE email = %s", (email,))
        existe = cursor.fetchone()

    if not existe and cpf:
        cursor.execute("SELECT id FROM participantes WHERE cpf = ?", (cpf,))
        existe = cursor.fetchone()

    try:
        if existe:
            cursor.execute("""
                UPDATE participantes
                SET nome=?,
                    email=?,
                    cpf=?,
                    whatsapp=?,
                    curso=?,
                    perfil=?,
                    semestre=?,
                    status='INSCRITO',
                    bloqueado=0
                WHERE id=?
            """, (
                nome,
                email or None,
                cpf or None,
                whatsapp,
                curso,
                perfil,
                semestre,
                existe[0]
            ))
        else:
            cursor.execute("""
                INSERT INTO participantes
                (nome, email, cpf, whatsapp, curso, perfil, semestre)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                nome,
                email or None,
                cpf or None,
                whatsapp,
                curso,
                perfil,
                semestre
            ))

        conexao.commit()

    except sqlite3.IntegrityError as e:
        conexao.rollback()
        msg = str(e).lower()
        if "participantes.email" in msg:
            raise ValueError("Esse e-mail já está cadastrado.")
        if "participantes.cpf" in msg:
            raise ValueError("Esse CPF já está cadastrado.")
        raise ValueError("Dados inválidos ou duplicados.")
    finally:
        conexao.close()


# -----------------------------
# CONFIRMAR PRESENÇA
# -----------------------------

def confirmar_presenca_por_email(email: str) -> int:
    conexao = conectar()
    cursor = conexao.cursor()

    email_norm = (email or "").strip().lower()
    if not email_norm:
        conexao.close()
        return 0

    cursor.execute("""
        UPDATE participantes
        SET confirmado = 1,
            status = 'CONFIRMADO'
        WHERE status = 'SELECIONADO'
          AND email IS NOT NULL
          AND LOWER(TRIM(email)) = ?
    """, (email_norm,))

    alterados = cursor.rowcount
    conexao.commit()
    conexao.close()
    return alterados


# -----------------------------
# PROMOVER SUPLENTE
# -----------------------------

try:
    from backend.database import conectar, registrar_vencedor
except Exception:
    from database import conectar, registrar_vencedor


def promover_suplente_se_expirou(prazo_horas: int = 48) -> Tuple[bool, str]:
    if prazo_horas < 0:
        return False, "prazo_horas inválido (deve ser >= 0)."

    limite_dt = datetime.now() - timedelta(hours=prazo_horas)
    limite_str = limite_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = conectar()
    cur = conn.cursor()

    try:
        # 1) selecionado expirado (não confirmado)
        cur.execute("""
            SELECT id, prioridade
            FROM participantes
            WHERE status='SELECIONADO'
              AND confirmado=0
              AND data_sorteio IS NOT NULL
              AND data_sorteio <= ?
            ORDER BY data_sorteio ASC
            LIMIT 1
        """, (limite_str,))
        expirado = cur.fetchone()

        if not expirado:
            return False, "Nenhum selecionado expirado encontrado."

        expirado_id = expirado[0]
        expirado_prioridade = expirado[1]

        # 2) pega o primeiro suplente
        cur.execute("""
            SELECT id, nome, email
            FROM participantes
            WHERE status='SUPLENTE'
            ORDER BY prioridade ASC
            LIMIT 1
        """)
        suplente = cur.fetchone()

        if not suplente:
            # marca expirado e libera (não trava o sistema)
            cur.execute("""
                UPDATE participantes
                SET status='EXPIRADO', bloqueado=0
                WHERE id=?
            """, (expirado_id,))
            conn.commit()
            return False, "Nenhum suplente disponível; selecionado marcado como EXPIRADO."

        supl_id, supl_nome, supl_email = suplente[0], suplente[1], suplente[2]
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 3) promove suplente
        cur.execute("""
            UPDATE participantes
            SET status='SELECIONADO',
                bloqueado=1,
                confirmado=0,
                data_sorteio=?,
                prioridade=?
            WHERE id=?
        """, (agora, expirado_prioridade, supl_id))

        # 4) marca expirado como EXPIRADO e libera
        cur.execute("""
            UPDATE participantes
            SET status='EXPIRADO', bloqueado=0
            WHERE id=?
        """, (expirado_id,))

        # 5) registra no histórico
        registrar_vencedor(supl_id, supl_nome or "", supl_email or "", conn=conn)

        conn.commit()
        return True, f"Suplente promovido: {supl_nome} ({supl_email})."

    except sqlite3.OperationalError as e:
        conn.rollback()
        return False, f"Erro SQL: {e}"
    except Exception as e:
        conn.rollback()
        return False, f"Erro inesperado: {type(e).__name__}: {e}"
    finally:
        conn.close()

# -----------------------------
# HISTÓRICO
# -----------------------------

def listar_historico() -> List[Tuple[str, str, str, str]]:
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT h.data_sorteio, h.nome, h.email, COALESCE(p.status, 'REMOVIDO')
        FROM historico_sorteios h
        LEFT JOIN participantes p ON p.id = h.participante_id
        ORDER BY h.id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def limpar_historico(reset_id: bool = True) -> int:
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM historico_sorteios")
    total = cur.fetchone()[0]
    cur.execute("DELETE FROM historico_sorteios")
    if reset_id:
        cur.execute("DELETE FROM sqlite_sequence WHERE name='historico_sorteios'")
    conn.commit()
    conn.close()
    return total


# -----------------------------
# EXPORTAR XLSX
# -----------------------------

def exportar_participantes_xlsx() -> bytes:
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nome, email, cpf, whatsapp, curso, perfil, semestre, status, confirmado, data_sorteio, prioridade
        FROM participantes
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Participantes"

    ws.append([
        "id", "nome", "email", "cpf", "whatsapp", "curso", "perfil",
        "semestre", "status", "confirmado", "data_sorteio", "prioridade"
    ])

    for r in rows:
        ws.append([
            r[0], r[1], r[2], r[3], r[4], r[5], r[6],
            r[7], r[8], r[9], r[10], r[11]
        ])

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


# -----------------------------
# IMPORTAR XLSX
# -----------------------------

def importar_participantes_xlsx(file_bytes: bytes) -> Dict[str, Any]:
    wb = load_workbook(BytesIO(file_bytes))
    ws = wb.active

    header = []
    for cell in ws[1]:
        header.append((str(cell.value).strip().lower() if cell.value is not None else ""))

    def idx(colname: str):
        try:
            return header.index(colname)
        except ValueError:
            return None

    col_nome = idx("nome")
    col_email = idx("email") if idx("email") is not None else idx("e-mail")
    col_cpf = idx("cpf")
    col_whats = idx("whatsapp") if idx("whatsapp") is not None else idx("whats") if idx("whats") is not None else idx("telefone")
    col_curso = idx("curso")
    col_perfil = idx("perfil")
    col_semestre = idx("semestre")

    if col_nome is None:
        return {"ok": False, "msg": "Planilha inválida: falta a coluna 'nome' no cabeçalho.", "importados": 0, "erros": []}

    importados = 0
    erros: List[Dict[str, Any]] = []

    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        try:
            nome = (row[col_nome] if col_nome is not None else "") or ""
            email = (row[col_email] if col_email is not None else "") or ""
            cpf = (row[col_cpf] if col_cpf is not None else "") or ""
            whatsapp = (row[col_whats] if col_whats is not None else "") or ""
            curso = (row[col_curso] if col_curso is not None else "") or ""
            perfil = (row[col_perfil] if col_perfil is not None else "") or ""
            semestre = (row[col_semestre] if col_semestre is not None else "") or ""

            if str(nome).strip() == "" and str(email).strip() == "" and str(cpf).strip() == "":
                continue

            cadastrar_participante(
                str(nome).strip(),
                str(email).strip(),
                str(cpf).strip(),
                str(whatsapp).strip(),
                str(curso).strip(),
                str(perfil).strip(),
                str(semestre).strip(),
            )
            importados += 1

        except Exception as e:
            erros.append({"linha": i, "erro": str(e)})

    return {
        "ok": True,
        "msg": f"Importação finalizada. {importados} linha(s) processada(s).",
        "importados": importados,
        "erros": erros
    }