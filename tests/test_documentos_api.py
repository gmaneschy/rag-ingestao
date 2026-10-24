"""Integração: o endpoint de envio, do HTTP até o banco."""

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient

from app.ingestao.config import get_ingestao_settings
from app.llm.fabrica import criar_embeddings
from app.main import create_app
from app.routers.documentos import obter_embeddings

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
TEXTO = b"O prazo de entrega e de trinta dias uteis.\n\nA garantia cobre defeitos por um ano."


@pytest.fixture
def cliente(banco):  # noqa: F811
    obter_embeddings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    obter_embeddings.cache_clear()


def _enviar(cliente, nome="a.txt", conteudo=TEXTO, **extra):
    return cliente.post(
        "/documentos", files={"arquivo": (nome, conteudo)}, data=extra, headers=CHAVE
    )


def test_exige_chave(cliente) -> None:
    resposta = cliente.post("/documentos", files={"arquivo": ("a.txt", TEXTO)})
    assert resposta.status_code == 401


def test_envio_e_ingestao_em_segundo_plano(cliente) -> None:
    resposta = _enviar(cliente)
    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["situacao"] == "novo"

    detalhe = cliente.get(f"/documentos/{corpo['id']}", headers=CHAVE).json()
    assert detalhe["estado"] == "processado"
    assert detalhe["trechos"] >= 1
    assert detalhe["nome"] == "a.txt" and detalhe["colecao"] == "geral"


def test_reenvio_devolve_200_e_nao_duplica(cliente) -> None:
    primeiro = _enviar(cliente).json()
    de_novo = _enviar(cliente)
    assert de_novo.status_code == 200
    assert de_novo.json() == {"id": primeiro["id"], "situacao": "duplicado", "estado": "processado"}
    assert cliente.get(f"/documentos/{primeiro['id']}", headers=CHAVE).json()["trechos"] == 1


def test_pdf_de_verdade(cliente) -> None:
    resposta = _enviar(cliente, "a.pdf", pdf_com_texto(["prazo de entrega"]))
    assert resposta.status_code == 202
    assert cliente.get(f"/documentos/{resposta.json()['id']}", headers=CHAVE).json()["trechos"] == 1


def test_pdf_sem_texto_fica_com_estado_falhou_e_motivo(cliente) -> None:
    corpo = _enviar(cliente, "a.pdf", pdf_com_texto([""])).json()
    detalhe = cliente.get(f"/documentos/{corpo['id']}", headers=CHAVE).json()
    assert detalhe["estado"] == "falhou" and "OCR" in detalhe["mensagem_erro"]


def test_colecao_e_respeitada(cliente) -> None:
    corpo = _enviar(cliente, colecao="manuais").json()
    assert cliente.get(f"/documentos/{corpo['id']}", headers=CHAVE).json()["colecao"] == "manuais"


@pytest.mark.parametrize(
    ("nome", "conteudo", "extra"),
    [
        ("a.exe", b"MZ", {}),
        ("a.pdf", b"nao e pdf", {}),
        ("vazio.txt", b"", {}),
        ("a.txt", TEXTO, {"colecao": "../etc"}),
    ],
)
def test_entradas_invalidas_dao_422(cliente, nome, conteudo, extra) -> None:
    assert _enviar(cliente, nome, conteudo, **extra).status_code == 422


def test_arquivo_grande_demais_da_413(cliente) -> None:
    grande = b"a" * (get_ingestao_settings().tamanho_max_bytes + 1)
    assert _enviar(cliente, conteudo=grande).status_code == 413


def test_documento_inexistente_da_404(cliente) -> None:
    assert cliente.get("/documentos/999", headers=CHAVE).status_code == 404


def test_o_provedor_padrao_e_o_simulado() -> None:
    assert criar_embeddings().nome_modelo == "simulado-hash-v1"


def test_reprocessar_refaz_os_trechos_sem_duplicar(cliente) -> None:
    corpo = _enviar(cliente).json()
    antes = cliente.get(f"/documentos/{corpo['id']}", headers=CHAVE).json()["trechos"]

    resposta = cliente.post(f"/documentos/{corpo['id']}/reprocessar", headers=CHAVE)
    assert resposta.status_code == 202 and resposta.json()["estado"] == "pendente"

    detalhe = cliente.get(f"/documentos/{corpo['id']}", headers=CHAVE).json()
    assert detalhe["estado"] == "processado" and detalhe["trechos"] == antes


def test_reprocessar_exige_chave_e_documento_existente(cliente) -> None:
    assert cliente.post("/documentos/1/reprocessar").status_code == 401
    assert cliente.post("/documentos/999/reprocessar", headers=CHAVE).status_code == 404
