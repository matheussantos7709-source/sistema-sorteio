from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from io import BytesIO
import io
import csv

from openpyxl import load_workbook, Workbook

try:
    from backend.database import criar_tabela, conectar, registrar_vencedor, caminho_db
    from backend.services import (
        cadastrar_participante,
        confirmar_presenca_por_email,
        promover_suplente_se_expirou,
        listar_historico,
        limpar_historico,
    )
except Exception:
    from database import criar_tabela, conectar, registrar_vencedor, caminho_db
    from services import (
        cadastrar_participante,
        confirmar_presenca_por_email,
        promover_suplente_se_expirou,
        listar_historico,
        limpar_historico,
    )

app = FastAPI(title="Sistema de Sorteio API", version="1.0.0")

criar_tabela()
print("DB em uso:", caminho_db())

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://127.0.0.1:5173","http://localhost:5173","https://sistema-sorteio-1.onrender.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

def row_to_dict(row):
    # se seu conectar() usa row_factory=sqlite3.Row, isso funciona também:
    try:
        return {
            "id": row["id"],
            "nome": row["nome"],
            "email": row["email"],
            "cpf": row["cpf"],
            "whatsapp": row["whatsapp"],
            "curso": row["curso"],
            "perfil": row["perfil"],
            "status": row["status"],
            "bloqueado": row["bloqueado"],
            "semestre": row["semestre"],
            "confirmado": row["confirmado"],
            "data_sorteio": row["data_sorteio"],
            "prioridade": row["prioridade"],
        }
    except Exception:
        return {
            "id": row[0],
            "nome": row[1],
            "email": row[2],
            "cpf": row[3],
            "whatsapp": row[4],
            "curso": row[5],
            "perfil": row[6],
            "status": row[7],
            "bloqueado": row[8],
            "semestre": row[9],
            "confirmado": row[10],
            "data_sorteio": row[11],
            "prioridade": row[12],
        }

# ---------------------------
# ROTAS BÁSICAS
# ---------------------------

@app.get("/")
def home():
    return {"ok": True, "msg": "API do Sistema de Sorteio no ar. Veja /docs"}

@app.get("/api/dbinfo")
def dbinfo():
    return {"db_path": caminho_db()}

@app.get("/api/participantes")
def listar_participantes():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nome, email, cpf, whatsapp, curso, perfil, status, bloqueado, semestre, confirmado, data_sorteio, prioridade
        FROM participantes
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]

@app.post("/api/participantes")
def criar_ou_atualizar_participante(p: ParticipanteIn):
    if not (p.nome or "").strip():
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")

    cadastrar_participante(
        (p.nome or "").strip(),
        (p.email or "").strip(),
        (p.cpf or "").strip(),
        (p.whatsapp or "").strip(),
        (p.curso or "").strip(),
        (p.perfil or "").strip(),
        (p.semestre or "").strip(),
    )
    return {"ok": True, "msg": "Participante salvo com sucesso."}

@app.delete("/api/participantes")
def deletar_todos_participantes(
    apagar_historico: bool = Query(False),
    reset_ids: bool = Query(True),
):
    conn = conectar()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM participantes")
    total_part = cur.fetchone()[0]

    total_hist = 0
    if apagar_historico:
        cur.execute("SELECT COUNT(*) FROM historico_sorteios")
        total_hist = cur.fetchone()[0]
        cur.execute("DELETE FROM historico_sorteios")
        if reset_ids:
            cur.execute("DELETE FROM sqlite_sequence WHERE name='historico_sorteios'")

    cur.execute("DELETE FROM participantes")
    if reset_ids:
        cur.execute("DELETE FROM sqlite_sequence WHERE name='participantes'")

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
    cur.execute("DELETE FROM participantes WHERE id=?", (pid,))
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
          AND bloqueado=0
    """)
    participantes = cur.fetchall()

    if len(participantes) < (vagas + suplentes_qtd):
        conn.close()
        raise HTTPException(status_code=400, detail="Participantes insuficientes.")

    import random
    random.shuffle(participantes)

    vencedores = participantes[:vagas]
    suplentes = participantes[vagas:vagas + suplentes_qtd]
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for idx, p in enumerate(vencedores, start=1):
        pid = p[0] if not hasattr(p, "keys") else p["id"]
        nome = p[1] if not hasattr(p, "keys") else p["nome"]
        email = p[2] if not hasattr(p, "keys") else p["email"]

        registrar_vencedor(pid, nome, email, conn=conn)
        cur.execute("""
            UPDATE participantes
            SET status='SELECIONADO',
                bloqueado=1,
                confirmado=0,
                data_sorteio=?,
                prioridade=?
            WHERE id=?
        """, (agora, idx, pid))

    for idx, s in enumerate(suplentes, start=vagas + 1):
        pid = s[0] if not hasattr(s, "keys") else s["id"]
        cur.execute("""
            UPDATE participantes
            SET status='SUPLENTE',
                confirmado=0,
                data_sorteio=?,
                prioridade=?
            WHERE id=?
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
    try:
        prazo = int(payload.prazo_horas)
        if prazo < 0:
            raise HTTPException(status_code=400, detail="prazo_horas deve ser >= 0.")

        ok, msg = promover_suplente_se_expirou(prazo)
        return {"ok": ok, "msg": msg}

    except HTTPException:
        raise
    except Exception as e:
        # devolve o erro real pro Swagger/front (facilita MUITO)
        raise HTTPException(status_code=500, detail=f"Erro ao promover suplente: {type(e).__name__}: {e}")

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

