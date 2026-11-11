(() => {
  // Barra lateral: ocultar/mostrar, lembrando a escolha. No celular ela vira uma gaveta.
  const raiz = document.documentElement;
  const aplicarBarra = (estado) => {
    raiz.dataset.barra = estado;
    try { localStorage.setItem("rag.barra", estado); } catch (e) { /* navegação privada */ }
    document.querySelectorAll("[data-barra-alternar][aria-controls]").forEach((b) =>
      b.setAttribute("aria-expanded", estado === "aberta"));
  };
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-barra-alternar]"))
      aplicarBarra(raiz.dataset.barra === "aberta" ? "fechada" : "aberta");
    // No celular, escolher um destino fecha a gaveta.
    else if (innerWidth < 800 && ev.target.closest("#barra a")) aplicarBarra("fechada");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && innerWidth < 800 && raiz.dataset.barra === "aberta") aplicarBarra("fechada");
  });
  document.querySelectorAll("[data-barra-alternar][aria-controls]").forEach((b) =>
    b.setAttribute("aria-expanded", raiz.dataset.barra === "aberta"));

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


  // Chat: Enter envia, bloqueio enquanto gera, rolagem, edição e menu das conversas.
  const composer = document.getElementById("composer");
  if (composer) {
    const texto = composer.querySelector("textarea");
    const enviar = document.getElementById("enviar");
    const fio = document.getElementById("fio");
    const gerando = () => !!document.querySelector(".turno[data-estado=gerando]");
    const atualizar = () => { enviar.disabled = gerando(); };
    const perto = () => innerHeight + scrollY >= document.documentElement.scrollHeight - 160;
    const descer = () => scrollTo({ top: document.documentElement.scrollHeight });
    texto.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        if (!enviar.disabled && composer.reportValidity()) composer.requestSubmit();
      }
    });
    document.body.addEventListener("htmx:beforeSwap", (ev) => { ev.detail.stick = perto(); });
    document.body.addEventListener("htmx:afterSwap", (ev) => {
      atualizar();
      if (ev.detail.stick && fio.contains(ev.target)) descer();
    });
    composer.addEventListener("htmx:afterRequest", (ev) => {
      if (ev.target === composer && ev.detail.successful && fio.querySelector(".turno:last-child")) {
        texto.value = "";
        descer();
      }
    });
    atualizar();
    if (document.querySelector(".turno")) descer();
  }
  document.addEventListener("click", (ev) => {
    const ed = ev.target.closest("[data-editar]");
    if (ed) {
      const form = ed.parentElement.querySelector("form.editar");
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector("textarea").focus();
    }
    if (ev.target.closest("[data-cancelar-edicao]")) ev.target.closest("form").hidden = true;
  });
  const dlg = document.getElementById("dialogo-conversa");
  let conversaAberta = null;
  document.addEventListener("click", (ev) => {
    const menu = ev.target.closest("[data-menu-conversa]");
    if (menu && dlg) {
      conversaAberta = menu.dataset.menuConversa;
      document.getElementById("conversa-titulo-dialogo").textContent = menu.dataset.titulo;
      document.getElementById("conversa-novo-titulo").value = menu.dataset.titulo;
      dlg.showModal();
    }
    if (ev.target.closest("[data-conversa-renomear]")) {
      const titulo = document.getElementById("conversa-novo-titulo").value.trim();
      if (titulo) htmx.ajax("POST", "/app/conversas/" + conversaAberta + "/renomear",
                            { target: "body", swap: "none", values: { titulo } });
    }
    if (ev.target.closest("[data-conversa-apagar]") && confirm("Apagar a conversa e as respostas dela?"))
      htmx.ajax("DELETE", "/app/conversas/" + conversaAberta, { target: "body", swap: "none" });
  });


  // Módulos de documentos e parâmetros: abrem em diálogos; fechar grava na conversa.
  const form = document.getElementById("composer");
  if (form) {
    const contar = () => {
      const n = (nome) => form.querySelectorAll(`[name=${nome}]:checked`).length;
      const docs = n("colecao") + n("documento");
      const padrao = form.querySelector("[name=modo]:checked")?.value === "hibrida"
        && form.querySelector("[name=tipo]:checked")?.value === "factual"
        && !form.querySelector("[name=k]").value && !n("incluir");
      const a = form.querySelector("[data-selo=docs]"), b = form.querySelector("[data-selo=params]");
      a.textContent = docs ? `(${docs})` : "";
      b.textContent = padrao ? "" : "•";
    };
    const gravar = () => {
      const m = /^\/app\/conversas\/(\d+)/.exec(location.pathname);
      if (!m) return;
      const dados = new FormData(form);
      dados.delete("pergunta");
      fetch(`/app/conversas/${m[1]}/config`, { method: "POST", body: dados, headers: { "HX-Request": "true" } })
        .catch(() => {});
    };
    form.addEventListener("change", contar);
    document.addEventListener("click", (ev) => {
      const ab = ev.target.closest("[data-abrir-modal]");
      if (ab) document.getElementById(ab.dataset.abrirModal).showModal();
      if (ev.target.closest("[data-fechar-modal]")) ev.target.closest("dialog").close();
      const lp = ev.target.closest("[data-limpar]");
      if (lp) {
        lp.dataset.limpar.split(",").forEach((nome) =>
          form.querySelectorAll(`[name=${nome}]`).forEach((c) => (c.checked = false)));
        contar();
      }
    });
    form.querySelectorAll("dialog.modal").forEach((d) => d.addEventListener("close", gravar));
    contar();
  }


  // Painel de fontes (direita): mostra as fontes da resposta escolhida.
  const painel = document.getElementById("painel-fontes");
  if (painel) {
    const corpo = document.getElementById("painel-fontes-corpo");
    const abrir = (turnoId, destaque) => {
      const dados = document.getElementById("fontes-dados-" + turnoId);
      if (!dados) return;
      corpo.replaceChildren(dados.content.cloneNode(true));
      const turno = document.getElementById("turno-" + turnoId);
      document.getElementById("painel-fontes-pergunta").textContent =
        "— " + (turno?.querySelector(".texto-pergunta")?.textContent || "").slice(0, 60);
      document.querySelectorAll(".turno.selecionado").forEach((t) => t.classList.remove("selecionado"));
      turno?.classList.add("selecionado");
      painel.hidden = false;
      const alvo = destaque && corpo.querySelector("#" + CSS.escape(destaque));
      if (alvo) { alvo.open = true; alvo.scrollIntoView({ block: "center" }); alvo.classList.add("destaque"); }
    };
    document.addEventListener("click", (ev) => {
      const bt = ev.target.closest("[data-fontes]");
      if (bt) abrir(bt.dataset.fontes);
      if (ev.target.closest("[data-fechar-fontes]")) painel.hidden = true;
      const cit = ev.target.closest(".turno .resposta a[href^='#t']");
      if (cit) {
        ev.preventDefault();
        const id = cit.getAttribute("href").slice(1);
        abrir(/^t(\d+)-/.exec(id)[1], id);
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
