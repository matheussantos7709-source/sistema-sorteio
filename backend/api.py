from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from io import BytesIO
import io
import csv
import random
import os

from openpyxl import Workbook

# imports do seu projeto (monorepo)
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

criar_tabela()
print("DB: PostgreSQL via DATABASE_URL (Render)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # depois você pode restringir pro domínio do frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# HELPERS
# ---------------------------

def rows_to_dicts(cur, rows):
    """
    Converte fetchall() em lista de dicts usando cur.description
    (funciona com pg8000 / postgres)
    """
    cols = [c[0] for c in (cur.description or [])]
    return [dict(zip(cols, r)) for r in rows]

def fetchall_dict(cur):
    return rows_to_dicts(cur, cur.fetchall())

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
def dbinfo():
    return {"assinatura": "POSTGRES_V2_2026-02-12"}

# ---------------------------
# PARTICIPANTES
# ---------------------------

@app.get("/api/participantes")
def listar_participantes():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT * FROM participantes ORDER BY id ASC")
    data = fetchall_dict(cur)
    conn.close()
    return data

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
    apagar_historico: bool = Query(False),
    reset_ids: bool = Query(True),
):
    conn = conectar()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM participantes")
    total_part = int(cur.fetchone()[0] or 0)

    total_hist = 0
    if apagar_historico:
        cur.execute("SELECT COUNT(*) FROM historico_sorteios")
        total_hist = int(cur.fetchone()[0] or 0)
        cur.execute("DELETE FROM historico_sorteios")
        if reset_ids:
            # Postgres: reseta a sequence do SERIAL/IDENTITY
            cur.execute("SELECT setval(pg_get_serial_sequence('historico_sorteios','id'), 1, false)")

    cur.execute("DELETE FROM participantes")
    if reset_ids:
        cur.execute("SELECT setval(pg_get_serial_sequence('participantes','id'), 1, false)")

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "msg": "Limpeza concluída.",
        "participantes_removidos": total_part,
        "historico_removidos": total_hist if apagar_historico else 0,
        "reset_ids": reset_ids,
    }

@app.delete("/api/participantes/{pid}")
def deletar_participante(pid: int):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("DELETE FROM participantes WHERE id=%s", (pid,))
    apagados = cur.rowcount
    conn.commit()
    conn.close()
    if apagados == 0:
        raise HTTPException(status_code=404, detail="Participante não encontrado.")
    return {"ok": True, "msg": "Participante removido."}

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
    cur = conn.cursor()

    cur.execute("""
        SELECT id, nome, email
        FROM participantes
        WHERE status='INSCRITO'
          AND bloqueado=FALSE
    """)
    participantes = fetchall_dict(cur)

    if len(participantes) < (vagas + suplentes_qtd):
        conn.close()
        raise HTTPException(status_code=400, detail="Participantes insuficientes.")

    random.shuffle(participantes)

    vencedores = participantes[:vagas]
    suplentes = participantes[vagas:vagas + suplentes_qtd]
    agora = datetime.now()  # timestamp real no Postgres

    # vencedores
    for idx, p in enumerate(vencedores, start=1):
        pid = p["id"]
        nome = p.get("nome") or ""
        email = p.get("email") or ""

        registrar_vencedor(pid, nome, email, conn=conn)
        cur.execute("""
            UPDATE participantes
            SET status='SELECIONADO',
                bloqueado=TRUE,
                confirmado=FALSE,
                data_sorteio=%s,
                prioridade=%s
            WHERE id=%s
        """, (agora, idx, pid))

    # suplentes
    for idx, s in enumerate(suplentes, start=vagas + 1):
        pid = s["id"]
        cur.execute("""
            UPDATE participantes
            SET status='SUPLENTE',
                confirmado=FALSE,
                data_sorteio=%s,
                prioridade=%s
            WHERE id=%s
        """, (agora, idx, pid))

    conn.commit()
    conn.close()
    return {"ok": True, "msg": "Sorteio realizado."}

@app.post("/api/confirmar")
def confirmar(payload: ConfirmarIn):
    email = (payload.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email é obrigatório.")
    alterados = confirmar_presenca_por_email(email)
    return {"ok": alterados > 0, "alterados": alterados}

@app.post("/api/promover")
def promover(payload: PromoverIn):
    prazo = int(payload.prazo_horas)
    ok, msg = promover_suplente_se_expirou(prazo)
    return {"ok": ok, "msg": msg}

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

    # tenta decodificar com fallback
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

    for row in reader:
        try:
            nome = (row.get("nome") or "").strip()
            if not nome:
                ignorados += 1
                continue

            cadastrar_participante(
                nome,
                (row.get("email") or "").strip(),
                (row.get("cpf") or "").strip(),
                (row.get("whatsapp") or "").strip(),
                (row.get("curso") or "").strip(),
                (row.get("perfil") or "").strip(),
                (row.get("semestre") or "").strip(),
            )
            importados += 1
        except Exception:
            erros += 1

    return {"ok": True, "msg": "Importação CSV concluída.", "importados": importados, "ignorados": ignorados, "erros": erros}

@app.post("/api/importar-excel")
async def importar_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .xlsx")

    raw = await file.read()
    result = importar_participantes_xlsx(raw)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("msg", "Planilha inválida."))

    erros_list = result.get("erros", []) or []
    importados = int(result.get("importados", 0) or 0)

    return {
        "ok": True,
        "msg": result.get("msg", "Importação Excel concluída."),
        "importados": importados,
        "ignorados": 0,
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
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nome, email, cpf, whatsapp, curso, perfil, status, bloqueado, semestre, confirmado, data_sorteio, prioridade
        FROM participantes
        ORDER BY id ASC
    """)
    rows = fetchall_dict(cur)
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
    cur = conn.cursor()

    wb = Workbook()
    ws = wb.active
    ws.title = "Resultado"

    def add_section(titulo: str, status: str):
        ws.append([titulo])
        ws.append(["ID", "Nome", "Email", "CPF", "WhatsApp", "Curso", "Perfil", "Semestre", "Data Sorteio", "Prioridade"])
        cur.execute("""
            SELECT id, nome, email, cpf, whatsapp, curso, perfil, semestre, data_sorteio, prioridade
            FROM participantes
            WHERE status=%s
            ORDER BY prioridade ASC, id ASC
        """, (status,))
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

    conn.close()

    nome_arquivo = f"resultado_sorteio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _xlsx_response(wb, nome_arquivo)