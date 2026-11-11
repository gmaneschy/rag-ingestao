# Interface web (fatia 1: Documentos)

Acesse `http://127.0.0.1:8000/` (leva a `/app/documentos`). Entre com a mesma `API_KEY` do `.env`.

- **Entrada:** a chave é trocada por um cookie (`rag_sessao`, HttpOnly, SameSite=Strict). O cookie é um
  HMAC da chave: não abre a API JSON e deixa de valer se a chave mudar.
- **Documentos:** enviar vários arquivos (escolher ou soltar), coleção, filtros por coleção e estado;
  a lista se atualiza sozinha enquanto há documentos processando.
- **Detalhe:** estado, tipos de trecho, seções detectadas, amostra de 20 trechos, reprocessar e remover
  (com a escolha: apagar só os trechos citados ou as consultas inteiras).
- **Segurança:** ações que alteram dados exigem o cabeçalho `HX-Request` (CSRF). HTMX e a fonte
  (Atkinson Hyperlegible, OFL) estão em `app/web/static`; nenhuma chamada a CDN.

## Fatia 2: Perguntar

`/app/perguntar`: caixa de pergunta e, em "Parâmetros", modo de busca (híbrida/vetorial/textual), tipo
(factual/análise), k, coleções, documentos específicos e tipos extras (notas, bibliografia, índice,
copyright). A resposta aparece em coluna de leitura, com cada `[n]` ligado à fonte n na margem (trecho
completo, documento e página). Abaixo, "Trechos recuperados" mostra o que o modelo recebeu (debug).
"A resposta ajudou?" grava em `consulta.ajudou`. `/app/consultas/{id}` mostra uma consulta guardada;
fontes de documentos removidos aparecem como "fonte removida". Os parâmetros valem só para a pergunta
em curso; a persistência (tabela `configuracao`) vem na fatia 4.

## Fatia 3: Histórico e Comparar

`/app/historico`: consultas guardadas, da mais recente à mais antiga, com filtros por texto, tipo,
avaliação e situação ("sem fontes" ou "fonte removida"), 25 por página. `/app/comparar`: a mesma pergunta
e a mesma busca para vários modelos do Ollama (lista lida de `/api/tags`), uma coluna por modelo, cada
uma gerada em chamada própria, com opção de ligar/desligar o raciocínio (`think`). A comparação não
entra no histórico nem na contagem de gasto diário.

## Fatia 4: Configuração, Saúde e Logs

- **Configuração** (`/app/configuracao`): ajustes que valem na próxima pergunta, sem reiniciar (k, candidatos,
  RRF, limiar, nota mínima e candidatos do reordenador, modelo de geração, raciocínio, limites de tokens e de
  gasto). Precedência: **parâmetro da pergunta > tabela `configuracao` > `.env`**. Campo vazio apaga o ajuste
  (volta ao `.env`). Valores inválidos, ou que violam regra entre campos, não são salvos. Se o banco tiver um
  valor que deixou de ser válido, o app usa o `.env` e avisa no log. Fixos (provedores, embeddings, chaves,
  reordenador) continuam só no `.env`. A API `/perguntas` usa os mesmos ajustes. O comando de avaliação lê só
  o `.env`, para as medições serem reproduzíveis.
- **Saúde** (`/app/saude`): banco, migração, pgvector, documentos por estado, vetores de outro modelo de
  embeddings, consultas e gasto do dia, Ollama (no ar? modelo instalado?), reordenador, limpezas pendentes.
- **Logs** (`/app/logs`): últimos 1000 eventos do processo, filtro por nível e texto, atualização automática.

Aplique a migração: `uv run alembic upgrade head` (ou reinicie o contêiner, se ele a roda na subida).

## Diagnóstico de recusas

Cada resposta traz `diagnostico` (também na API `/perguntas` e no log "pergunta respondida"): quem recusou
(`portao`, `modelo` ou `sem_trechos`), a melhor nota do reordenador contra o mínimo, a melhor similaridade
contra o limiar, quantos achados a busca trouxe e o tempo de busca, reordenador e geração. Na tela, a
recusa explica o motivo e "Trechos recuperados" mostra a nota e a similaridade de cada trecho.

## Ações em lote e mover de coleção

Na lista de Documentos, marque caixas (ou a caixa do cabeçalho, para todos da lista atual; use o
filtro de estado "Falhou" para pegar só os que falharam). Aparece uma barra com:

- **Reprocessar**: coloca todos na fila e os processa um por vez, em segundo plano.
- **Mover para**: troca a coleção sem reindexar (o trecho não guarda a coleção). O que colidiria no
  destino (mesmo nome ou mesmo conteúdo) é recusado, com o motivo, e o resto é movido.

