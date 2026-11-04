"""Etapa 4d de ponta a ponta: tipo do trecho, filtros da API, duplicados e aviso de nomes."""

# ruff: noqa: F811

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.consulta import busca
from app.consulta.busca import Achado
from app.llm.tipos import Resposta, TrechoRecuperado
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
LIVRO = (
    "# Livro\n\nA garantia do produto cobre defeitos de fabricação por um ano inteiro.\n\n"
    "## Bibliography\n\nBerkowitz, D. Garantia e Social Structures (Cambridge: Cambridge "
    "University Press, 1988). Ibid., 12. Garantia, 1990 (Moscow: Nauka, 1991), pp. 5-9."
).encode()


class GeradorFalso:
    nome_modelo = "falso"

    def __init__(self, resposta: Resposta) -> None:
        self.resposta = resposta

    def responder(self, pergunta, trechos, *, tipo="factual"):
        return self.resposta


@pytest.fixture
def cliente(banco):
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        c.app_ref = app
        yield c
    app.dependency_overrides.clear()
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _enviar(cliente, nome, conteudo, colecao="geral") -> int:
    r = cliente.post(
        "/documentos", files={"arquivo": (nome, conteudo)}, data={"colecao": colecao}, headers=CHAVE
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _perguntar(cliente, pergunta="Qual é a garantia?", **extra):
    r = cliente.post("/perguntas", json={"pergunta": pergunta, "k": 10, **extra}, headers=CHAVE)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_bibliografia_e_guardada_com_o_tipo_e_fica_fora_da_busca(cliente) -> None:
    doc = _enviar(cliente, "livro.md", LIVRO)
    detalhe = cliente.get(f"/documentos/{doc}?amostra=5", headers=CHAVE).json()
    assert detalhe["tipos"] == {"corpo": 1, "bibliografia": 1}
    assert {t["tipo"] for t in detalhe["amostra"]} == {"corpo", "bibliografia"}

    r = _perguntar(cliente)
    assert [t["secao"] for t in r["trechos_usados"]] == ["Livro"]


def test_incluir_tipos_traz_o_material_de_apoio_de_volta(cliente) -> None:
    _enviar(cliente, "livro.md", LIVRO)
    r = _perguntar(cliente, incluir_tipos=["bibliografia"])
    assert {t["secao"] for t in r["trechos_usados"]} == {"Livro", "Livro > Bibliography"}


def test_tipo_de_trecho_invalido_e_recusado(cliente) -> None:
    r = cliente.post(
        "/perguntas", json={"pergunta": "qualquer", "incluir_tipos": ["lixo"]}, headers=CHAVE
    )
    assert r.status_code == 422
    # "corpo" não precisa (nem pode) ser pedido: ele sempre entra.
    r = cliente.post(
        "/perguntas", json={"pergunta": "qualquer", "incluir_tipos": ["corpo"]}, headers=CHAVE
    )
    assert r.status_code == 422


def test_filtro_por_documento(cliente) -> None:
    a = _enviar(cliente, "a.md", b"# A\n\nA garantia cobre defeitos de fabricacao do produto A.")
    b = _enviar(cliente, "b.md", b"# B\n\nA garantia cobre defeitos de transporte do produto B.")
    todos = _perguntar(cliente)
    assert {t["documento_id"] for t in todos["trechos_usados"]} == {a, b}
    so_b = _perguntar(cliente, documento_ids=[b])
    assert {t["documento_id"] for t in so_b["trechos_usados"]} == {b}


def test_trechos_quase_identicos_contam_uma_vez(cliente) -> None:
    # A mesma orelha em dois livros (ou em duas páginas), com uma variação de OCR.
    base = "Lydia Chukovskaya nasceu em 1907 em Sao Petersburgo e escreveu sobre a infancia"
    _enviar(cliente, "a.md", f"{base} e a juventude.".encode())
    _enviar(cliente, "b.md", f"{base} e a juventude , com cuidado.".encode())
    _enviar(cliente, "c.md", b"A garantia e outro assunto, bem diferente, sem relacao com isso.")
    r = _perguntar(cliente, "Quando Lydia Chukovskaya nasceu?")
    assert len([t for t in r["trechos_usados"] if "Lydia" in t["texto"]]) == 1


def test_deduplicar_mantem_o_de_melhor_posicao_e_diferencia_numeros() -> None:
    def achado(i, texto):
        return Achado(TrechoRecuperado(i, "d.pdf", 1, texto), 1, i, None, None, None, 1.0 / i)

    a = achado(1, "Born in 1907 in St. Petersburg, Lydia has observed most of the major events.")
    b = achado(2, "Born in 1907 in St. Petersburg, Lydia has observed in most of the major events.")
    c = achado(3, "Item 1 custa dez reais e vence em marco de cada ano fiscal.")
    d = achado(4, "Item 2 custa vinte reais e vence em junho de cada ano fiscal.")
    assert [x.trecho.id for x in busca.deduplicar([a, b, c, d])] == [1, 3, 4]


def test_aviso_de_nome_que_veio_de_outro_trecho(cliente) -> None:
    _enviar(
        cliente,
        "a.md",
        b"# A\n\nMarshak teve a secao de literatura infantil fechada.\n\n"
        b"# B\n\nKornei Chukovsky escreveu para criancas.",
    )
    resposta = "Kornei Chukovsky teve a secao fechada [1]."
    # O trecho [1] é qualquer um dos dois; o aviso só dispara se o nome estiver no outro.
    cliente.app_ref.dependency_overrides[obter_gerador] = lambda: GeradorFalso(
        Resposta(resposta, (1,))
    )
    r = _perguntar(cliente, "Quem teve a secao fechada? Kornei Marshak")
    primeiro = r["trechos_usados"][0]["texto"]
    if "Kornei" in primeiro:
        assert r["aviso"] is None
    else:
        assert "Kornei" in r["aviso"] and "confira a fonte" in r["aviso"]


def test_reprocessar_reclassifica_os_trechos_antigos(cliente, banco) -> None:
    doc = _enviar(cliente, "livro.md", LIVRO)
    with banco() as s:
        s.execute(text("UPDATE trecho SET tipo = 'corpo'"))  # como ficam os já ingeridos
        s.commit()
    assert cliente.post(f"/documentos/{doc}/reprocessar", headers=CHAVE).status_code == 202
    detalhe = cliente.get(f"/documentos/{doc}", headers=CHAVE).json()
    assert detalhe["tipos"] == {"corpo": 1, "bibliografia": 1}


def test_a_coluna_tipo_so_aceita_os_valores_conhecidos(banco) -> None:
    from sqlalchemy.exc import IntegrityError

    with banco() as s:
        s.execute(
            text(
                "INSERT INTO documento (nome, hash_conteudo, colecao, caminho_arquivo) "
                "VALUES ('a', repeat('a', 64), 'c', '')"
            )
        )
        with pytest.raises(IntegrityError):
            s.execute(
                text(
                    "INSERT INTO trecho (documento_id, posicao, texto, tipo, modelo_embedding, "
                    "dimensao) "
                    "SELECT id, 0, 'x', 'lixo', 'm', 768 FROM documento"
                )
            )
