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
