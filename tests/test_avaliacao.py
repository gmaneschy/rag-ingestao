"""Avaliação: métricas puras, validação do conjunto e execução contra um corpus pequeno."""

import json

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from pydantic import ValidationError

from app.avaliacao import conjunto, metricas, relatorio
from app.avaliacao.__main__ import main
from app.avaliacao.conjunto import Caso, Suporte
from app.avaliacao.executor import avaliar
from app.ingestao import servico
from app.ingestao.config import get_ingestao_settings
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado
from app.llm.tipos import TrechoRecuperado


def _trecho(doc: str, pagina: int | None, texto: str) -> TrechoRecuperado:
    return TrechoRecuperado(id=1, documento=doc, pagina=pagina, texto=texto)


def test_casa_exige_documento_pagina_e_termos() -> None:
    suporte = Suporte(documento="chuk", paginas=(10, 12), contem=["Lídia", "irmã"])
    assert metricas.casa(suporte, _trecho("Chukovskaya.pdf", 11, "A IRMA de lidia era Luiza"))
    assert not metricas.casa(suporte, _trecho("outro.pdf", 11, "irmã de Lídia"))
    assert not metricas.casa(suporte, _trecho("Chukovskaya.pdf", 13, "irmã de Lídia"))
    assert not metricas.casa(suporte, _trecho("Chukovskaya.pdf", None, "irmã de Lídia"))
    assert not metricas.casa(suporte, _trecho("Chukovskaya.pdf", 11, "só Lídia"))


def test_recall_e_mrr() -> None:
    posicoes = [1, 3, None, 12]
    assert metricas.recall_em_k(posicoes, 1) == 0.25
    assert metricas.recall_em_k(posicoes, 5) == 0.5
    assert metricas.mrr(posicoes, 10) == pytest.approx((1 + 1 / 3) / 4)
    assert metricas.recall_em_k([], 5) == 0.0


def test_primeiro_acerto_e_percentil() -> None:
    s = [Suporte(documento="a", contem=["x"])]
    trechos = [_trecho("a.txt", 1, "nada"), _trecho("a.txt", 2, "tem x aqui")]
    assert metricas.primeiro_acerto(s, trechos) == 2
    assert metricas.primeiro_acerto(s, trechos[:1]) is None
    assert metricas.percentil([1, 2, 3, 4, 100], 0.95) == 100
    assert metricas.percentil([], 0.5) is None


def test_conjunto_valida_regras() -> None:
    with pytest.raises(ValidationError):  # com resposta, sem suporte
        Caso(id="a", pergunta="Pergunta?")
    with pytest.raises(ValidationError):  # sem resposta, com suporte
        Caso(
            id="a",
            pergunta="Pergunta?",
            responde=False,
            suporte=[Suporte(documento="d", contem=["x"])],
        )
    with pytest.raises(ValidationError):  # suporte só com o documento é largo demais
        Suporte(documento="d")
    with pytest.raises(ValidationError):
        Suporte(documento="d", paginas=(5, 2))
    caso = Caso(id="a", pergunta="Pergunta?", responde=False)
    with pytest.raises(ValidationError):
        conjunto.Conjunto(casos=[caso, caso])


def test_exemplo_do_repositorio_e_valido() -> None:
    from pathlib import Path

    exemplo = conjunto.carregar(Path(__file__).parent.parent / "avaliacao/perguntas.exemplo.json")
    assert any(not c.responde for c in exemplo.casos)


DOCUMENTOS = {
    "entrega.txt": "O prazo de entrega é de trinta dias úteis após o pagamento.",
    "garantia.txt": "A garantia cobre defeitos de fabricação por doze meses.",
    "bolo.txt": "A receita de bolo de cenoura leva cenouras, farinha e açúcar.",
}
CASOS = [
    Caso(
        id="prazo",
        pergunta="Qual é o prazo de entrega?",
        suporte=[Suporte(documento="entrega", contem=["trinta dias"])],
    ),
    Caso(
        id="garantia",
        pergunta="Quanto tempo dura a garantia?",
        suporte=[Suporte(documento="garantia", contem=["doze meses"])],
    ),
    Caso(
        id="errado",
        pergunta="Qual é o prazo de entrega?",
        suporte=[Suporte(documento="garantia", contem=["trinta dias"])],  # nunca acerta
    ),
    Caso(id="fora", pergunta="Zzzz qqqq xxxx wwww?", responde=False),
]


