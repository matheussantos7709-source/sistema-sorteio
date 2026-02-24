# backend/api.py
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
from io import BytesIO
import io
import csv
import random
import os

from openpyxl import Workbook

from backend.database import criar_tabela, conectar, registrar_vencedor
from backend.services import (
    cadastrar_participante,
    confirmar_presenca_por_email,
    promover_suplente_se_expirou,
    listar_historico,
    limpar_historico,
    importar_participantes_xlsx,
)

app = FastAPI(title="Sistema de Sorteio API", version="1.0.0")

# cria tabelas no start (Postgres)
criar_tabela()
print("DB: PostgreSQL via DATABASE_URL (Render)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# HELPERS
# ---------------------------

def rows_to_dicts(cur, rows):
    cols = [c[0] for c in (cur.description or [])]
    return [dict(zip(cols, r)) for r in rows]

def fetchall_dict(cur):
    return rows_to_dicts(cur, cur.fetchall())

def _mask_db_url(url: str) -> str:
    if not url:
        return ""
    try:
        if "://" not in url:
            return "***"
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return f"{scheme}://***"
        creds, tail = rest.split("@", 1)
        if ":" in creds:
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{tail}"
        return f"{scheme}://***@{tail}"
    except Exception:
        return "***"

def _require_admin(x_admin_key: Optional[str]):
    expected = (os.getenv("ADMIN_KEY") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_KEY não configurada no servidor.")
    if not x_admin_key or x_admin_key.strip() != expected:
        raise HTTPException(status_code=401, detail="Não autorizado (admin).")

# para CSV (se quiser manter bloqueados permanentes também no csv)
def _csv_get(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

# ---------------------------
# MODELOS
# ---------------------------

class ParticipanteIn(BaseModel):
    nome: str
    email: Optional[str] = None
    cpf: Optional[str] = None
    whatsapp: Optional[str] = None
    curso: Optional[str] = None
    perfil: Optional[str] = None
    semestre: Optional[str] = None

class SorteioIn(BaseModel):
    vagas: int
    suplentes: int

class ConfirmarIn(BaseModel):
    email: str

class PromoverIn(BaseModel):
    prazo_horas: int = 48

# ---------------------------
# ROTAS BÁSICAS
# ---------------------------

@app.get("/")
def home():
    return {"ok": True, "msg": "API no ar. Acesse /docs"}

@app.get("/api/dbinfo")
def dbinfo() -> Dict[str, Any]:
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    admin_set = bool((os.getenv("ADMIN_KEY") or "").strip())
    return {
        "ok": True,
        "assinatura": "POSTGRES_BLOQUEIO_TEMP_E_PERM_2026-02-24",
        "database_url_set": bool(db_url),
        "database_url_masked": _mask_db_url(db_url) if db_url else "",
        "admin_key_set": admin_set,
    }

# ---------------------------
# ADMIN - MIGRAÇÃO (OPÇÃO B)
# ---------------------------

@app.get("/api/admin/migracao-status")
def migracao_status(x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)

    conn = conectar()
    try:
        cur = conn.cursor()

        # coluna chave existe?
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='participantes' AND column_name='chave'
            LIMIT 1
            """
        )
        has_col = cur.fetchone() is not None

        # índice existe? (no seu database.py o nome é participantes_chave_uidx)
        cur.execute(
            """
            SELECT 1
            FROM pg_indexes
            WHERE tablename='participantes' AND indexname IN ('participantes_chave_uidx','participantes_chave_unique')
            LIMIT 1
            """
        )
        has_index = cur.fetchone() is not None

        # tabela bloqueados existe?
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name='bloqueados_permanentes'
            LIMIT 1
            """
        )
        has_block_table = cur.fetchone() is not None

        return {
            "ok": True,
            "coluna_chave_existe": has_col,
            "indice_chave_existe": has_index,
            "tabela_bloqueados_existe": has_block_table,
        }
    finally:
        conn.close()


@app.post("/api/admin/migrar-schema")
def migrar_schema(x_admin_key: Optional[str] = Header(None)):
    """
    Mantido por compatibilidade.
    OBS: no seu database.py a tabela já cria chave e index.
    """
    _require_admin(x_admin_key)

    conn = conectar()
    try:
        cur = conn.cursor()

        cur.execute("ALTER TABLE participantes ADD COLUMN IF NOT EXISTS chave TEXT;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS participantes_chave_uidx ON participantes(chave);")
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

        # preencher chave onde estiver vazia (versão simples)
        cur.execute(
            """
            UPDATE participantes
            SET chave =
                LOWER(TRIM(nome)) || '|' ||
                COALESCE(NULLIF(LOWER(TRIM(email)), ''),
                         NULLIF(LOWER(TRIM(cpf)), ''),
                         NULLIF(LOWER(TRIM(whatsapp)), ''),
                         'sem_contato')
            WHERE chave IS NULL OR chave = '';
            """
        )
        updated_keys = cur.rowcount

        conn.commit()
        return {"ok": True, "msg": "Migração executada.", "chaves_preenchidas": updated_keys}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro na migração: {type(e).__name__}: {e}")
    finally:
        conn.close()

# ---------------------------
# PARTICIPANTES
# ---------------------------

@app.get("/api/participantes")
def listar_participantes():
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM participantes ORDER BY id ASC")
        return fetchall_dict(cur)
    finally:
        conn.close()

@app.post("/api/participantes")
def criar_ou_atualizar_participante(p: ParticipanteIn):
    if not (p.nome or "").strip():
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")
    try:
        cadastrar_participante(
            (p.nome or "").strip(),
            (p.email or "").strip(),
            (p.cpf or "").strip(),
            (p.whatsapp or "").strip(),
            (p.curso or "").strip(),
            (p.perfil or "").strip(),
            (p.semestre or "").strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "msg": "Participante salvo com sucesso."}

@app.delete("/api/participantes")
def deletar_todos_participantes(
    apagar_historico: bool = Query(False),  # opção extra se você quiser apagar também
    reset_ids: bool = Query(True),
):
    """
    OPÇÃO 2 (a sua): por padrão APAGA SÓ participantes.
    - NÃO apaga bloqueados_permanentes
    - histórico só apaga se apagar_historico=True
    """
    conn = conectar()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM participantes")
        total_part = int(cur.fetchone()[0] or 0)

        total_hist = 0
        if apagar_historico:
            cur.execute("SELECT COUNT(*) FROM historico_sorteios")
            total_hist = int(cur.fetchone()[0] or 0)
            cur.execute("DELETE FROM historico_sorteios")
            if reset_ids:
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('historico_sorteios','id'), 1, false)"
                )

        cur.execute("DELETE FROM participantes")
        if reset_ids:
            cur.execute("SELECT setval(pg_get_serial_sequence('participantes','id'), 1, false)")

        conn.commit()

        return {
            "ok": True,
            "msg": "Limpeza concluída (participantes apagados; bloqueio permanente preservado).",
            "participantes_removidos": total_part,
            "historico_removidos": total_hist if apagar_historico else 0,
            "reset_ids": reset_ids,
        }
    finally:
        conn.close()

