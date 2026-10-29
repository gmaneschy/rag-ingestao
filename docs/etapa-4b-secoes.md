# Etapa 4b — Seções

Cada trecho passa a saber a que seção do documento pertence. Serve para filtrar a busca ("só a
seção Garantia"), para a tela de debug e, depois, para o modo análise.

**Antes de rodar:** `uv run alembic upgrade head` (migração 0003: `trecho.secao` e
`documento.aviso`). Documentos já ingeridos só ganham seção depois de
`POST /documentos/{id}/reprocessar`.

## Como a seção é detectada

Genérica, sem supor "capítulo". Duas fontes, por ordem de confiança:

1. **Índice (bookmarks) do PDF**, se tiver pelo menos duas entradas. Respeita a hierarquia.
2. **Linhas que parecem título**, no texto:

| Padrão | Exemplos | Nível |
| --- | --- | --- |
| Markdown | `# Guia`, `## Instalação` | número de `#` |
| Palavra + número | `Capítulo 5`, `CAPÍTULO V`, `Capítulo dois — O retorno`, `Chapter 3` | Parte/Livro/Anexo 1, Capítulo 2, Seção 3 |
| Palavra sozinha | `Prólogo`, `Epílogo`, `Introdução`, `Resumo`, `Referências` | 1 |
| Numeração decimal | `1.2 Requisitos`, `2 Metodologia` | número de segmentos |
| Caixa alta curta | `INTRODUÇÃO GERAL` | 1 |

Um título de nível *n* encerra os abertos de nível *n* ou maior. O trecho guarda o caminho
completo: `Parte I > Capítulo 2`. A seção atravessa páginas até aparecer outro título.

**Um trecho nunca mistura duas seções** (como já não mistura páginas). O título entra no texto do
primeiro trecho da seção, então a busca textual por "Capítulo 5" funciona.

**Cabeçalho corrido não é seção:** um título idêntico em 3 ou mais páginas é descartado (caixa
alta, decimal e Markdown por completo; `Capítulo N` fica só na primeira página).

**É heurística e erra.** Por isso `GET /documentos/{id}/secoes` e a amostra mostram o que foi
detectado. Um documento sem estrutura fica com `secao` vazia, o que é normal. Títulos de várias
linhas não são reconhecidos.

## Endpoints novos

| Rota | O que faz |
| --- | --- |
| `GET /documentos/{id}/secoes` | `{id, secoes: [{secao, trechos, pagina_inicial, pagina_final}], trechos_sem_secao}`, na ordem do documento |
| `GET /documentos/{id}` | ganha `secoes` (quantas), `aviso` e, na amostra, `secao` |
| `POST /perguntas` | ganha `secao` (o nome da seção **contém** o texto, sem diferenciar maiúsculas), `pagina_de` e `pagina_ate` (inclusivos). `fontes` e `trechos_usados` trazem `secao` |

## Avisos

- **Documento:** `documento.aviso` diz o que ficou de fora sem falhar, como
  "12 de 200 páginas sem texto extraível (escaneadas?); o conteúdo delas não foi indexado".
  Aparece em `GET /documentos` e `GET /documentos/{id}`.
- **Pergunta:** se a busca não achou nada, `aviso` explica quando o motivo é o filtro:
  "Coleção sem documentos: x." ou "Nenhum trecho em seção que contenha 'y'."
  (Resolve o caso de uma coleção digitada errada parecer "sem evidência".)