A seleção sobrevive às atualizações automáticas da lista. Na página de um documento há o mesmo
"mover". A API tem `POST /documentos/reprocessar` (`{"ids": [...]}` ou `{"todos": true, "colecao":
..., "estado": ...}`) e `POST /documentos/mover` (`{"ids": [...], "destino": "..."}`). A migração
`0008` só acrescenta o tipo de evento `movido` ao registro de auditoria.

## Idioma da resposta

Em Configuração, **Idioma da resposta** (`LLM_IDIOMA` no `.env`): `pergunta` (padrão, responde no
idioma em que foi perguntado), `pt` (sempre português do Brasil) ou `en` (sempre inglês). Troca só a
regra de idioma do prompt; a frase de recusa e as citações `[n]` não mudam, e na análise os títulos
"Evidências:" e "Avaliação:" continuam os mesmos, porque o app depende deles. Vale na próxima
pergunta, sem reiniciar. Útil quando a pergunta é em português e o documento está em inglês ou russo
e o modelo se confunde de língua.

## Andamento da ingestão

Enquanto um documento está `pendente`, a lista e a página do documento mostram uma barra com o
percentual, a etapa e o tempo restante estimado; sem andamento ainda, aparece "Na fila". A
percentagem segue o custo real: ler o arquivo vai a 10%, gerar os vetores (o grosso, em lotes de
16 trechos) de 10% a 95%, gravar fecha em 100%. O tempo restante só aparece depois de 15%, por
extrapolação do ritmo daquele documento (um PDF grande pode levar um tempo na leitura antes de o
primeiro percentual andar). Os campos `progresso`, `etapa` e `restante_s` também vêm em
`GET /documentos` e `GET /documentos/{id}` (a migração `0009` cria as colunas). Os botões de
Perguntar e Comparar mostram um indicador giratório enquanto a resposta é gerada.

## Casca: barra lateral e Markdown

A navegação mora numa **barra lateral ocultável** (botão no topo dela; quando fechada, um botão no canto
mostra de novo). A escolha fica lembrada no navegador. No celular a barra vira uma gaveta que fecha
ao escolher um destino ou com Esc. "Nova conversa" leva a Perguntar.

As respostas do modelo são renderizadas como **Markdown**: negrito, listas, tabelas, código e citações
`[n]` clicáveis (que não viram link dentro de código). HTML cru na resposta é mostrado como texto, e
links `javascript:` não são linkados, porque a saída do modelo não é confiável.

## Conversas (chat)

- A barra lateral lista as conversas (mais recentes primeiro); "⋯" abre o diálogo para **renomear** ou
  **apagar**. Apagar remove também as consultas (respostas) da conversa.
- `/app/perguntar` é uma conversa nova; a primeira pergunta cria a conversa (título = início da pergunta).
  Cada pergunta é independente: não há contexto de pergunta anterior.
- A pergunta roda numa thread do servidor, **não presa à página**: trocar de tela não a interrompe, e a
  conversa mostra um indicador na barra enquanto gera. A tela consulta o turno a cada 1 s e mostra a
  etapa (buscando, reordenando, gerando) e o texto parcial (streaming do Ollama).
- **Parar** cancela a geração (o turno vira "interrompida", sem consulta registrada); **Tentar de novo** a
  refaz. **Editar** reescreve a pergunta e gera de novo, apagando a consulta antiga.
- Parâmetros (busca, tipo, k, coleções, documentos, tipos extras) ficam gravados na conversa.
- Limites: o estado em andamento mora na memória de um processo só; se o servidor reinicia, turnos que
  estavam gerando viram "erro" na subida. Turnos cancelados ou com erro guardam o texto da pergunta no
  turno (sem consulta): a remoção de documentos não os apaga.
- Enter envia; Shift+Enter quebra a linha.

### Módulos da conversa

Os botões **Documentos** e **Parâmetros**, ao lado de "Perguntar", abrem diálogos. Documentos: coleções
e documentos que a busca deve usar (nada marcado = todos). Parâmetros: modo de busca, tipo, k e tipos
extras. Fechar o diálogo grava a escolha na conversa (`POST /app/conversas/{id}/config`); ela volta ao
reabrir a conversa. Um número ou ponto ao lado do botão indica que há algo diferente do padrão.

### Painel de fontes

Cada resposta tem o botão **Fontes (n)**; clicar nele, ou numa citação `[n]` do texto, abre o painel
recolhível à direita com os trechos daquela resposta (a citação destaca o trecho). ✕ fecha o painel.
Em telas estreitas o painel sobrepõe a conversa.