@app.delete("/api/participantes/{pid}")
def deletar_participante(pid: int):
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM participantes WHERE id=%s", (pid,))
        apagados = cur.rowcount
        conn.commit()
        if apagados == 0:
            raise HTTPException(status_code=404, detail="Participante não encontrado.")
        return {"ok": True, "msg": "Participante removido."}
    finally:
        conn.close()

# ---------------------------
# SORTEIO / CONFIRMAÇÃO / PROMOÇÃO
# ---------------------------

@app.post("/api/sortear")
def sortear(payload: SorteioIn):
    vagas = int(payload.vagas)
    suplentes_qtd = int(payload.suplentes)

    if vagas <= 0:
        raise HTTPException(status_code=400, detail="Vagas deve ser > 0.")
    if suplentes_qtd < 0:
        raise HTTPException(status_code=400, detail="Suplentes deve ser >= 0.")

    conn = conectar()
    try:
        cur = conn.cursor()

        # OPÇÃO B + sua regra:
        # - só INSCRITO e não bloqueado
        # - NÃO pode estar em bloqueados_permanentes (bloqueio permanente só no CONFIRMAR)
        cur.execute(
            """
            SELECT p.id, p.nome, p.email, p.chave
            FROM participantes p
            WHERE p.status='INSCRITO'
              AND p.bloqueado=FALSE
              AND NOT EXISTS (
                SELECT 1 FROM bloqueados_permanentes b
                WHERE b.chave = p.chave
              )
            """
        )
        participantes = fetchall_dict(cur)

        if len(participantes) < (vagas + suplentes_qtd):
            raise HTTPException(
                status_code=400,
                detail="Participantes insuficientes (considerando bloqueio permanente)."
            )

        random.shuffle(participantes)

        vencedores = participantes[:vagas]
        suplentes = participantes[vagas : vagas + suplentes_qtd]
        agora = datetime.now()

        # vencedores => bloqueio TEMPORÁRIO (bloqueado=TRUE) + status SELECIONADO
        for idx, p in enumerate(vencedores, start=1):
            pid = p["id"]
            nome = p.get("nome") or ""
            email = p.get("email") or ""

            registrar_vencedor(pid, nome, email, conn=conn)

            cur.execute(
                """
                UPDATE participantes
                SET status='SELECIONADO',
                    bloqueado=TRUE,
                    confirmado=FALSE,
                    data_sorteio=%s,
                    prioridade=%s
                WHERE id=%s
                """,
                (agora, idx, pid),
            )

        # suplentes
        for idx, s in enumerate(suplentes, start=vagas + 1):
            pid = s["id"]
            cur.execute(
                """
                UPDATE participantes
                SET status='SUPLENTE',
                    confirmado=FALSE,
                    data_sorteio=%s,
                    prioridade=%s
                WHERE id=%s
                """,
                (agora, idx, pid),
            )

        conn.commit()
        return {"ok": True, "msg": "Sorteio realizado (bloqueio temporário ao selecionar; permanente só ao confirmar)."}
    finally:
        conn.close()