# ============================================================
# ✅ IMPORTAR / EXPORTAR (o que estava “bloqueado” no Vite)
# ============================================================

@app.post("/api/importar-excel")
async def importar_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .xlsx")

    conteudo = await file.read()
    wb = load_workbook(filename=BytesIO(conteudo))
    ws = wb.active

    importados = 0
    ignorados = 0

    # Espera colunas na ordem: nome, email, cpf, whatsapp, curso, perfil, semestre
    for linha in ws.iter_rows(min_row=2, values_only=True):
        try:
            nome, email, cpf, whatsapp, curso, perfil, semestre = linha
            cadastrar_participante(
                str(nome or "").strip(),
                str(email or "").strip(),
                str(cpf or "").strip(),
                str(whatsapp or "").strip(),
                str(curso or "").strip(),
                str(perfil or "").strip(),
                str(semestre or "").strip(),
            )
            importados += 1
        except Exception:
            ignorados += 1

    return {"ok": True, "importados": importados, "ignorados": ignorados}

def _xlsx_response(wb: Workbook, filename: str) -> StreamingResponse:
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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
            WHERE status=?
            ORDER BY prioridade ASC, id ASC
        """, (status,))
        for r in cur.fetchall():
            try:
                ws.append([r["id"], r["nome"], r["email"], r["cpf"], r["whatsapp"], r["curso"], r["perfil"], r["semestre"], r["data_sorteio"], r["prioridade"]])
            except Exception:
                ws.append(list(r))
        ws.append([])

    add_section("CONFIRMADOS", "CONFIRMADO")
    add_section("SELECIONADOS (aguardando confirmação)", "SELECIONADO")
    add_section("SUPLENTES", "SUPLENTE")
    add_section("INSCRITOS", "INSCRITO")

    conn.close()

    nome_arquivo = f"resultado_sorteio_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _xlsx_response(wb, nome_arquivo)

@app.get("/api/exportar-participantes")
def exportar_participantes():
    conn = conectar()
    cur = conn.cursor()

    wb = Workbook()
    ws = wb.active
    ws.title = "Participantes"
    ws.append(["ID","Nome","Email","CPF","WhatsApp","Curso","Perfil","Status","Bloqueado","Semestre","Confirmado","Data Sorteio","Prioridade"])

    cur.execute("""
        SELECT id, nome, email, cpf, whatsapp, curso, perfil, status, bloqueado, semestre, confirmado, data_sorteio, prioridade
        FROM participantes
        ORDER BY id ASC
    """)
    for r in cur.fetchall():
        try:
            ws.append([r["id"], r["nome"], r["email"], r["cpf"], r["whatsapp"], r["curso"], r["perfil"], r["status"], r["bloqueado"], r["semestre"], r["confirmado"], r["data_sorteio"], r["prioridade"]])
        except Exception:
            ws.append(list(r))

    conn.close()

    nome_arquivo = f"participantes_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _xlsx_response(wb, nome_arquivo)

def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")

def _to_str(v):
    if v is None:
        return ""
    return str(v).strip()

def _map_csv_row(row: dict):
    """
    Aceita CSV com cabeçalhos diferentes.
    Tenta mapear para: nome,email,cpf,whatsapp,curso,perfil,semestre
    """
    # normaliza chaves
    norm = {_norm_key(k): _to_str(v) for k, v in (row or {}).items()}

    def pick(*keys):
        for k in keys:
            kk = _norm_key(k)
            if kk in norm and norm[kk] != "":
                return norm[kk]
        return ""

    nome = pick("nome", "name", "aluno", "participante")
    email = pick("email", "e-mail", "mail")
    cpf = pick("cpf", "documento", "doc", "cpf_cnpj", "cpfcnpj")
    whatsapp = pick("whatsapp", "telefone", "celular", "fone", "phone")
    curso = pick("curso", "course")
    perfil = pick("perfil", "tipo", "categoria")
    semestre = pick("semestre", "periodo", "período", "turma")

    return nome, email, cpf, whatsapp, curso, perfil, semestre


@app.post("/api/importar-csv")
async def importar_csv(file: UploadFile = File(...)):
    """
    CSV (com cabeçalho). Aceita vários nomes de colunas.
    Ex esperado: nome,email,cpf,whatsapp,curso,perfil,semestre
    """
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
            nome, email, cpf, whatsapp, curso, perfil, semestre = _map_csv_row(row)
            if not nome.strip():
                ignorados += 1
                continue
            cadastrar_participante(nome, email, cpf, whatsapp, curso, perfil, semestre)
            importados += 1
        except Exception:
            erros += 1

    return {"ok": True, "msg": "Importação CSV concluída.", "importados": importados, "ignorados": ignorados, "erros": erros}


@app.post("/api/importar-excel")
async def importar_excel(file: UploadFile = File(...)):
    """
    XLSX: espera colunas na ordem:
    nome, email, cpf, whatsapp, curso, perfil, semestre
    (igual no seu Tkinter)
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .xlsx")

    raw = await file.read()
    wb = load_workbook(filename=io.BytesIO(raw), data_only=True)
    ws = wb.active

    importados = 0
    ignorados = 0
    erros = 0

    # começa na linha 2 (linha 1 = cabeçalho)
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            nome = _to_str(row[0] if len(row) > 0 else "")
            email = _to_str(row[1] if len(row) > 1 else "")
            cpf = _to_str(row[2] if len(row) > 2 else "")
            whatsapp = _to_str(row[3] if len(row) > 3 else "")
            curso = _to_str(row[4] if len(row) > 4 else "")
            perfil = _to_str(row[5] if len(row) > 5 else "")
            semestre = _to_str(row[6] if len(row) > 6 else "")

            if not nome.strip():
                ignorados += 1
                continue

            cadastrar_participante(nome, email, cpf, whatsapp, curso, perfil, semestre)
            importados += 1
        except Exception:
            erros += 1

    return {"ok": True, "msg": "Importação Excel concluída.", "importados": importados, "ignorados": ignorados, "erros": erros}


