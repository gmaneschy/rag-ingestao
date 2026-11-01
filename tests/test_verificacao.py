"""Nomes da resposta que não aparecem no trecho citado (os casos vêm das respostas reais)."""

from app.llm.verificacao import nomes_sem_suporte

TRECHO_4 = (
    "but nothing could stave off Stalin's terror once it began in the late 1930s. Most of "
    "Chukovskaya's co-workers and close friends were arrested, and Marshak's children's "
    'literature section was closed down for "bourgeois leanings." Chukovskaya\'s husband, '
    "astrophysicist and science writer Matvei Petrovich Bronshtein, was imprisoned and killed "
    "under torture. Chukovskaya herself narrowly escaped arrest."
)
TRECHO_1 = (
    "1978. The Soviet authorities could not stop Lydia Chukovskaya from writing, but the moment "
    "Kornei Chukovsky died in 1969, they made her a literary pariah. She was expelled from the "
    "Union of Writers."
)
TRECHO_7 = (
    "This circle, which Raeff describes as a study group, was called the “Friendly Literary "
    "Society”; note that the term literary circle, as Russians used it, could have a very broad "
    "meaning. See Marc Raeff, Russian Youth on the Eve of Romanticism: Andrei I. Turgenev."
)
TRECHO_VOLOSHIN = (
    "Maximilian Voloshin hosted writers and painters at his Koktebel dacha every summer, and the "
    "circle around him became the subject of many memoirs."
)


def test_atribuicao_trocada_e_apontada() -> None:
    # ID 44: o trecho fala de Marshak e do marido de Lydia, não de "Korney".
    resposta = (
        "Korney Chukovsky teve sua seção de literatura infantil fechada e seu filho Matvei "
        "Petrovich Bronshtein foi preso e morto sob tortura [1]."
    )
    assert nomes_sem_suporte(resposta, [TRECHO_4, TRECHO_1]) == ["Korney"]


def test_pessoa_do_outro_trecho_e_apontada() -> None:
    # ID 46: "Sociedade Amiga" vinha do trecho sobre o círculo de Raeff, não de Voloshin.
    resposta = "Maximilian Voloshin e seu círculo foram estudados como um grupo de estudo [1]."
    achados = nomes_sem_suporte(resposta, [TRECHO_7, TRECHO_VOLOSHIN])
    assert achados == ["Maximilian", "Voloshin"]


def test_resposta_fiel_nao_gera_alerta() -> None:
    resposta = (
        "Lydia Chukovskaya foi expulsa da União dos Escritores. Depois que Kornei Chukovsky "
        "morreu, ela virou uma pária literária [1]."
    )
    # "União" e "Escritores" são tradução de "Union" e "Writers": o prefixo comparado os acha.
    assert nomes_sem_suporte(resposta, [TRECHO_1]) == []


def test_nome_que_nao_esta_em_trecho_nenhum_e_ignorado() -> None:
    # Provavelmente é tradução; sem um modelo de linguagem não dá para distinguir de invenção.
    assert nomes_sem_suporte("Eles se mudaram para Kiev e Odessa [1].", [TRECHO_4]) == []


def test_grafia_diferente_do_mesmo_nome_nao_dispara() -> None:
    assert nomes_sem_suporte("Foi Korney Chukovsky quem morreu em 1969 [1].", [TRECHO_1]) == []


def test_paragrafo_sem_citacao_nao_e_verificado() -> None:
    assert nomes_sem_suporte("Voloshin e Bronshtein foram amigos.", [TRECHO_4]) == []


def test_citacao_no_fim_vale_para_o_paragrafo_todo() -> None:
    resposta = "Marshak teve a seção fechada. Bronshtein foi preso. Depois Voloshin ajudou. [1]"
    assert nomes_sem_suporte(resposta, [TRECHO_4, TRECHO_VOLOSHIN]) == ["Voloshin"]


def test_so_vale_o_trecho_citado_no_paragrafo() -> None:
    resposta = "O círculo de Voloshin existiu [2]."
    assert nomes_sem_suporte(resposta, [TRECHO_4, TRECHO_VOLOSHIN]) == []
    assert nomes_sem_suporte(resposta, [TRECHO_VOLOSHIN, TRECHO_4]) == ["Voloshin"]


def test_titulos_da_analise_e_a_primeira_palavra_da_frase_sao_ignorados() -> None:
    resposta = (
        "Evidências:\nSim, Marshak teve a seção fechada [1].\nAvaliação:\nLydia foi perseguida."
    )
    assert nomes_sem_suporte(resposta, [TRECHO_4]) == []


def test_siglas_e_nomes_curtos_sao_ignorados() -> None:
    assert nomes_sem_suporte("Ela saiu da URSS e de Ana [1].", [TRECHO_4, "Ana e URSS."]) == []


def test_limite_de_nomes() -> None:
    resposta = "Ana disse que Roberto, Fernanda, Gustavo, Henrique, Isabela e Joaquim saíram [1]."
    outro = "Roberto Fernanda Gustavo Henrique Isabela Joaquim"
    assert len(nomes_sem_suporte(resposta, [TRECHO_4, outro])) == 5