@app.post("/api/confirmar")
def confirmar(payload: ConfirmarIn):
    email = (payload.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email é obrigatório.")

    alterados = confirmar_presenca_por_email(email)

    if alterados > 0:
        return {
            "ok": True,
            "alterados": alterados,
            "msg": "Confirmado e bloqueado permanentemente (não volta em sorteios futuros).",
        }
    return {"ok": False, "alterados": 0, "msg": "Nenhum SELECIONADO encontrado com esse e-mail."}

@app.post("/api/promover")
def promover(payload: PromoverIn):
    prazo = int(payload.prazo_horas)
    ok, msg = promover_suplente_se_expirou(prazo)
    return {"ok": ok, "msg": msg}

# ---------------------------
# BLOQUEADOS PERMANENTES (para mostrar no front)
# ---------------------------

@app.get("/api/bloqueados-permanentes/count")
def bloqueados_count():
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bloqueados_permanentes")
        total = int(cur.fetchone()[0] or 0)
        return {"ok": True, "total": total}
    finally:
        conn.close()

@app.get("/api/bloqueados-permanentes")
def listar_bloqueados(limit: int = 200):
    limit = max(1, min(int(limit), 2000))
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT chave, nome, email, data_confirmacao
            FROM bloqueados_permanentes
            ORDER BY data_confirmacao DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = fetchall_dict(cur)
        return {"ok": True, "items": rows, "limit": limit}
    finally:
        conn.close()

# ---------------------------
# HISTÓRICO
# ---------------------------

@app.get("/api/historico")
def historico():
    dados = listar_historico()
    return [{"data_sorteio": d, "nome": n, "email": e, "status_atual": s} for (d, n, e, s) in dados]

@app.delete("/api/historico")
def apagar_historico(reset_id: bool = True):
    apagados = limpar_historico(reset_id=reset_id)
    return {"ok": True, "apagados": apagados}

# ---------------------------
# IMPORTAÇÃO
# ---------------------------

@app.post("/api/importar-csv")
async def importar_csv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .csv")

    content = await file.read()

    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(status_code=400, detail="Não foi possível ler o CSV (encoding inválido).")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV sem cabeçalho (colunas).")

    importados = 0
    ignorados = 0
    erros = 0

    # IMPORTANTE:
    # seu bloqueio permanente no CSV é feito dentro do services no XLSX,
    # mas no CSV a gente não tem isso lá. Então deixo o CSV simples:
    # - cadastra e o services faz UPSERT por chave
    # - se quiser bloquear permanente também no CSV, a gente mexe no services depois.
    for row in reader:
        try:
            nome = _csv_get(row, "nome", "Nome", "NOME")
            if not nome:
                ignorados += 1
                continue

            cadastrar_participante(
                nome,
                _csv_get(row, "email", "e-mail", "Email", "E-mail"),
                _csv_get(row, "cpf", "CPF"),
                _csv_get(row, "whatsapp", "WhatsApp", "telefone", "celular"),
                _csv_get(row, "curso", "Curso"),
                _csv_get(row, "perfil", "Perfil"),
                _csv_get(row, "semestre", "Semestre"),
            )
            importados += 1
        except Exception:
            erros += 1

    return {
        "ok": True,
        "msg": "Importação CSV concluída.",
        "importados": importados,
        "ignorados": ignorados,
        "erros": erros,
    }

@app.post("/api/importar-excel")
async def importar_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .xlsx")

    raw = await file.read()
    result = importar_participantes_xlsx(raw)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("msg", "Planilha inválida."))

    erros_list = result.get("erros", []) or []

    return {
        "ok": True,
        "msg": result.get("msg", "Importação Excel concluída."),
        "importados": int(result.get("importados", 0) or 0),
        "ignorados": int(result.get("ignorados", 0) or 0),
        "erros": erros_list,
        "erros_qtd": len(erros_list),
    }

