# Etapa 5a: conjunto de referência e executor de recuperação

Mede o que já existe, sem o LLM: o trecho certo aparece entre os k recuperados? O portão de
evidência recusa quando deve? Qual `LIMIAR_SIMILARIDADE` separa as duas coisas?

## Como aplicar
Extraia o pacote sobre o projeto. Não há migração nem dependência nova.

## 1. Montar o conjunto (`avaliacao/perguntas.json`)
Copie `avaliacao/perguntas.exemplo.json` e preencha. Cada caso tem `pergunta`, `responde`
(`false` = a resposta não está no corpus) e, se responde, o `suporte`: onde a resposta está.

- `documento`: parte do nome do arquivo (sem acento e sem diferenciar maiúsculas).
- `paginas`: `[de, ate]`, páginas do PDF (não as impressas). Opcional.
- `contem`: termos que o trecho precisa ter (todos). Opcional, mas informe `paginas` ou `contem`.

O trecho correto é identificado por lugar e conteúdo, e não por `id`, para que o mesmo conjunto
valha ao trocar o tamanho do trecho ou reprocessar. Para achar o suporte de uma pergunta:

    uv run python -m app.avaliacao inspecionar "Qual o nome da irmã de Lígia?"

Mostra os 10 trechos com documento, página e texto; escolha o que contém a resposta.

Recomendação: 25 a 40 perguntas, cerca de 25% sem resposta no corpus, misturando português e
inglês, perguntas de um fato (nome, data) e de síntese, e algumas com termos exatos.
`respostas.json` do projeto tem boas perguntas para começar.

## 2. Rodar
    uv run python -m app.avaliacao rodar --modos hibrida,vetorial,textual
    uv run python -m app.avaliacao rodar --gerar --rotulo qwen2.5-7b     # inclui o LLM

Saída em `avaliacao/resultados/AAAAMMDD-HHMMSS-<rotulo>.{json,md}`: tabela de recuperação
(R@1/3/5/10, MRR, latência da busca), tabela de geração (com `--gerar`), varredura do limiar e a
lista de perguntas cujo trecho correto não veio. O JSON traz, por pergunta, o topo da busca e a
resposta gerada, para ler à mão.

## O que cada métrica diz
- **R@k**: das perguntas com resposta, quantas têm o trecho correto entre os k primeiros.
- **MRR**: média de 1/posição do primeiro acerto.
- **Recusa correta / indevida (portão)**: sem o LLM, se o sistema acha que há evidência. Com
  `--gerar`, as mesmas taxas medem a recusa final (portão + modelo).
- **Fundamentação automática**: das respostas dadas, quantas citam um trecho correto. É um piso
  barato; não lê a resposta. A conferência do conteúdo continua sendo sua (`resposta_esperada`).
- **Varredura do limiar**: recusa correta/indevida para cada limiar. Atenção: se a pergunta sem
  resposta tem alguma palavra em comum com algum trecho, o portão já diz "há evidência" pelo
  léxico e nenhum limiar a recusa; é o caso a investigar (`ha_lexical` no JSON).

## Limites
- O conjunto é seu e é pequeno: diferenças de poucos pontos não são significativas.
- O modo de geração usa `CONSULTA_K` trechos, como a consulta real.
- Ainda não mede tamanho de trecho, reranker nem extração de PDF: é a etapa 5b.

## Achado da primeira rodada: a busca textual
Em perguntas em inglês a busca textual quase não funcionava (R@1 0%, R@10 25%) e o portão de
evidência nunca recusava, qualquer que fosse o limiar. Causa: a configuração `portuguese` não
trata "what", "was", "on" como palavras vazias; como a consulta é OU, essas palavras casavam com
quase todos os trechos, afogavam as palavras raras e davam "evidência" a qualquer pergunta.
Correção: a consulta textual usa só os radicais que aparecem em até 25% dos trechos do corpus
(`FRACAO_MAX_DO_TERMO`, desligado em corpus com menos de 20 trechos). Se nenhum radical sobra, a
perna textual não devolve nada e quem decide é a similaridade. A varredura do limiar ganhou duas
colunas ("só sim.") com o que aconteceria sem o atalho lexical.

