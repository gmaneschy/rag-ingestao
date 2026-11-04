(() => {
  // Soltar arquivos na zona de envio.
  const zona = document.getElementById("zona");
  const entrada = document.getElementById("arquivos");
  if (zona && entrada) {
    ["dragenter", "dragover"].forEach((e) =>
      zona.addEventListener(e, (ev) => { ev.preventDefault(); zona.classList.add("sobre"); }));
    ["dragleave", "drop"].forEach((e) =>
      zona.addEventListener(e, (ev) => { ev.preventDefault(); zona.classList.remove("sobre"); }));
    zona.addEventListener("drop", (ev) => {
      if (ev.dataTransfer && ev.dataTransfer.files.length) {
        entrada.files = ev.dataTransfer.files;
        entrada.form.requestSubmit();
      }
    });
    entrada.addEventListener("change", () => { if (entrada.files.length) entrada.form.requestSubmit(); });
  }

  // Diálogo de remoção.
  document.addEventListener("click", (ev) => {
    const abrir = ev.target.closest("[data-abrir]");
    if (abrir) document.getElementById(abrir.dataset.abrir).showModal();
    const confirmar = ev.target.closest("[data-confirmar-remocao]");
    if (confirmar) {
      const dialogo = confirmar.closest("dialog");
      const modo = dialogo.querySelector("input[name=consultas]:checked").value;
      confirmar.disabled = true;
      htmx.ajax("DELETE", confirmar.dataset.confirmarRemocao + "?consultas=" + modo,
                { target: "body", swap: "none" });
    }
  });

  // Seleção em lote na lista de documentos. A tabela é recarregada a cada poucos segundos
  // enquanto há documentos processando, então a seleção mora aqui, não nas caixas.
  const lote = document.getElementById("lote");
  if (lote) {
    const marcados = new Set();
    const contagem = document.getElementById("lote-contagem");
    const caixas = () => document.querySelectorAll("#tabela input[name=ids]");
    const atualizar = () => {
      const n = marcados.size;
      lote.hidden = n === 0;
      contagem.textContent = n + (n === 1 ? " selecionado" : " selecionados");
      const todos = document.getElementById("marcar-todos");
      if (todos) {
        const lista = [...caixas()];
        todos.checked = lista.length > 0 && lista.every((c) => c.checked);
      }
    };
    const restaurar = () => {
      caixas().forEach((c) => { c.checked = marcados.has(c.value); });
      atualizar();
    };
    document.addEventListener("change", (ev) => {
      const alvo = ev.target;
      if (alvo.matches && alvo.matches("#tabela input[name=ids]")) {
        alvo.checked ? marcados.add(alvo.value) : marcados.delete(alvo.value);
        atualizar();
      } else if (alvo.id === "marcar-todos") {
        caixas().forEach((c) => {
          c.checked = alvo.checked;
          alvo.checked ? marcados.add(c.value) : marcados.delete(c.value);
        });
        atualizar();
      }
    });
    document.body.addEventListener("htmx:afterSettle", (ev) => {
      if (ev.target.id === "tabela" || ev.target.closest("#tabela")) restaurar();
    });
    // Mover ou reprocessar terminou: a seleção cumpriu o papel.
    document.body.addEventListener("htmx:afterRequest", (ev) => {
      if (ev.detail.elt.closest && ev.detail.elt.closest("#lote") && ev.detail.successful) {
        marcados.clear();
        restaurar();
      }
    });
  }

  // Erro de rede ou do servidor numa ação HTMX.
  document.addEventListener("htmx:responseError", (ev) => {
    const alvo = document.getElementById("resultado-envio") || document.getElementById("conteudo");
    const p = document.createElement("p");
    p.className = "aviso erro";
    p.setAttribute("role", "alert");
    p.textContent = "Algo deu errado (" + ev.detail.xhr.status + "). Tente de novo.";
    alvo.prepend(p);
  });
})();
