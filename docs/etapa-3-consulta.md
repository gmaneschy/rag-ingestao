# Etapa 3 — Consulta

`POST /perguntas` responde com texto, fontes (documento e página) e os trechos usados, ou recusa
quando não há evidência.

## Fluxo

```
pergunta ─► limite diário ─► modelo de embeddings compatível? ─► vetor da pergunta
        ─► busca vetorial (pgvector) + busca textual (tsvector) ─► fusão RRF ─► top k
        ─► há evidência? ──não──► recusa (o modelo nem é chamado)
                         └─sim──► prompt com trechos numerados ─► Ollama ─► resposta
        ─► extrai as citações [n] ─► grava em `consulta` (com o texto citado)
```

## Busca híbrida

- **Vetorial:** `ORDER BY vetor <=> :v` (cosseno), usa o índice HNSW.
- **Textual:** `tsvector` em português, com **OU** entre as palavras da pergunta (o padrão do
  Postgres exige todas e zera perguntas naturais); quem casa mais palavras sobe.
- **Fusão (RRF):** nota = soma de `1/(60 + posição)` em cada lista. Usa só a posição, então não
  compara notas de escalas diferentes. Empate: maior similaridade.
- `modo` aceita `hibrida` (padrão), `vetorial` e `textual`, para a etapa 5 comparar.
- O filtro de `colecoes` vai **dentro** das consultas SQL, antes de qualquer trecho sair do banco.

## Recusa

Duas camadas:
1. **Sem evidência:** nenhuma palavra da pergunta aparece nos trechos **e** o melhor trecho tem
   similaridade abaixo de `LIMIAR_SIMILARIDADE` (0,25). Resposta fixa, sem chamar o modelo.
2. **No prompt:** o modelo é instruído a responder exatamente a frase de recusa quando os
   trechos não bastam. A resposta é reconhecida e marcada `recusou: true`.

O limiar **não foi calibrado** com o modelo de embeddings real; a etapa 5 mede. Se perguntas
fora do corpus estão passando, suba o valor; se perguntas boas estão sendo recusadas, desça.

## Citação

Os trechos vão ao modelo como `<trecho n="1" documento="..." pagina="3">`. A resposta cita
`[1]`, `[2][3]` ou `[1, 2]`. A API:
- devolve em `fontes` só os trechos **realmente citados**, com documento, página e texto;
- descarta citações de números que não existem;
- se a resposta não é recusa e não citou nada, devolve `fontes: []` e um `aviso`.

`trechos_usados` lista tudo o que foi enviado ao modelo.

## Segurança

- Os trechos são tratados como dado: ficam entre marcas, o texto do documento não consegue
  abrir nem fechar uma marca, e o sistema avisa o modelo para ignorar ordens dentro deles.
  Isso **reduz** o risco de injeção de prompt; não o elimina. A etapa 5 testa documentos hostis.
- A consulta guarda o texto citado, não uma referência: apagar o documento não quebra o
  histórico.
- Nada do texto dos documentos vai para o log: só identificadores, contagens e tempos.

## Modelo de embeddings trocado

Se algum trecho foi gerado por outro modelo (ou outra dimensão), a consulta devolve **409** e
lista os documentos. Reprocesse-os com `POST /documentos/{id}/reprocessar`.

## Ollama

`LLM_PROVIDER=ollama` e `LLM_MODEL=<nome em ollama list>`. A API fala com `LLM_BASE_URL`
(padrão `http://127.0.0.1:11434`) pela API nativa `/api/chat`, com temperatura 0.

- **Janela de contexto:** o Ollama usa um padrão pequeno e corta o prompt em silêncio. A API
  pede `num_ctx` proporcional ao prompt (mínimo 4096, teto `LLM_NUM_CTX_MAX`, padrão 8192).
- **Erros claros:** servidor fora do ar, modelo não baixado (`ollama pull ...`), demora acima de
  `LLM_TIMEOUT_S` (300 s) viram **503** com a mensagem do problema.
- **Dentro do Compose**, a API alcança o Ollama do computador por `host.docker.internal`
  (`LLM_BASE_URL_DOCKER`). No Windows o Ollama escuta só em 127.0.0.1 por padrão; se o contêiner
  não conseguir falar com ele, defina `OLLAMA_HOST=0.0.0.0` no Windows e reinicie o Ollama.
- Modelos que emitem `<think>...</think>` têm o bloco removido da resposta.

## Limites e estado

| Variável | Padrão | Efeito |
|---|---|---|
| `LIMITE_DIARIO_GASTO` | `1.0` | soma de `consulta.custo` do dia; acima disso, 429. Com Ollama o custo é 0 |
| `MAX_TOKENS_POR_PERGUNTA` | `2000` | teto de tokens gerados (também limitado por `LLM_MAX_TOKENS_SAIDA`, 500) |
| `CONSULTA_K` | `5` | trechos enviados ao modelo (1 a 10) |
| `CONSULTA_CANDIDATOS` | `20` | candidatos de cada busca antes da fusão |

Banco fora do ar agora devolve **503** "Banco de dados indisponível" em qualquer rota, em vez de 500.

## Experimentar

```powershell
$h = @{ "X-API-Key" = "SUA_CHAVE" }
$corpo = @{ pergunta = "Quem é Lígia?" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/perguntas -Method Post -Headers $h `
  -ContentType "application/json; charset=utf-8" -Body $corpo
```

Ou pela página interativa em `http://127.0.0.1:8000/docs` (Authorize, depois `POST /perguntas`).
