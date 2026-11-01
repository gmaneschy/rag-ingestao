# Etapa 4d: limpeza na ingestão e filtros da API

Nasceu da análise das respostas reais (qwen2.5 3b e 7b) sobre sete livros: de 10 trechos
devolvidos, perto de 6 eram lixo (copyright, bibliografia, legendas, título repetido), e as
seções detectadas em dois dos três livros inspecionados estavam erradas.

## O que mudou

### API
| Antes | Agora |
|---|---|
| `secao`, `pagina_de`, `pagina_ate` no corpo de `POST /perguntas` | **removidos**. Campo desconhecido dá 422 (não é ignorado em silêncio) |
| — | `documento_ids: [1, 2]`: consulta só esses documentos (ids de `GET /documentos`) |
| — | `incluir_tipos: ["bibliografia", ...]`: inclui material de apoio na busca |
| resposta | continua trazendo `secao` e `pagina` de cada trecho |
| `GET /documentos/{id}` | ganha `tipos` (contagem por tipo); `amostra` vai até 100 e cada trecho traz `tipo` |

Os filtros de seção e de páginas continuam no serviço (`consulta.servico.perguntar`) para a futura
tela, que os oferece depois de escolher o documento, com listas preenchidas por
`GET /documentos/{id}/secoes`. Página sozinha não identifica nada quando há vários documentos.

### Tipo do trecho
Cada trecho recebe um `tipo`: `corpo`, `notas`, `bibliografia`, `indice` (sumário, índice,
lista de ilustrações), `copyright` ou `fragmento` (título solto, legenda, folha de rosto).
**A busca padrão usa só o `corpo`.** Nada é apagado: `incluir_tipos` traz os outros de volta
(ex.: `{"pergunta": "Qual o ISBN?", "incluir_tipos": ["copyright"]}`).

A classificação combina o título da seção ("Notes", "Bibliography", "Index"), o conteúdo
(ISBN e "Library of Congress", citações do tipo `(Moscow: Nauka, 1991)`, "Ibid.", numeração de
notas) e a página: se a maior parte de uma página é de notas, a página toda é. É heurística e
erra; o tipo aparece em `amostra`, então o erro é visível. Um glossário ou "Sobre o autor" é
`corpo` de propósito (foi a única evidência certa em uma das perguntas dos testes).

### Ingestão
- **Texto**: ligaduras (`ﬁ` → `fi`, o que faz "fin-de-siècle" casar na busca), hífen suave,
  `Russia’ s` → `Russia’s`, espaço antes de vírgula/ponto de OCR.
- **Cabeçalhos e rodapés correntes** (`98 The Case of Alexander Blok`, `Lev Tolstoy ... 43`):
  comparados sem o número de página (só nas pontas da linha), saem do texto quando se repetem em
  4+ páginas. Se o cabeçalho traz número e ocupa até 35% das páginas, a primeira aparição vira o
  início de uma seção (é o título do capítulo). Número de página solto também sai.
- **Seções**: página com mais de 3 "títulos" é sumário/notas/lista e não gera seção; linha com
  ano, aspas, parênteses ou hífen final não é título (nota de rodapé, data); números romanos
  sozinhos (`II`, `XVII`) viram capítulo se o documento tiver 3 ou mais.
- **Duplicados**: na busca, trechos com 80%+ das palavras iguais contam uma vez (a mesma orelha
  em 3 páginas; variações de OCR ainda casam; números diferentes não).

### Resposta
Novo aviso quando um nome próprio da resposta não está no trecho citado mas está em outro
trecho recuperado (fato creditado ao trecho errado). Nome que não está em trecho nenhum é
ignorado (quase sempre é tradução). É alerta: "confira a fonte".

## Como aplicar
```powershell
tar -xzf rag-etapa-4d.tar.gz
uv run alembic upgrade head      # migração 0005: coluna trecho.tipo
uv run pytest
```
Os documentos já ingeridos ficam todos como `corpo` até serem **reprocessados** (isso refaz
texto, seções, tipos e vetores; ~15–20 min para os sete livros):
```powershell
$h = @{ "X-API-Key" = $env:API_KEY }
(Invoke-RestMethod -Uri http://127.0.0.1:8000/documentos -Headers $h).itens | ForEach-Object {
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/documentos/$($_.id)/reprocessar" -Headers $h
}
```
Depois confira: `GET /documentos/5/secoes` (as seções) e `GET /documentos/5?amostra=100` (campo `tipos`
e o `tipo` de cada trecho).

## Limites conhecidos
- Cabeçalho sem número de página é removido mas não vira seção.
- O carimbo "BURLINGAME PUBLIC LIBRARY" ainda pode virar a seção das primeiras páginas do livro
  da Chukovskaya (aparece em poucas páginas). Fica só como seção; o texto sai.
- `pagina` é o índice da página no PDF (238), não o número impresso (221). A diferença (aqui, 17)
  é o que o leitor vê ao conferir a citação no livro.
- Se o corpo do livro nunca é recuperado (perguntas sobre "infância" trouxeram orelha e posfácio),
  limpar não resolve: é problema de recuperação, a medir na etapa 5.
- A verificação de nomes só pega fato trocado entre trechos, não invenção.

## Refinamento 2 (a partir dos dados reais dos documentos 3 e 5)

- Cabeçalhos corridos que sobraram no meio da página ("28 Lev Tolstoy as Early Modernist") agora
  também saem, desde que a linha tenha número na ponta e o mesmo texto de um cabeçalho já detectado.
- Seções: no máximo 2 títulos por página; endereço ("3 Henrietta Street") e frase quebrada
  ("chapter 7).") não são título; início de nota ("36. Charles Bernheimer, ... in") não é título;
  títulos em CAIXA ALTA repetidos em 2 páginas viram carimbo, e os institucionais (LIBRARY,
  UNIVERSITY, PRESS...) nunca são título.
- Classificação: legenda terminada em ")" é `fragmento`; trecho com ≥6 números de página
  ("Blok, Alexander, 12, 45–46, 120") é `indice`.
- Limites: capítulos romanos cujo título divide a linha com outro texto seguem sem detecção; a
  página é a do PDF, não a impressa; OCR sem espaços fica para a etapa 5.
