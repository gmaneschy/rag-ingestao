"""Classificação de trechos, com textos reais dos livros usados nos testes de validação.

Os casos vêm de trechos que a busca devolveu (ou das amostras dos documentos) e que poluíam as
respostas: copyright, notas, bibliografia, legendas, cabeçalhos. Os de "corpo" são o contrário:
trechos que a classificação NÃO pode tirar da busca.
"""

import pytest

from app.ingestao.classificacao import classificar, classificar_texto
from app.ingestao.divisao import TrechoNovo

COPYRIGHT_ROMANOV = (
    "“Published in … Canada by Random House of Canada Limited, Toronto”—T.p. verso. “This is a "
    "Borzoi book”—T.p. verso. eISBN: 978-0-307-59552-2 1. Romanov, House of—History. 2. Romanov, "
    "House of—Art patronage. 3. Russia—Kings and rulers—Biography. 4. Authors, Russian—Biography. "
    "5. Russian literature—History and criticism. 6. Artists—Russia—Biography. I. Title."
)
COPYRIGHT_CHUCHVAHA = (
    "-- (Library of the written word ; volume 44) (Industrial world ; volume 5) Includes "
    "bibliographical references. ISBN 978-90-04-26926-2 (hardback : alk. paper) -- ISBN "
    "978-90-04-30140-5 (e-book) 1. Mir iskusstva. 2. Zolotoe runo. 3. Apollon (Saint Petersburg, "
    "Russia) 4. Illustrated periodicals--Russia--History--19th century."
)
ILUSTRACOES = (
    "ffiustrations Mayakovsky drawing of Lydia Chukovskaya, 1915 Frontispiece Following Page 82 "
    "Kornei Chukovsky with Boba, Lydia, and Kolya on the seashore, 1912 or 1913 (?) Lydia, Ilya "
    "Repin, Kornei Chukovsky, Maria Borisovna, Natalia Borisovna Nordman-Severova (Repin's wife), "
    "and Kolya The Chukovsky dacha at Kuokkala Lydia with Kornei Chukovsky, 1911"
)
NOTAS_CHUKOVSKAYA = (
    "4. Chukovsky, Voprosy Literatury 1 (1972): 156\n\n5. Olga Dyachkova, Chukokkala, 260. "
    "6. Chukovsky, Voprosy Literatury 1, 169-70. 7. Chukovsky, Voprosy Literatury 1, 158. "
    "8. Chukovsky, Voprosy Literatury 1, 162."
)
NOTAS_MATICH = (
    "Psikhodiakhronologika: Psikhoistoriia russkoj literatury ot romantizma do nashikh dnei "
    "(Moscow: Novoe literaturnoe obozrenie, 1994); Judith Ryan, The Vanishing Subject (Chicago: "
    "University of Chicago Press, 1991), 5. 3. Ibid., 12."
)
NOTAS_MATICH_2 = (
    "Heinz Frank [Garden City , N.J.: Doubleday , 1966], 46). Also cf. Olga Matich, “The "
    "Merezhkovskys’ Third Testament and the Russian Utopian Tradition,” pp. 120-31."
)
BIBLIOGRAFIA_WALKER = (
    "Literature and Bureaucratism. New York: Octagon, 1972 [1934]. Engel, Barbara.\n\n"
    "Mothers and Daughters: Women of the Intelligentsia in Nineteenth Century Russia. Cambridge: "
    "Cambridge University Press, 1983. Engelstein, Laura. The Keys to Happiness: Sex and the "
    "Search for Modernity in Fin-de-Siècle Russia. Ithaca, N.Y.: Cornell University Press, 1992."
)
BIBLIOGRAFIA_RUSSA = (
    "ежегодник за 1978 год. М., 1979. С. 71). 2 См.: К. С . Объединения лит. / / ЛЭ. М., 1934. "
    "Т. 8. С. 212— 217; Брудный Д. Л. Лит. кружки / / КЛЭ. М., 1967. Т. 4. С. 306-309. 3 См.: "
    "Leighton L. G . Circles, literary: 1800— 1860 / / The M odern Encyclopedia of Russian and "
    "Soviet Literatures / Ed. by H. B. Weber. Gulf Breeze, 1981. Vol. 4. P . 181-187."
)

