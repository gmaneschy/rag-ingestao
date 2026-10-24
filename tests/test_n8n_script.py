import json
import subprocess

import pytest

from scripts import n8n


def _fluxo(pasta, nome: str, **campos) -> None:
    (pasta / nome).write_text(json.dumps(campos), encoding="utf-8")


def test_ids_ativos_so_devolve_fluxos_ativos(tmp_path) -> None:
    _fluxo(tmp_path, "a.json", id="A1", active=True)
    _fluxo(tmp_path, "b.json", id="B2", active=False)
    _fluxo(tmp_path, "c.json", id="C3")
    _fluxo(tmp_path, "d.json", active=True)  # sem id: ignorado
    assert n8n.ids_ativos(tmp_path) == ["A1"]


def test_fluxo_de_exemplo_e_json_valido_e_ativo() -> None:
    pasta = n8n.RAIZ / n8n.PASTA
    assert set(n8n.ids_ativos(pasta)) == {"ExemploSaude0001", "IngestaoPasta0001"}


@pytest.fixture
def comandos(monkeypatch):
    """Substitui o docker por um registro dos comandos que seriam executados."""
    chamados: list[list[str]] = []

    def falso(cmd, **kwargs):
        chamados.append(cmd)
        saida = "ExemploSaude0001.json\n" if cmd[-2:] == ["ls", n8n.TMP_EXPORT] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=saida)

    monkeypatch.setattr(n8n.subprocess, "run", falso)
    return chamados


def test_importar_copia_importa_publica_e_reinicia(comandos) -> None:
    assert n8n.importar() == 0
    texto = [" ".join(c) for c in comandos]
    assert any("import:workflow --separate --input=/tmp/wf-import" in t for t in texto)
    assert any("publish:workflow --id=ExemploSaude0001" in t for t in texto)
    assert texto[-1] == "docker compose restart n8n"


def test_importar_cai_para_o_comando_antigo(monkeypatch) -> None:
    chamados: list[str] = []

    def falso(cmd, **kwargs):
        chamados.append(" ".join(cmd))
        if "publish:workflow" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(n8n.subprocess, "run", falso)
    assert n8n.importar() == 0
    assert any("update:workflow --id=ExemploSaude0001 --active=true" in c for c in chamados)


EXEMPLO = n8n.RAIZ / n8n.PASTA / "ExemploSaude0001.json"


def _exportacao_bruta() -> dict:
    """O exemplo como o n8n o exporta: com datas, versões e o dono da instalação."""
    fluxo = json.loads(EXEMPLO.read_text(encoding="utf-8"))
    return {
        "updatedAt": "2026-10-17T16:37:26.948Z",
        "createdAt": "2026-10-17T16:37:20.033Z",
        **fluxo,
        "versionId": "a4c258c0-b59a-4c18-bcda-97663ca54327",
        "activeVersionId": "a4c258c0-b59a-4c18-bcda-97663ca54327",
        "versionCounter": 1,
        "triggerCount": 1,
        "sourceWorkflowId": None,
        "shared": [{"role": "workflow:owner", "projectId": "P1", "project": {"id": "P1"}}],
        "versionMetadata": {"name": None, "description": None},
    }


def test_exemplo_ja_esta_normalizado() -> None:
    texto = EXEMPLO.read_text(encoding="utf-8")
    assert n8n.como_texto(n8n.normalizar(json.loads(texto))) == texto


def test_exportar_copia_e_normaliza(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(n8n, "RAIZ", tmp_path)
    chamados: list[str] = []

    def falso(cmd, **kwargs):
        chamados.append(" ".join(cmd))
        if cmd[2] == "cp":  # docker compose cp n8n:<origem> <destino local>
            (tmp_path / cmd[-1]).write_text(json.dumps(_exportacao_bruta()), encoding="utf-8")
        saida = "ExemploSaude0001.json\n" if cmd[-2:] == ["ls", n8n.TMP_EXPORT] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=saida)

    monkeypatch.setattr(n8n.subprocess, "run", falso)
    assert n8n.exportar() == 0

    assert any("export:workflow --all --pretty --separate" in c for c in chamados)
    exportado = (tmp_path / n8n.PASTA / "ExemploSaude0001.json").read_text(encoding="utf-8")
    # Sem ruído: o resultado é idêntico ao arquivo versionado no template.
    assert exportado == EXEMPLO.read_text(encoding="utf-8")
    for campo in n8n.CAMPOS_DA_INSTALACAO:
        assert f'"{campo}"' not in exportado


def test_normalizar_limpa_datas_das_tags() -> None:
    fluxo = {"id": "X", "tags": [{"id": "T", "name": "n", "createdAt": "a", "updatedAt": "b"}]}
    assert n8n.normalizar(fluxo)["tags"] == [{"id": "T", "name": "n"}]


def test_comando_desconhecido_mostra_ajuda() -> None:
    assert n8n.main(["n8n.py", "apagar"]) == 2