@pytest.fixture
def corpus(banco):  # noqa: F811
    emb = EmbeddingsSimulado(384)
    for nome, conteudo in DOCUMENTOS.items():
        with banco() as s:
            reg = servico.registrar(
                s,
                nome=nome,
                conteudo=conteudo.encode(),
                colecao="geral",
                config=get_ingestao_settings(),
            )
        servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())
    return banco, emb


@precisa_de_banco
def test_avaliar_mede_recuperacao_e_recusa(corpus) -> None:
    sessoes, emb = corpus
    with sessoes() as session:
        rel = avaliar(session, emb, CASOS, rotulo="t", modo="hibrida", k=5, limiar=0.25)
    por_id = {r.caso.id: r for r in rel.casos}
    assert por_id["prazo"].posicao == 1
    assert por_id["garantia"].posicao == 1
    assert por_id["errado"].posicao is None
    assert por_id["fora"].tem_evidencia is False
    resumo = relatorio.resumir(rel, (1, 5))
    assert resumo["recall@1"] == pytest.approx(2 / 3)
    assert resumo["recusa_correta_portao"] == 1.0
    assert [r.caso.id for r in relatorio.casos_com_falha(rel)] == ["errado"]
    varredura = relatorio.varrer_limiar(rel, (0.0, 1.0))
    assert varredura[0]["recusa_correta"] == 1.0 or varredura[0]["recusa_correta"] == 0.0
    assert "R@1" in relatorio.tabela_recuperacao([resumo])


@precisa_de_banco
def test_avaliar_com_geracao_confere_fonte_citada(corpus) -> None:
    sessoes, emb = corpus
    with sessoes() as session:
        rel = avaliar(
            session,
            emb,
            CASOS,
            rotulo="sim",
            k=5,
            gerador=GeradorDeRespostaSimulado(),
        )
    por_id = {r.caso.id: r for r in rel.casos}
    assert por_id["fora"].resposta["recusou"] is True
    assert por_id["prazo"].resposta["recusou"] is False
    assert por_id["prazo"].resposta["citou_trecho_correto"] is True
    resumo = relatorio.resumir(rel, (1, 5))
    assert resumo["recusa_correta"] == 1.0
    assert 0 <= resumo["fundamentacao_auto"] <= 1
    assert "Fundamentação" in relatorio.tabela_geracao([resumo])


@precisa_de_banco
def test_cli_rodar_grava_json_e_markdown(corpus, tmp_path, capsys) -> None:
    arquivo = tmp_path / "perguntas.json"
    arquivo.write_text(
        json.dumps({"casos": [c.model_dump(mode="json") for c in CASOS]}), encoding="utf-8"
    )
    saida = tmp_path / "res"
    codigo = main(
        ["rodar", "--conjunto", str(arquivo), "--modos", "hibrida,textual", "--ks", "1,5",
         "--saida", str(saida), "--rotulo", "teste"]
    )  # fmt: skip
    assert codigo == 0
    assert len(list(saida.glob("*.json"))) == 1 and len(list(saida.glob("*.md"))) == 1
    dados = json.loads(next(saida.glob("*.json")).read_text(encoding="utf-8"))
    assert set(dados) == {"hibrida", "textual"}
    assert "Recuperação" in capsys.readouterr().out


def test_contem_algum_aceita_qualquer_alternativa() -> None:
    suporte = Suporte(documento="livro", contem_algum=["piano", "violoncelo"])
    assert metricas.casa(suporte, _trecho("livro.pdf", 1, "Ela estudava Violoncelo"))
    assert not metricas.casa(suporte, _trecho("livro.pdf", 1, "Ela estudava pintura"))
    ambos = Suporte(documento="livro", contem=["Lígia"], contem_algum=["piano", "violoncelo"])
    assert not metricas.casa(ambos, _trecho("livro.pdf", 1, "estudava piano"))
    assert metricas.casa(ambos, _trecho("livro.pdf", 1, "Lígia estudava piano"))


