# Etapa 4c — Modo análise

`POST /perguntas` com `"tipo": "analise"` deixa o modelo interpretar e opinar, **separando o que
está nos documentos do que é opinião dele**. O padrão continua `"tipo": "factual"`, que responde
só com o que os trechos dizem.

**Antes de rodar:** `uv run alembic upgrade head` (migração 0004: `consulta.tipo`).

## O que muda na resposta

O modelo é instruído a escrever duas seções, com estes títulos:

```
Evidências:
Lígia recusou o convite [1]. Luiza é irmã dela [2].

Avaliação:
Parece reservada, mas só vejo dois trechos; é cedo para concluir.
```

- **Evidências:** só o que está nos trechos, cada afirmação com a citação `[n]`.
- **Avaliação:** interpretação e opinião; deve dizer quando passa do que os trechos mostram.

A API devolve, além de `resposta` (o texto inteiro), `tipo` e o campo `analise`:

| Campo | Significado |
| --- | --- |
| `analise.evidencias`, `analise.avaliacao` | as duas partes, já separadas |
| `analise.separou` | `false` se o modelo não escreveu a Avaliação; aí o texto inteiro vem em `evidencias` |
| `analise.base_trechos`, `analise.base_paginas` | em quanto do documento a análise se apoia: **só os trechos recuperados**, não o documento inteiro |

`fontes` e `trechos_usados` funcionam como no tipo factual (e trazem `secao`). Citações de
`[n]` valem nas duas partes e só os números de trechos que existem são aceitos.

## Avisos de transparência (`aviso`)

| Situação | Aviso |
| --- | --- |
| Normal | "A Avaliação é interpretação do modelo, não afirmação dos documentos: só as Evidências são sustentadas pelos trechos citados." |
| O modelo não separou as partes | "O modelo não separou Evidências de Avaliação; trate todo o texto como interpretação." |
| Evidências sem citação | "As Evidências não citaram nenhum trecho; trate a análise com cautela." + o aviso normal |

Sem evidência na busca, a análise recusa como o factual (`recusou: true`, modelo nem chamado).

## Parâmetros

- `CONSULTA_K_ANALISE` (padrão 8): trechos enviados na análise. O `k` do corpo da pergunta
  vale nos dois tipos.
- `LLM_MAX_TOKENS_ANALISE` (padrão 900, limitado por `MAX_TOKENS_POR_PERGUNTA`): a análise é mais
  longa. A janela de contexto pedida ao Ollama acompanha esse valor.
- Os filtros `colecoes`, `documento_ids` e `incluir_tipos` valem na análise (`secao` e as páginas saíram da API na 4d).
- No prompt da análise, cada trecho leva também o atributo `secao`.

## Limites conhecidos

- **A análise vê só os trechos recuperados.** "O que você acha de Lígia?" é respondida com cerca
  de 8 trechos, não com o livro. `base_trechos` e `base_paginas` mostram isso; resumos por seção
  são o caminho para ir além.
- **O modelo pode não seguir o formato** (um 7B em CPU às vezes ignora os títulos). Por isso
  existe `separou: false`.
- Opinião não tem resposta esperada: a avaliação da etapa 5 mede só o tipo factual (a coluna
  `consulta.tipo` permite separar). Na análise, o que dá para verificar é se as Evidências citam e
  se o trecho citado sustenta a afirmação.
- Como em qualquer resposta, remover um documento não apaga paráfrases dele na `resposta`
  (veja a etapa 4a).
