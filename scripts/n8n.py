"""Exporta e importa os fluxos do n8n para a pasta n8n/workflows (versionada no git).

Uso:
    python scripts/n8n.py export    # n8n em execução -> n8n/workflows/*.json
    python scripts/n8n.py import    # n8n/workflows/*.json -> n8n em execução

Só usa a biblioteca padrão e chama `docker compose`, então funciona igual no Windows, Mac e Linux.
Credenciais não são exportadas: o JSON guarda só a referência a elas, nunca os segredos.
"""

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA = "n8n/workflows"
TMP_EXPORT = "/tmp/wf-export"
TMP_IMPORT = "/tmp/wf-import"

# Campos que o n8n acrescenta na exportação e que mudariam a cada vez (datas, versões) ou que
# pertencem à instalação (dono e projeto). Sem eles, o git mostra só mudanças reais no fluxo.
CAMPOS_DA_INSTALACAO = (
    "updatedAt",
    "createdAt",
    "versionId",
    "activeVersionId",
    "versionCounter",
    "triggerCount",
    "shared",
    "sourceWorkflowId",
    "versionMetadata",
)


def compose(*args: str, capturar: bool = False) -> str:
    """Roda `docker compose ...` na raiz do projeto; levanta exceção se falhar."""
    resultado = subprocess.run(
        ["docker", "compose", *args],
        cwd=RAIZ,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capturar else None,
    )
    return resultado.stdout or ""


def n8n(*args: str, capturar: bool = False) -> str:
    return compose("exec", "-T", "n8n", *args, capturar=capturar)


def ids_ativos(pasta: Path) -> list[str]:
    """Ids dos fluxos marcados como ativos nos JSON da pasta."""
    ids = []
    for arquivo in sorted(pasta.glob("*.json")):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        if dados.get("active") and dados.get("id"):
            ids.append(str(dados["id"]))
    return ids


def normalizar(fluxo: dict) -> dict:
    """Tira do JSON o que muda a cada exportação ou só vale para aquela instalação."""
    limpo = {k: v for k, v in fluxo.items() if k not in CAMPOS_DA_INSTALACAO}
    if "tags" in limpo:
        limpo["tags"] = [
            {k: v for k, v in tag.items() if k not in ("createdAt", "updatedAt")}
            for tag in limpo["tags"] or []
        ]
    return limpo


def como_texto(fluxo: dict) -> str:
    return json.dumps(fluxo, indent=2, ensure_ascii=False) + "\n"


def exportar() -> int:
    n8n("rm", "-rf", TMP_EXPORT)
    n8n("n8n", "export:workflow", "--all", "--pretty", "--separate", f"--output={TMP_EXPORT}")
    nomes = [n for n in n8n("ls", TMP_EXPORT, capturar=True).split() if n.endswith(".json")]
    destino = RAIZ / PASTA
    destino.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        compose("cp", f"n8n:{TMP_EXPORT}/{nome}", f"{PASTA}/{nome}")
        arquivo = destino / nome
        fluxo = normalizar(json.loads(arquivo.read_text(encoding="utf-8")))
        arquivo.write_text(como_texto(fluxo), encoding="utf-8", newline="\n")
    print(f"{len(nomes)} fluxo(s) exportado(s) para {PASTA}/")
    print("Fluxos apagados no n8n continuam aqui até você remover o arquivo.")
    return 0


def importar() -> int:
    arquivos = sorted((RAIZ / PASTA).glob("*.json"))
    if not arquivos:
        print(f"Nenhum JSON em {PASTA}/", file=sys.stderr)
        return 1

    n8n("rm", "-rf", TMP_IMPORT)
    n8n("mkdir", "-p", TMP_IMPORT)
    for arquivo in arquivos:
        compose("cp", f"{PASTA}/{arquivo.name}", f"n8n:{TMP_IMPORT}/{arquivo.name}")
    # Fluxos com o mesmo id de um que já existe são sobrescritos.
    n8n("n8n", "import:workflow", "--separate", f"--input={TMP_IMPORT}")

    # A importação deixa tudo inativo; publica os que estão marcados como ativos no JSON.
    for id_fluxo in ids_ativos(RAIZ / PASTA):
        try:
            n8n("n8n", "publish:workflow", f"--id={id_fluxo}")
        except subprocess.CalledProcessError:
            # Versões anteriores ao n8n 2.0 usam o comando antigo.
            try:
                n8n("n8n", "update:workflow", f"--id={id_fluxo}", "--active=true")
            except subprocess.CalledProcessError:
                print(f"Não consegui ativar {id_fluxo}; ative pelo editor.", file=sys.stderr)

    # Mudanças feitas pela linha de comando só valem depois de reiniciar o n8n.
    compose("restart", "n8n")
    print(f"{len(arquivos)} fluxo(s) importado(s). O n8n foi reiniciado.")
    print("Aguarde uns 20 segundos: os webhooks só respondem depois que o n8n termina de subir.")
    return 0


def main(argv: list[str]) -> int:
    comandos = {"export": exportar, "import": importar}
    if len(argv) != 2 or argv[1] not in comandos:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return comandos[argv[1]]()
    except subprocess.CalledProcessError as erro:
        print(f"Falhou: {' '.join(map(str, erro.cmd))}", file=sys.stderr)
        print(
            "O n8n está no ar? (docker compose ps; profile n8n no COMPOSE_PROFILES)",
            file=sys.stderr,
        )
        return erro.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