def test_cli_explica_conjunto_invalido(tmp_path, capsys) -> None:
    arquivo = tmp_path / "p.json"
    arquivo.write_text(
        json.dumps(
            {
                "casos": [
                    {
                        "id": "a",
                        "pergunta": "Pergunta?",
                        "suporte": [{"documento": "d", "paginas": [3, 1, 2]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["rodar", "--conjunto", str(arquivo)]) == 2
    erro = capsys.readouterr().err
    assert "Conjunto inválido" in erro and "paginas" in erro


def test_documento_casa_ignorando_pontuacao_do_nome() -> None:
    suporte = Suporte(documento="Vidas e Anedotas: Personalidades Literárias", contem=["x"])
    trecho = _trecho("Vidas e Anedotas Personalidades Literárias do Cânone.pdf", 1, "tem x")
    assert metricas.casa(suporte, trecho)


def test_conferencia_aponta_a_causa_de_cada_falha() -> None:
    from app.avaliacao.conferencia import Linha, diagnosticar

    linhas = [
        Linha(_trecho("livro.pdf", 3, "Cervantes perdeu a mão esquerda em Lepanto"), "corpo"),
        Linha(_trecho("livro.pdf", 9, "Notas: Cervantes, 1616"), "notas"),
    ]
    ok, msg = diagnosticar(Suporte(documento="livro", paginas=(3, 3), contem=["Lepanto"]), linhas)
    assert ok and "1 trecho" in msg
    ok, msg = diagnosticar(Suporte(documento="outro", contem=["Lepanto"]), linhas)
    assert not ok and "livro.pdf" in msg  # lista os documentos existentes
    ok, msg = diagnosticar(Suporte(documento="livro", paginas=(50, 60), contem=["x"]), linhas)
    assert not ok and "vai até 9" in msg
    ok, msg = diagnosticar(
        Suporte(documento="livro", paginas=(3, 3), contem=["Cervantes", "1616"]), linhas
    )
    assert not ok and "'1616': [9]" in msg  # os termos existem, mas em outra página
    ok, msg = diagnosticar(Suporte(documento="livro", paginas=(9, 9), contem=["Cervantes"]), linhas)
    assert not ok and "fora do corpo" in msg


@precisa_de_banco
def test_cli_conferir_acusa_suporte_inalcancavel(corpus, tmp_path, capsys) -> None:
    casos = [
        Caso(
            id="bom",
            pergunta="Qual é o prazo de entrega?",
            suporte=[Suporte(documento="entrega", contem=["trinta dias"])],
        ),
        Caso(
            id="ruim",
            pergunta="Qual é o prazo de entrega?",
            suporte=[Suporte(documento="entrega: errado", contem=["trinta dias"])],
        ),
    ]
    arquivo = tmp_path / "p.json"
    arquivo.write_text(
        json.dumps({"casos": [c.model_dump(mode="json") for c in casos]}), encoding="utf-8"
    )
    assert main(["conferir", "--conjunto", str(arquivo)]) == 1
    saida = capsys.readouterr().out
    assert "OK   bom" in saida and "ERRO ruim" in saida and "Existentes" in saida


def test_falhas_lista_recusas_indevidas(tmp_path, capsys) -> None:
    topo = {
        "documento": "d",
        "pagina": 1,
        "similaridade": None,
        "nota_reranker": -0.3,
        "texto": "t",
    }
    caso = {
        "id": "q",
        "pergunta": "Pergunta?",
        "responde": True,
        "posicao": 2,
        "tem_evidencia": False,
        "topo": [topo],
    }
    arquivo = tmp_path / "r.json"
    arquivo.write_text(json.dumps({"hibrida": {"casos": [caso]}}), encoding="utf-8")
    assert main(["falhas", str(arquivo)]) == 0
    saida = capsys.readouterr().out
    assert "RECUSADA POR ENGANO" in saida and "-0.3" in saida
