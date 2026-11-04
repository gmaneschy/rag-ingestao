# Remoção sem rastro: a prova

Remover um documento apaga os trechos e vetores (cascata, na mesma transação), o arquivo original e
o texto citado nas respostas antigas (`consulta.fontes`, redigido ou, com `consultas=apagar`, a
consulta inteira). Esta página mostra como **provar** isso, em vez de só afirmar.

## A varredura

`app/ingestao/rastros.py` procura um termo, sem diferenciar maiúsculas, em:

- **toda tabela** do schema `public`, olhando a linha inteira como texto (colunas, JSON e vetores);
  tabelas novas entram sozinhas;
- **todos os arquivos** da pasta de arquivos (nome e conteúdo);
- os **logs** guardados em memória.

Lista vazia = nenhum rastro. Os testes de `tests/test_rastros.py` provam as duas pontas: antes da
remoção a varredura *enxerga* o termo (trecho, consulta, arquivo); depois de remover com
`consultas=apagar`, não resta nada em lugar nenhum.

## Como rodar na sua instalação

1. Escolha uma frase rara do documento (4+ caracteres) e confira que ela aparece:
   `uv run python -m app.avaliacao rastros "frase rara"` (sai com código 1 e lista onde).
2. Remova o documento pela interface (marcando "apagar também as consultas que o citam") ou por
   `DELETE /documentos/{id}?consultas=apagar`.
3. Rode o mesmo comando: `Nenhum rastro de 'frase rara': banco inteiro e pasta de arquivos limpos.`

## Limites, de propósito

- Com `consultas=redigir` (o padrão), a pergunta e o texto da resposta escrita permanecem: a
  resposta pode ter parafraseado o documento. Quem precisa apagar isso usa `consultas=apagar`.
- `evento_documento` guarda id, **nome do arquivo**, tipo e data da remoção, sem conteúdo. É o
  registro de auditoria.
- A varredura não olha os blocos físicos do Postgres (linhas mortas até o próximo `VACUUM`) nem
  backups feitos antes da remoção.
- Os logs só são varridos dentro do processo (memória); o que foi para arquivo ou coletor externo
  depende de onde você os guarda.
