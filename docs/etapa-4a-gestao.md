# Etapa 4a — Gestão de documentos

Listar, inspecionar e **remover sem deixar rastro**; retomar a ingestão interrompida.

## Endpoints (todos com `X-API-Key`)

| Rota | O que faz |
| --- | --- |
| `GET /documentos` | Lista com `total` e `itens`. Filtros `colecao`, `estado`; paginação `limite` (1–200, padrão 50) e `deslocamento`. Do mais recentemente atualizado ao mais antigo. |
| `GET /documentos/{id}?amostra=N` | Detalhe. Com `amostra` (0–20), devolve N trechos espalhados do início ao fim (`posicao`, `pagina`, `texto`). Inclui `modelo_embedding`. |
| `DELETE /documentos/{id}?consultas=redigir\|apagar` | Remove o documento e tudo o que veio dele. 404 se não existe. |

Resposta do `DELETE`: `id`, `nome`, `colecao`, `trechos_removidos`, `consultas_redigidas`,
`consultas_apagadas`, `arquivo_removido`.

## O que "sem rastro" significa aqui

Na mesma transação: o documento sai, os trechos e vetores saem junto (`ON DELETE CASCADE`) e os
trechos citados em `consulta.fontes` são tratados. Depois do commit, a pasta do arquivo original é
apagada.

| Onde | O que acontece |
| --- | --- |
| `documento`, `trecho` (vetores) | apagados |
| Arquivo original em `dados/arquivos/{id}` | apagado; se falhar, fica **limpeza pendente** e é repetida a cada subida |
| `consulta.fontes` | `consultas=redigir` (padrão): cada fonte vira `{"n": N, "removido": true}`, sem texto, nome nem página. `consultas=apagar`: a consulta inteira é apagada |
| `evento_documento` | uma linha `removido` com id, nome e data. **Nunca guarda conteúdo** |

**Limite conhecido:** no modo `redigir`, a `pergunta` e o texto da `resposta` ficam, e a resposta
pode ter parafraseado o documento. Quem precisa apagar isso usa `consultas=apagar`.

Segurança da limpeza: só se apaga o que está dentro de `dados/arquivos`, mesmo que o caminho gravado
no banco diga outra coisa.

Consultas feitas antes desta etapa não têm `documento_id` nas fontes; elas são reconhecidas pelo
nome do documento.

## Ao subir a aplicação

1. **Toda a configuração é validada** (ingestão, LLM e consulta). Um valor inválido, como
   `LIMIAR_SIMILARIDADE=0,25`, derruba a subida com a mensagem do erro, em vez de aparecer na
   primeira pergunta.
2. Numa thread própria (não atrasa a subida): repete as limpezas pendentes e reprocessa os
   documentos em `pendente`. Desligue com `RETOMAR_PENDENTES=false`.

Com vários processos (workers), cada um retomaria os mesmos documentos; a troca de trechos é
atômica e confere o hash, então o resultado é correto, só há trabalho repetido.

## Testes

`tests/test_gestao_api.py` cobre, entre outros: remoção completa (trechos, arquivo, fontes,
evento) e a busca recusando depois; `consultas=apagar`; documento de outra coleção com o mesmo nome
intacto; limpeza que falha e é repetida; caminho fora da pasta nunca apagado; retomada de pendentes
na subida; configuração inválida derrubando a subida.
