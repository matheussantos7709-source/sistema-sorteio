from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from pathlib import Path
from io import BytesIO
import io
import csv
import random

from openpyxl import load_workbook, Workbook

from backend.database import criar_tabela, conectar, registrar_vencedor, caminho_db
from backend.services import (
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- MODELOS ----------------

class ParticipanteIn(BaseModel):
    nome: str
    email: Optional[str] = None
    cpf: Optional[str] = None
    whatsapp: Optional[str] = None
    curso: Optional[str] = None
    perfil: Optional[str] = None
    semestre: Optional[str] = None


# ---------------- PARTICIPANTES ----------------

@app.get("/api/participantes")
def listar_participantes():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT * FROM participantes ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/participantes")
def criar_participante(p: ParticipanteIn):
    cadastrar_participante(
        p.nome.strip(),
        (p.email or "").strip(),
        (p.cpf or "").strip(),
        (p.whatsapp or "").strip(),
        (p.curso or "").strip(),
        (p.perfil or "").strip(),
        (p.semestre or "").strip(),
    )
    return {"ok": True}


# ---------------- IMPORTAÇÃO ----------------

@app.post("/api/importar-csv")
async def importar_csv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Envie um CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))

    importados = 0
    ignorados = 0

    for row in reader:
        try:
            nome = row.get("nome", "").strip()
            if not nome:
                ignorados += 1
                continue

            cadastrar_participante(
                nome,
                row.get("email", "").strip(),
                row.get("cpf", "").strip(),
                row.get("whatsapp", "").strip(),
                row.get("curso", "").strip(),
                row.get("perfil", "").strip(),
                row.get("semestre", "").strip(),
            )
            importados += 1
        except:
            ignorados += 1

    return {"ok": True, "importados": importados, "ignorados": ignorados}


@app.post("/api/importar-excel")
async def importar_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Envie um XLSX")

    raw = await file.read()
    wb = load_workbook(filename=io.BytesIO(raw))
    ws = wb.active

    importados = 0
    ignorados = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            nome = str(row[0] or "").strip()
            if not nome:
                ignorados += 1
                continue

            cadastrar_participante(
                nome,
                str(row[1] or "").strip(),
                str(row[2] or "").strip(),
                str(row[3] or "").strip(),
                str(row[4] or "").strip(),
                str(row[5] or "").strip(),
                str(row[6] or "").strip(),
            )
            importados += 1
        except:
            ignorados += 1

    return {"ok": True, "importados": importados, "ignorados": ignorados}


# ---------------- EXPORTAÇÃO ----------------

@app.get("/api/exportar-participantes")
def exportar_participantes():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT * FROM participantes ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.append(rows[0].keys())

    for r in rows:
        ws.append(list(r))

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="participantes.xlsx"'},
    )