## Conferir o gabarito antes de medir
    uv run python -m app.avaliacao conferir

Para cada suporte do `perguntas.json` diz se algum trecho do corpus (tipo `corpo`) o alcança e,
se não, por quê: documento inexistente (lista os nomes gravados), faixa de páginas sem trechos,
termos que não aparecem juntos (mostra em que páginas cada um aparece) ou trecho só em notas.
Rode sempre que mudar o conjunto. O nome do documento é comparado sem acento e sem pontuação.

Para ler só as falhas de uma rodada, sem abrir o `.json` inteiro:

    uv run python -m app.avaliacao falhas avaliacao/resultados/ARQUIVO.json --topo 3

Dica de gabarito: prefira um termo característico em `contem` e use `contem_algum` para
alternativas; vários termos em `contem` exigem todos no mesmo trecho (no máximo ~500 caracteres)
e o gabarito vira inalcançável se a frase os separa em dois trechos.

## Reordenador (cross-encoder)
Opcional, desligado por padrão. Para medir:

    uv run python -m app.avaliacao rodar --modos hibrida --ks 1,3,5,10,20 --rotulo sem-reranker
    uv run python -m app.avaliacao rodar --modos hibrida --ks 1,3,5,10,20 \
        --reranker jinaai/jina-reranker-v2-base-multilingual --rotulo rerank-jina
    uv run python -m app.avaliacao rodar --modos hibrida --ks 1,3,5,10,20 \
        --reranker BAAI/bge-reranker-v2-m3-int8 --rotulo rerank-bge-m3

A busca traz `--candidatos-reranker` (20) trechos, o modelo os reordena e a tabela mostra R@k
dos primeiros. **R@20 é o teto do reordenador:** ele só reordena o que a busca trouxe. Se R@20
for bem maior que R@10, há ganho possível; se for igual, o problema está em encontrar o trecho
(modelo de embeddings), não em ordená-lo.

A seção "Nota do reordenador como portão" mostra, para cada nota mínima do 1º trecho, quantas
perguntas sem resposta seriam recusadas e quantas com resposta seriam recusadas por engano. A
nota do cross-encoder costuma separar "responde" de "não responde" melhor que a similaridade.

Para usar na API: `RERANKER_MODEL=...` no `.env` (`RERANKER_CANDIDATOS` opcional). Só reordena;
o portão de evidência continua como estava até medirmos.

## Resultado da primeira comparação (32 perguntas: 23 com resposta, 9 sem)
| Variação | R@1 | R@5 | R@10 | R@20 | MRR | Busca |
|---|---|---|---|---|---|---|
| sem reordenador | 43% | 65% | 74% | 87% | 0,54 | 0,2 s |
| jina-reranker-v2-base-multilingual | 74% | 87% | 87% | 87% | 0,78 | 5,6 s |
| bge-reranker-v2-m3 (int8) | 78% | 87% | 87% | 87% | 0,81 | 13,2 s |

Os dois reordenadores levam R@5 ao teto (R@20 = 87%); a diferença entre eles é de uma pergunta.
Como portão de recusa, a nota do reordenador separa "responde" de "não responde" (jina: 100% de
recusa correta com 22% de recusa indevida, 3 dessas são perguntas cujo trecho a busca nem trouxe;
bge-m3: 89% e 9%); a similaridade do embedding não separa (33% e 22–35% no melhor limiar).

Para ligar o portão: `RERANKER_MODEL` + `RERANKER_NOTA_MINIMA` no `.env`. Para medir um valor:
`rodar --reranker MODELO --nota-minima X --gerar`.
