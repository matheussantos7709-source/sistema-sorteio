// frontend/src/App.jsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";
import { api } from "./api";

function statusBadge(status) {
  const s = (status || "").toUpperCase();
  let cls = "outro";
  if (s === "INSCRITO") cls = "inscrito";
  else if (s === "SELECIONADO") cls = "selecionado";
  else if (s === "CONFIRMADO") cls = "confirmado";
  else if (s === "SUPLENTE") cls = "suplente";
  else if (s === "EXPIRADO") cls = "outro";
  return <span className={`badge ${cls}`}>{s || "-"}</span>;
}

function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modalOverlay" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modalHeader">
          <div>{title}</div>
          <button onClick={onClose}>Fechar</button>
        </div>
        <div className="modalBody">{children}</div>
      </div>
    </div>
  );
}

// --- helper de download ---
async function downloadBlob(blob, filenameFallback) {
  const fileUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = fileUrl;
  a.download = filenameFallback;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(fileUrl);
}

export default function App() {
  const [tab, setTab] = useState("cadastro"); // cadastro | importacao | sorteio

  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null); // {type:'ok'|'err', msg:string}

  const [participantes, setParticipantes] = useState([]);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const lastClickedIndexRef = useRef(null);

  // filtros
  const [filtroStatus, setFiltroStatus] = useState("TODOS");
  const [busca, setBusca] = useState("");

  // modal edit
  const [editOpen, setEditOpen] = useState(false);
  const [editData, setEditData] = useState(null);

  // histórico
  const [histOpen, setHistOpen] = useState(false);
  const [historico, setHistorico] = useState([]);
  const [resetId, setResetId] = useState(true);

  // forms
  const [cad, setCad] = useState({
    nome: "",
    email: "",
    cpf: "",
    whatsapp: "",
    curso: "",
    perfil: "",
    semestre: "",
  });

  const [sorteio, setSorteio] = useState({ vagas: "1", suplentes: "0" });
  const [confirmEmail, setConfirmEmail] = useState("");
  const [prazoHoras, setPrazoHoras] = useState("48");

  // import/export UI
  const fileXlsxRef = useRef(null);
  const fileCsvRef = useRef(null);

  function showOk(msg) {
    setToast({ type: "ok", msg });
    setTimeout(() => setToast(null), 3500);
  }
  function showErr(err) {
    setToast({ type: "err", msg: String(err?.message || err) });
    setTimeout(() => setToast(null), 6000);
  }

  async function carregarParticipantes() {
    setLoading(true);
    try {
      const data = await api.listarParticipantes();
      setParticipantes(Array.isArray(data) ? data : []);
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    carregarParticipantes();
  }, []);

  const participantesFiltrados = useMemo(() => {
    const s = filtroStatus.toUpperCase();
    const q = busca.trim().toLowerCase();

    return participantes
      .filter((p) => (s === "TODOS" ? true : (p.status || "").toUpperCase() === s))
      .filter((p) => {
        if (!q) return true;
        const blob = `${p.nome || ""} ${p.email || ""}`.toLowerCase();
        return blob.includes(q);
      });
  }, [participantes, filtroStatus, busca]);

  // seleção com Ctrl/Shift
  function handleRowClick(e, pid) {
    const idsArr = participantesFiltrados.map((p) => p.id);
    const idx = idsArr.indexOf(pid);

    const isCtrl = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;

    setSelectedIds((prev) => {
      const next = new Set(prev);

      if (isShift && lastClickedIndexRef.current != null) {
        const a = Math.min(lastClickedIndexRef.current, idx);
        const b = Math.max(lastClickedIndexRef.current, idx);
        for (let i = a; i <= b; i++) next.add(idsArr[i]);
        return next;
      }

      if (isCtrl) {
        if (next.has(pid)) next.delete(pid);
        else next.add(pid);
      } else {
        next.clear();
        next.add(pid);
      }

      lastClickedIndexRef.current = idx;
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
    lastClickedIndexRef.current = null;
  }

  const selectedList = useMemo(() => {
    const set = selectedIds;
    return participantes.filter((p) => set.has(p.id));
  }, [participantes, selectedIds]);

  // -------- ações PARTICIPANTES --------
  async function onSalvarCadastro() {
    if (!cad.nome.trim()) return showErr("Nome é obrigatório.");
    setLoading(true);
    try {
      await api.salvarParticipante(cad);
      showOk("Participante salvo!");
      setCad({ nome: "", email: "", cpf: "", whatsapp: "", curso: "", perfil: "", semestre: "" });
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  function abrirEditar() {
    if (selectedList.length !== 1) return showErr("Selecione exatamente 1 participante para editar.");
    setEditData({ ...selectedList[0] });
    setEditOpen(true);
  }

  async function salvarEdicao() {
    if (!editData?.nome?.trim()) return showErr("Nome é obrigatório.");
    setLoading(true);
    try {
      await api.salvarParticipante({
        nome: editData.nome,
        email: editData.email || "",
        cpf: editData.cpf || "",
        whatsapp: editData.whatsapp || "",
        curso: editData.curso || "",
        perfil: editData.perfil || "",
        semestre: editData.semestre || "",
      });
      showOk("Participante atualizado!");
      setEditOpen(false);
      setEditData(null);
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function excluirSelecionados() {
    if (selectedIds.size === 0) return showErr("Selecione ao menos 1 participante.");
    const ok = confirm(`Excluir ${selectedIds.size} participante(s)? Isso não pode ser desfeito.`);
    if (!ok) return;

    setLoading(true);
    try {
      for (const id of selectedIds) {
        await api.deletarParticipante(id);
      }
      showOk("Removido(s) com sucesso!");
      clearSelection();
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function excluirTodos() {
    const ok = confirm("Excluir TODOS os participantes? Isso não pode ser desfeito.");
    if (!ok) return;

    setLoading(true);
    try {
      await api.deletarTodosParticipantes();
      showOk("Todos removidos.");
      clearSelection();
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  // -------- ações SORTEIO --------
  async function fazerSorteio() {
    const vagas = Number(sorteio.vagas);
    const supl = Number(sorteio.suplentes);

    if (!Number.isInteger(vagas) || vagas <= 0) return showErr("Vagas deve ser um número > 0.");
    if (!Number.isInteger(supl) || supl < 0) return showErr("Suplentes deve ser um número >= 0.");

    setLoading(true);
    try {
      const r = await api.sortear({ vagas, suplentes: supl });
      showOk(r?.msg || "Sorteio realizado!");
      clearSelection();
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function confirmar() {
    const email = confirmEmail.trim();
    if (!email) return showErr("Digite o e-mail do selecionado.");
    setLoading(true);
    try {
      const r = await api.confirmar({ email });
      if (r.ok) showOk("Confirmado com sucesso!");
      else showErr(r.msg || "Não encontrado.");
      setConfirmEmail("");
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function promover() {
    const prazo = Number(prazoHoras);
    if (!Number.isInteger(prazo) || prazo < 0) return showErr("Prazo deve ser um número >= 0.");

    setLoading(true);
    try {
      const r = await api.promover({ prazo_horas: prazo });
      showOk(r.msg || (r.ok ? "Suplente promovido!" : "Nada para promover."));
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  // -------- HISTÓRICO --------
  async function abrirHistorico() {
    setLoading(true);
    try {
      const data = await api.historico();
      setHistorico(Array.isArray(data) ? data : []);
      setHistOpen(true);
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function apagarHistorico() {
    const ok = confirm("Apagar TODO o histórico? Isso não pode ser desfeito.");
    if (!ok) return;

    setLoading(true);
    try {
      const r = await api.apagarHistorico(resetId);
      showOk(`Histórico apagado. Removidos: ${r.apagados}`);
      setHistorico([]);
      setHistOpen(false);
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  // -------- IMPORTAÇÃO / EXPORTAÇÃO --------
  async function onImportarXlsx(file) {
    if (!file) return;
    setLoading(true);
    try {
      const r = await api.importarExcel(file);
      showOk(`Importação concluída! Importados: ${r.importados ?? 0} | Ignorados: ${r.ignorados ?? 0}`);
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
      if (fileXlsxRef.current) fileXlsxRef.current.value = "";
    }
  }

  async function onImportarCsv(file) {
    if (!file) return;
    setLoading(true);
    try {
      const r = await api.importarCsv(file);
      showOk(`Importação CSV ok! Importados: ${r.importados ?? 0} | Ignorados: ${r.ignorados ?? 0}`);
      await carregarParticipantes();
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
      if (fileCsvRef.current) fileCsvRef.current.value = "";
    }
  }

  async function onExportarResultados() {
    setLoading(true);
    try {
      const blob = await api.exportarResultados();
      await downloadBlob(blob, "resultado_sorteio.xlsx");
      showOk("Download gerado!");
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  async function onExportarParticipantes() {
    setLoading(true);
    try {
      const blob = await api.exportarParticipantes();
      await downloadBlob(blob, "participantes.xlsx");
      showOk("Download gerado!");
    } catch (e) {
      showErr(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">Sistema de Sorteio</div>
          <div className="sub">
            Participantes (tempo real) — <b>{participantes.length}</b> no total
          </div>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={carregarParticipantes} className="primary" disabled={loading}>
            {loading ? "Carregando..." : "Atualizar"}
          </button>
          <button onClick={abrirHistorico} disabled={loading}>Ver Histórico</button>
        </div>
      </div>

      <div className="shell">
        {/* LEFT */}
        <div className="card">
          <div className="tabs">
            <button className={`tab ${tab === "cadastro" ? "active" : ""}`} onClick={() => setTab("cadastro")}>
              Cadastro
            </button>
            <button className={`tab ${tab === "importacao" ? "active" : ""}`} onClick={() => setTab("importacao")}>
              Importação/Exportação
            </button>
            <button className={`tab ${tab === "sorteio" ? "active" : ""}`} onClick={() => setTab("sorteio")}>
              Sorteio
            </button>
          </div>

          <div className="cardBody">
            {tab === "cadastro" && (
              <>
                <div className="cardHeader" style={{ padding: 0, marginBottom: 10 }}>Dados do participante</div>

                <div className="fieldGrid">
                  <div className="field">
                    <label>Nome *</label>
                    <input value={cad.nome} onChange={(e) => setCad((s) => ({ ...s, nome: e.target.value }))} placeholder="Ex: Maria Silva" />
                  </div>
                  <div className="field">
                    <label>E-mail</label>
                    <input value={cad.email} onChange={(e) => setCad((s) => ({ ...s, email: e.target.value }))} placeholder="maria@email.com" />
                  </div>
                  <div className="field">
                    <label>CPF</label>
                    <input value={cad.cpf} onChange={(e) => setCad((s) => ({ ...s, cpf: e.target.value }))} placeholder="Somente números" />
                  </div>
                  <div className="field">
                    <label>WhatsApp</label>
                    <input value={cad.whatsapp} onChange={(e) => setCad((s) => ({ ...s, whatsapp: e.target.value }))} placeholder="DDD + número" />
                  </div>
                  <div className="field">
                    <label>Curso</label>
                    <input value={cad.curso} onChange={(e) => setCad((s) => ({ ...s, curso: e.target.value }))} placeholder="Ex: História" />
                  </div>
                  <div className="field">
                    <label>Perfil</label>
                    <input value={cad.perfil} onChange={(e) => setCad((s) => ({ ...s, perfil: e.target.value }))} placeholder="Ex: Público" />
                  </div>
                  <div className="field">
                    <label>Semestre</label>
                    <input value={cad.semestre} onChange={(e) => setCad((s) => ({ ...s, semestre: e.target.value }))} placeholder="Ex: 2026-1" />
                  </div>
                </div>

                <div className="actionsRow">
                  <button className="primary" onClick={onSalvarCadastro} disabled={loading}>
                    Cadastrar / Atualizar
                  </button>
                </div>

                <div className="small" style={{ marginTop: 10 }}>
                  Dica: o cadastro atualiza se já existir mesmo e-mail ou CPF.
                </div>
              </>
            )}

            {tab === "importacao" && (
              <>
                <div className="cardHeader" style={{ padding: 0, marginBottom: 10 }}>Importação / Exportação</div>

                <div className="small">
                  Importar envia o arquivo pro backend e grava no SQLite. Exportar baixa um .xlsx gerado pela API.
                </div>

                <div style={{ height: 10 }} />

                <div className="field">
                  <label>Importar Excel (.xlsx)</label>
                  <input
                    ref={fileXlsxRef}
                    type="file"
                    accept=".xlsx"
                    onChange={(e) => onImportarXlsx(e.target.files?.[0])}
                    disabled={loading}
                  />
                </div>

                <div className="field" style={{ marginTop: 10 }}>
                  <label>Importar CSV (.csv)</label>
                  <input
                    ref={fileCsvRef}
                    type="file"
                    accept=".csv"
                    onChange={(e) => onImportarCsv(e.target.files?.[0])}
                    disabled={loading}
                  />
                </div>

                <div style={{ height: 12 }} />

                <div className="actionsRow">
                  <button className="primary" onClick={onExportarResultados} disabled={loading}>
                    Exportar resultados para Excel
                  </button>
                  <button onClick={onExportarParticipantes} disabled={loading}>
                    Exportar participantes
                  </button>
                </div>
              </>
            )}

            {tab === "sorteio" && (
              <>
                <div className="cardHeader" style={{ padding: 0, marginBottom: 10 }}>Configurar sorteio</div>

                <div className="fieldGrid">
                  <div className="field">
                    <label>Vagas principais</label>
                    <input value={sorteio.vagas} onChange={(e) => setSorteio((s) => ({ ...s, vagas: e.target.value }))} />
                  </div>
                  <div className="field">
                    <label>Vagas suplentes</label>
                    <input value={sorteio.suplentes} onChange={(e) => setSorteio((s) => ({ ...s, suplentes: e.target.value }))} />
                  </div>
                </div>

                <div className="actionsRow">
                  <button className="primary" onClick={fazerSorteio} disabled={loading}>
                    Realizar Sorteio
                  </button>
                </div>

                <div style={{ height: 12 }} />

                <div className="cardHeader" style={{ padding: 0, marginBottom: 10 }}>Confirmação e promoção</div>

                <div className="field">
                  <label>Confirmar presença (E-mail do selecionado)</label>
                  <input value={confirmEmail} onChange={(e) => setConfirmEmail(e.target.value)} placeholder="email do SELECIONADO" />
                </div>

                <div className="field" style={{ marginTop: 10 }}>
                  <label>Prazo para promover suplente (horas)</label>
                  <input value={prazoHoras} onChange={(e) => setPrazoHoras(e.target.value)} />
                </div>

                <div className="actionsRow">
                  <button onClick={confirmar} className="primary" disabled={loading}>Confirmar Presença</button>
                  <button onClick={promover} disabled={loading}>Promover Suplente ({prazoHoras || 0}h)</button>
                </div>

                <div className="actionsRow">
                  <button className="danger" onClick={apagarHistorico} disabled={loading}>
                    Apagar Histórico
                  </button>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#cbd5e1" }}>
                    <input
                      type="checkbox"
                      checked={resetId}
                      onChange={(e) => setResetId(e.target.checked)}
                      style={{ width: 16, height: 16 }}
                    />
                    Resetar ID (opcional)
                  </label>
                </div>
              </>
            )}

            {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
          </div>
        </div>

        {/* RIGHT */}
        <div className="card">
          <div className="cardHeader">Participantes (tempo real)</div>
          <div className="cardBody">
            <div className="fieldGrid" style={{ gridTemplateColumns: "180px 1fr" }}>
              <div className="field">
                <label>Status</label>
                <select value={filtroStatus} onChange={(e) => { setFiltroStatus(e.target.value); clearSelection(); }}>
                  <option value="TODOS">TODOS</option>
                  <option value="INSCRITO">INSCRITO</option>
                  <option value="SELECIONADO">SELECIONADO</option>
                  <option value="CONFIRMADO">CONFIRMADO</option>
                  <option value="SUPLENTE">SUPLENTE</option>
                  <option value="EXPIRADO">EXPIRADO</option>
                </select>
              </div>
              <div className="field">
                <label>Buscar</label>
                <input value={busca} onChange={(e) => { setBusca(e.target.value); clearSelection(); }} placeholder="Buscar por nome ou e-mail..." />
              </div>
            </div>

            <div className="actionsRow">
              <button onClick={abrirEditar} disabled={selectedList.length !== 1}>Editar</button>
              <button className="danger" onClick={excluirSelecionados} disabled={selectedIds.size === 0}>
                Excluir selecionado(s)
              </button>
              <button className="danger" onClick={excluirTodos} disabled={participantes.length === 0}>
                Excluir todos
              </button>
              <button onClick={clearSelection} disabled={selectedIds.size === 0}>Limpar seleção</button>
            </div>

            <div className="small" style={{ marginTop: 8 }}>
              Seleção: clique (1), Ctrl+clique (múltiplos), Shift+clique (intervalo).
            </div>

            <div style={{ height: 10 }} />

            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 150 }}>Status</th>
                    <th>Nome</th>
                    <th>E-mail</th>
                    <th style={{ width: 90 }}>ID</th>
                  </tr>
                </thead>
                <tbody>
                  {participantesFiltrados.map((p) => {
                    const selected = selectedIds.has(p.id);
                    return (
                      <tr
                        key={p.id}
                        className={`row ${selected ? "selected" : ""}`}
                        onClick={(e) => handleRowClick(e, p.id)}
                        title="Clique para selecionar. Ctrl/Shift para múltiplos."
                      >
                        <td>{statusBadge(p.status)}</td>
                        <td>{p.nome || "-"}</td>
                        <td>{p.email || "-"}</td>
                        <td>{p.id}</td>
                      </tr>
                    );
                  })}
                  {participantesFiltrados.length === 0 && (
                    <tr>
                      <td colSpan={4} style={{ color: "#9ca3af" }}>
                        Nenhum participante encontrado.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

          </div>
        </div>
      </div>

      {/* MODAL EDIT */}
      {editOpen && editData && (
        <Modal title="Editar participante" onClose={() => { setEditOpen(false); setEditData(null); }}>
          <div className="fieldGrid">
            <div className="field">
              <label>Nome *</label>
              <input value={editData.nome || ""} onChange={(e) => setEditData((s) => ({ ...s, nome: e.target.value }))} />
            </div>
            <div className="field">
              <label>E-mail</label>
              <input value={editData.email || ""} onChange={(e) => setEditData((s) => ({ ...s, email: e.target.value }))} />
            </div>
            <div className="field">
              <label>CPF</label>
              <input value={editData.cpf || ""} onChange={(e) => setEditData((s) => ({ ...s, cpf: e.target.value }))} />
            </div>
            <div className="field">
              <label>WhatsApp</label>
              <input value={editData.whatsapp || ""} onChange={(e) => setEditData((s) => ({ ...s, whatsapp: e.target.value }))} />
            </div>
            <div className="field">
              <label>Curso</label>
              <input value={editData.curso || ""} onChange={(e) => setEditData((s) => ({ ...s, curso: e.target.value }))} />
            </div>
            <div className="field">
              <label>Perfil</label>
              <input value={editData.perfil || ""} onChange={(e) => setEditData((s) => ({ ...s, perfil: e.target.value }))} />
            </div>
            <div className="field">
              <label>Semestre</label>
              <input value={editData.semestre || ""} onChange={(e) => setEditData((s) => ({ ...s, semestre: e.target.value }))} />
            </div>
          </div>

          <div className="actionsRow">
            <button className="primary" onClick={salvarEdicao} disabled={loading}>Salvar</button>
            <button onClick={() => { setEditOpen(false); setEditData(null); }}>Cancelar</button>
          </div>
        </Modal>
      )}

      {/* MODAL HISTÓRICO */}
      {histOpen && (
        <Modal title="Histórico de Sorteios" onClose={() => setHistOpen(false)}>
          <div className="actionsRow" style={{ justifyContent: "space-between" }}>
            <div className="small">
              Registros: <b>{historico.length}</b>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#cbd5e1" }}>
                <input
                  type="checkbox"
                  checked={resetId}
                  onChange={(e) => setResetId(e.target.checked)}
                  style={{ width: 16, height: 16 }}
                />
                Resetar ID
              </label>
              <button className="danger" onClick={apagarHistorico} disabled={loading}>Apagar histórico</button>
            </div>
          </div>

          <div style={{ height: 10 }} />

          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 180 }}>Data/Hora</th>
                  <th>Nome</th>
                  <th>E-mail</th>
                  <th style={{ width: 160 }}>Status atual</th>
                </tr>
              </thead>
              <tbody>
                {historico.map((h, idx) => (
                  <tr key={idx}>
                    <td>{h.data_sorteio || "-"}</td>
                    <td>{h.nome || "-"}</td>
                    <td>{h.email || "-"}</td>
                    <td>{h.status_atual || "-"}</td>
                  </tr>
                ))}
                {historico.length === 0 && (
                  <tr>
                    <td colSpan={4} style={{ color: "#9ca3af" }}>Sem registros.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Modal>
      )}
    </div>
  );
}