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
