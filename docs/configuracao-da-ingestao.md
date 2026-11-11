# Configuração da ingestão

Na tela **Configuração**, grupo **Ingestão**. Os valores da tela valem mais que o `.env`; campo vazio
usa o `.env`. Cada campo tem, na própria tela, o que é e o que muda ao aumentar ou diminuir.

| Campo | Padrão | O que faz |
|---|---|---|
| Perfil de trecho | personalizado | Escolhe tamanho, sobreposição e (no curto) a nota mínima de uma vez |
| Tamanho do trecho | 500 | Caracteres por trecho (4 caracteres ≈ 1 token em português) |
| Sobreposição | 80 | Quanto do trecho anterior se repete; abaixo de metade do tamanho |
| Tamanho máximo do arquivo | 20 MB | Maiores são recusados no envio |
| Máximo de páginas | 500 | Documentos maiores são recusados |

## Perfis

| Perfil | Tamanho / sobreposição | Para | Nota mínima |
|---|---|---|---|
| curto | 500 / 80 (~125 tokens) | fatos, FAQs, notas, diários | 0,05 (calibrada) |
| médio | 1000 / 150 (~250 tokens) | artigos, cláusulas, seções: leis, contratos, manuais | a configurada (não calibrada) |
| longo | 1800 / 250 (~450 tokens) | prosa corrida: capítulos, relatórios | a configurada (não calibrada) |
| personalizado | os campos | quem sabe o que faz | a configurada |

Com um perfil escolhido, os campos de tamanho e sobreposição são ignorados.

**Só o perfil curto tem nota mínima calibrada** (0,05, medida com o conjunto de referência do
projeto e o jina-reranker-v2). A nota do reordenador depende do comprimento do trecho: nos perfis
médio e longo a nota configurada continua valendo, e ela pode recusar respostas boas ou deixar passar
respostas sem base. Antes de confiar nas recusas, calibre (abaixo).

## Mudar o tamanho exige reprocessar

O tamanho vale para documentos **novos ou reprocessados**. Cada documento guarda o tamanho com que
foi dividido; a Configuração avisa quantos estão com tamanho diferente do atual. Para atualizá-los,
selecione-os em Documentos e use "Reprocessar". Misturar tamanhos na mesma base faz a nota mínima
valer só para parte dos trechos.

## Calibrar a nota mínima de um perfil

O comando de avaliação lê só o `.env` (as medições precisam ser reproduzíveis).

1. No `.env`, defina `PERFIL_TRECHOS=medio` e reinicie.
2. Reprocesse os documentos do conjunto de referência.
3. Rode `uv run python -m app.avaliacao rodar --modos hibrida --reranker jinaai/jina-reranker-v2-base-multilingual --nota-minima 0.05 --rotulo medio`.
4. Na tabela "Nota do reordenador como portão", escolha o ponto com boa recusa correta sem
   derrubar respostas boas, e grave a nota em Configuração.