CORPO = [
    # A única evidência certa de "Akhmatova fez parte de algum círculo?": glossário, é conteúdo.
    "Biographical Glossary Akhmatova, Anna Andreyevna (1889-1966): Major twentieth-century "
    "Russian poet. A member of the Acmeist group, she attained early fame, but was criticized as "
    "apohtical after the revolution. No new work of hers was published between 1922 and 1940, "
    "and in 1946 after a vitriolic propaganda campaign she was expelled from the Soviet Union "
    "of Writers.",
    "Born in 1907 in St. Petersburg, Lydia Korneyevna Chukovskaya has observed or participated "
    "in most of the major literary events of Russia's tumultuous twentieth century. Her father "
    "was Kornei Ivanovich Chukovsky (1882-1969), the celebrated critic, editor, scholar.",
    "to Max Nordau, self-castration is one of the “stigmata” of psychopathology .\n\nWhat strikes "
    "today’ s reader of Nordau’ s Degeneration (1892) is the author’ s obliteration of the "
    "difference between the epoch’ s fear of castration and physical castration.",
    '"Tell me, Kolechka," he\'d say in a gentle but slightly threatening voice, "what\'s '
    "'straw' in English? Hmm. That's right. . . And Lidauchek, what about you?\"",
    "In more traditional historical terms, Russia’ s Solov’evians imagined themselves as the "
    "apocalyptic vanguard of a messianic project that offered an answer to the nagging issue of "
    "Russia’ s backwardness. They replaced progress with prophecy, making transﬁguration the goal.",
]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (COPYRIGHT_ROMANOV, "copyright"),
        (COPYRIGHT_CHUCHVAHA, "copyright"),
        (ILUSTRACOES, "indice"),
        (NOTAS_CHUKOVSKAYA, "notas"),
        (NOTAS_MATICH, "notas"),
        (NOTAS_MATICH_2, "bibliografia"),
        (BIBLIOGRAFIA_WALKER, "bibliografia"),
        (BIBLIOGRAFIA_RUSSA, "bibliografia"),
    ],
)
def test_material_de_apoio_sai_do_corpo(texto, esperado) -> None:
    obtido = classificar_texto(texto)
    if esperado in {"notas", "bibliografia"}:
        # Para a busca tanto faz qual dos dois: ambos ficam fora do corpo.
        assert obtido in {"notas", "bibliografia"}
    else:
        assert obtido == esperado


def test_lista_numerada_de_passos_nao_e_nota() -> None:
    passos = (
        "1. Baixe o instalador. 2. Abra o arquivo baixado. 3. Clique em Avançar. "
        "4. Escolha a pasta de destino. 5. Clique em Concluir e reinicie o computador."
    )
    assert classificar_texto(passos) == "corpo"


@pytest.mark.parametrize("texto", CORPO)
def test_conteudo_de_verdade_continua_corpo(texto) -> None:
    assert classificar_texto(texto) == "corpo"


@pytest.mark.parametrize(
    "texto",
    [
        "Erotic Utopia",
        "Kornei Chukovsky at his desk",
        "MAXIMILIAN VOLOSHIN AND THE RUSSIAN LITERARY CIRCLE",
        "60 The Meaning of The Meaning of Love",
    ],
)
def test_titulo_solto_e_legenda_sao_fragmentos(texto) -> None:
    assert classificar_texto(texto) == "fragmento"


def test_frase_curta_completa_e_conteudo() -> None:
    assert classificar_texto("O prazo de entrega é de trinta dias úteis.") == "corpo"


def test_o_titulo_da_secao_decide() -> None:
    texto = "Berkowitz, Social Structures: a network approach, discussed at length by many."
    assert classificar_texto(texto, "Notes") == "notas"
    assert classificar_texto(texto, "Parte II > Bibliography") == "bibliografia"
    assert classificar_texto(texto, "Index") == "indice"
    assert classificar_texto(texto, "Capítulo 3") == "corpo"


def _trecho(pos: int, pagina: int, texto: str, secao: str | None = None) -> TrechoNovo:
    return TrechoNovo(posicao=pos, pagina=pagina, texto=texto, secao=secao)


def test_pagina_de_notas_e_toda_de_notas() -> None:
    # O segundo trecho da página é uma citação longa, sem nenhum sinal próprio de nota.
    citacao = (
        "And when He perceived the sighs of the creature, He lovingly gave himself up to the "
        "torment in the place of His bride, and He joined them in a single flesh of suffering."
    )
    trechos = [
        _trecho(0, 311, "Corpo do capítulo, com texto corrido suficiente para ser conteúdo."),
        _trecho(1, 312, NOTAS_MATICH),
        _trecho(2, 312, citacao),
        _trecho(3, 312, NOTAS_MATICH_2),
    ]
    tipos = [t.tipo for t in classificar(trechos)]
    assert tipos[0] == "corpo"
    assert set(tipos[1:]) <= {"notas", "bibliografia"}  # inclusive a citação sem sinal nenhum


def test_pagina_isolada_entre_duas_de_notas_tambem_e() -> None:
    texto_neutro = "Comentário do autor sobre o ponto discutido acima, sem nenhuma referência."
    trechos = [
        _trecho(0, 10, NOTAS_MATICH),
        _trecho(1, 11, texto_neutro),
        _trecho(2, 12, NOTAS_MATICH),
    ]
    assert [t.tipo for t in classificar(trechos)] == ["notas", "notas", "notas"]


def test_documento_sem_paginas_e_sem_notas_fica_corpo() -> None:
    trechos = [TrechoNovo(posicao=i, pagina=None, texto=t) for i, t in enumerate(CORPO)]
    assert {t.tipo for t in classificar(trechos)} == {"corpo"}


def test_legenda_com_parenteses_e_fragmento() -> None:
    assert (
        classificar_texto("Kornei Chukovsky with Kolya, Boba, and Lydia, 1913 (?)") == "fragmento"
    )


def test_indice_remissivo() -> None:
    texto = (
        "Blok, Alexander, 12, 45–46, 120; Bely, Andrei, 7, 33, 98; Rozanov, Vasilii, 251, 260–62, "
        "280; Tolstoy, Lev, 28, 43, 50"
    )
    assert classificar_texto(texto) == "indice"