@app.get("/api/exportar-participantes")
def exportar_participantes():
    """
    Gera um XLSX com TODOS os participantes.
    """
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

    ws.append(["ID", "Nome", "Email", "CPF", "WhatsApp", "Curso", "Perfil", "Semestre", "Status", "Confirmado", "Data Sorteio", "Prioridade"])

    for r in rows:
        # sqlite.Row funciona como dict e tuple ao mesmo tempo
        ws.append([
            r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]
        ])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    headers = {"Content-Disposition": 'attachment; filename="participantes.xlsx"'}
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/api/exportar-resultados")
def exportar_resultados():
    """
    Gera um XLSX com SELECIONADOS, SUPLENTES, CONFIRMADOS e INSCRITOS.
    """
    conn = conectar()
    cur = conn.cursor()

    def fetch(q):
        cur.execute(q)
        return cur.fetchall()

    selecionados = fetch("SELECT nome, email FROM participantes WHERE status='SELECIONADO' ORDER BY prioridade ASC")
    suplentes = fetch("SELECT nome, email FROM participantes WHERE status='SUPLENTE' ORDER BY prioridade ASC")
    confirmados = fetch("SELECT nome, email FROM participantes WHERE status='CONFIRMADO' ORDER BY id ASC")
    inscritos = fetch("SELECT nome, email FROM participantes WHERE status='INSCRITO' ORDER BY id ASC")

    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Resultado"

    ws.append(["SELECIONADOS"])
    ws.append(["Nome", "E-mail"])
    for n, e in selecionados:
        ws.append([n, e])

    ws.append([])
    ws.append(["SUPLENTES"])
    ws.append(["Nome", "E-mail"])
    for n, e in suplentes:
        ws.append([n, e])

    ws.append([])
    ws.append(["CONFIRMADOS"])
    ws.append(["Nome", "E-mail"])
    for n, e in confirmados:
        ws.append([n, e])

    ws.append([])
    ws.append(["INSCRITOS"])
    ws.append(["Nome", "E-mail"])
    for n, e in inscritos:
        ws.append([n, e])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    headers = {"Content-Disposition": 'attachment; filename="resultado_sorteio.xlsx"'}
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)