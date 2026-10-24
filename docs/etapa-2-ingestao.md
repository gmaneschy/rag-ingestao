# Etapa 2 — Ingestão

Soltar um arquivo na pasta `dados/entrada` (ou enviar por `POST /documentos`) o torna
consultável sem intervenção. Reenviar o mesmo arquivo não duplica nada.

## Fluxo

```
dados/entrada ──► n8n (nó de pasta) ──► POST /documentos ──► registrar()  ──► resposta 202
                                                                  │
                                                       segundo plano ▼
                                      extrair ► dividir ► embeddings ► trocar trechos (1 transação)
```

1. **n8n só avisa.** O fluxo `IngestaoPasta0001` lê o arquivo novo e chama a API. Toda a lógica
   (validação, extração, vetores) vive na API, que também aceita envio direto.
2. **`registrar()`** valida nome, tipo, tamanho e assinatura do PDF; calcula o SHA-256; grava o
   arquivo e a linha em `documento` com estado `pendente`; responde na hora.
3. **`processar()`** roda em segundo plano: extrai o texto por página, divide em trechos, gera os
   vetores e, em **uma única transação**, apaga os trechos antigos, insere os novos e marca
   `processado`. A busca vê o texto antigo ou o novo, nunca metade de cada.

## Regras de idempotência

| Situação | Resultado |
|---|---|
| Mesmo conteúdo, mesma coleção (qualquer nome) | `duplicado`: nada muda; se estava `pendente`/`falhou`, tenta de novo |
| Mesmo nome, conteúdo diferente | `nova_versao`: a linha é reaproveitada (`versao`+1); os trechos antigos continuam consultáveis até a troca |
| Falha ao processar a versão nova | estado `falhou` com o motivo; **o texto antigo continua** |
| Chega a versão 3 enquanto a 2 processa | a 2 descarta o resultado (confere o hash com `FOR UPDATE`) |
| Mesmo conteúdo em outra coleção | outro documento |

## Divisão em trechos

Por parágrafo, com sobreposição, **sem atravessar páginas** (a citação "página N" é sempre
verdadeira). Parágrafo maior que o tamanho é quebrado por frase e, no limite, por palavra.
Padrão: 500 caracteres com 80 de sobreposição (`TAMANHO_TRECHO`, `SOBREPOSICAO_TRECHO`).

O modelo de embeddings padrão enxerga cerca de **128 tokens** (~500 caracteres) e trunca o
resto sem avisar. Por isso o padrão é 500; a etapa 5 mede 3 tamanhos para decidir.

## Embeddings locais

`EMBEDDINGS_PROVIDER=local` usa o fastembed (ONNX, CPU, sem PyTorch) com
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`: multilíngue (~50 idiomas, inclui
português), 384 dimensões (casa com `trecho.vetor`), ~220 MB, baixado na primeira ingestão e
guardado em `MODELOS_DIR` (volume `modelos` no Compose).

O documento do projeto sugeria o `multilingual-e5-small`, mas o fastembed não o oferece; os
outros multilíngues disponíveis têm 768 ou 1024 dimensões e exigiriam mudar a coluna.

`EMBEDDINGS_PROVIDER=simulado` (padrão dos testes) não baixa nada.

## O que ainda não existe (de propósito ou pendente)

- **OCR** de PDF escaneado: hoje o documento fica `falhou` com a dica "OCR". Entra depois.
- **Limite de tempo e memória** na extração: há limite de tamanho (20 MB) e de páginas (500),
  mas a extração roda no mesmo processo. Isolar em subprocesso é um endurecimento futuro.
- **Retomada de pendentes**: se a API reiniciar no meio de uma ingestão, o documento fica
  `pendente`. Reenviar o arquivo resolve; uma tarefa de retomada automática entra na etapa 4.
- Listagem, detalhe com amostra de trechos e **remoção** (etapa 4); consulta (etapa 3).

## Variáveis novas

| Variável | Padrão | Observação |
|---|---|---|
| `EMBEDDINGS_PROVIDER` | `simulado` no código, `local` no `.env.example` e no Compose | |
| `DADOS_DIR` | `dados` | arquivos originais em `dados/arquivos/<id>/v<versão>.<ext>` |
| `MODELOS_DIR` | `modelos` | cache do modelo |
| `TAMANHO_MAX_MB` | `20` | acima disso, 413 |
| `MAX_PAGINAS` | `500` | PDF maior vira `falhou` |
| `RAG_API_URL` | `http://host.docker.internal:8000` | onde o n8n encontra a API |

## n8n

O Compose liga o que o fluxo precisa: `NODES_EXCLUDE=[]` (o nó de pasta vem desligado no
n8n 2.x), `N8N_RESTRICT_FILE_ACCESS_TO=/files` e `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.
A URL e a chave da API chegam ao fluxo por `$env`, então o JSON versionado não tem segredos.
Custo: qualquer fluxo do n8n passa a poder ler as variáveis do contêiner. Com um único dono,
é aceitável; com vários usuários, troque por uma credencial do n8n.

A pasta é vigiada por **polling** (`usePolling`), porque eventos de arquivo não atravessam o
volume do Docker Desktop no Windows. Se o n8n reenviar um arquivo (por exemplo ao reiniciar),
a API responde `duplicado` e nada muda.

## Testar sem o n8n

```powershell
uv run uvicorn app.main:app --reload                  # num terminal
uv run python scripts/enviar.py caminho\arquivo.pdf   # em outro
```

Teste do modelo real (baixa ~220 MB):

```powershell
$env:TEST_MODELO_LOCAL = "1"
uv run pytest tests/test_embeddings_local.py -v
```