# ---------------------------
# EXPORTAÇÃO
# ---------------------------

def _xlsx_response(wb: Workbook, filename: str) -> StreamingResponse:
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@app.get("/api/exportar-participantes")
def exportar_participantes():
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nome, email, cpf, whatsapp, curso, perfil, status, bloqueado, semestre, confirmado, data_sorteio, prioridade
            FROM participantes
            ORDER BY id ASC
            """
        )
        rows = fetchall_dict(cur)
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Participantes"
    ws.append(["ID","Nome","Email","CPF","WhatsApp","Curso","Perfil","Status","Bloqueado","Semestre","Confirmado","Data Sorteio","Prioridade"])

    for r in rows:
        ws.append([
            r.get("id"), r.get("nome"), r.get("email"), r.get("cpf"), r.get("whatsapp"), r.get("curso"), r.get("perfil"),
            r.get("status"), r.get("bloqueado"), r.get("semestre"), r.get("confirmado"), r.get("data_sorteio"), r.get("prioridade")
        ])

    nome_arquivo = f"participantes_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _xlsx_response(wb, nome_arquivo)

@app.get("/api/exportar-resultados")
def exportar_resultados():
    conn = conectar()
    try:
        cur = conn.cursor()

        wb = Workbook()
        ws = wb.active
        ws.title = "Resultado"

        def add_section(titulo: str, status: str):
            ws.append([titulo])
            ws.append(["ID", "Nome", "Email", "CPF", "WhatsApp", "Curso", "Perfil", "Semestre", "Data Sorteio", "Prioridade"])
            cur.execute(
                """
                SELECT id, nome, email, cpf, whatsapp, curso, perfil, semestre, data_sorteio, prioridade
                FROM participantes
                WHERE status=%s
                ORDER BY prioridade ASC, id ASC
                """,
                (status,),
            )
            for r in fetchall_dict(cur):
                ws.append([
                    r.get("id"), r.get("nome"), r.get("email"), r.get("cpf"), r.get("whatsapp"),
                    r.get("curso"), r.get("perfil"), r.get("semestre"), r.get("data_sorteio"), r.get("prioridade")
                ])
            ws.append([])

        add_section("CONFIRMADOS", "CONFIRMADO")
        add_section("SELECIONADOS (aguardando confirmação)", "SELECIONADO")
        add_section("SUPLENTES", "SUPLENTE")
        add_section("INSCRITOS", "INSCRITO")

    finally:
        conn.close()

    nome_arquivo = f"resultado_sorteio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _xlsx_response(wb, nome_arquivo)
