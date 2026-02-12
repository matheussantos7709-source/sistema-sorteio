// frontend/src/api.js
const BASE = (import.meta.env.VITE_API_BASE || "https://sistema-sorteio.onrender.com").replace(/\/+$/, "");

async function parseError(res) {
  let detail = "";
  try {
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const j = await res.json();
      detail = j?.detail ? JSON.stringify(j.detail) : JSON.stringify(j);
    } else {
      detail = await res.text();
    }
  } catch {}
  return `${res.status} - ${detail || res.statusText}`;
}

async function jsonReq(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

async function fileReq(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.blob();
}

async function uploadReq(path, file) {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export const api = {
  // IMPORTAÇÃO
  importarExcel: (file) => uploadReq("/api/importar-excel", file),
  importarCsv: (file) => uploadReq("/api/importar-csv", file),

  // PARTICIPANTES
  listarParticipantes: () => jsonReq("/api/participantes"),
  salvarParticipante: (data) =>
    jsonReq("/api/participantes", { method: "POST", body: JSON.stringify(data) }),
  deletarParticipante: (id) =>
    jsonReq(`/api/participantes/${id}`, { method: "DELETE" }),
  deletarTodosParticipantes: () =>
    jsonReq("/api/participantes", { method: "DELETE" }),

  // SORTEIO
  sortear: (data) =>
    jsonReq("/api/sortear", { method: "POST", body: JSON.stringify(data) }),
  confirmar: (data) =>
    jsonReq("/api/confirmar", { method: "POST", body: JSON.stringify(data) }),
  promover: (data) =>
    jsonReq("/api/promover", { method: "POST", body: JSON.stringify(data) }),

  // HISTÓRICO
  historico: () => jsonReq("/api/historico"),
  apagarHistorico: (resetId) =>
    jsonReq(`/api/historico?reset_id=${resetId}`, { method: "DELETE" }),

  // EXPORTAÇÃO
  exportarParticipantes: () => fileReq("/api/exportar-participantes"),
  exportarResultados: () => fileReq("/api/exportar-resultados"),

  // (Opcional) expor BASE pra debug
  __BASE__: BASE,
};