# Formatos aceitos

| Formato | Como é lido | Citação | Observações |
|---|---|---|---|
| PDF | pypdf, página a página | página | sem OCR: página escaneada não é indexada (o aviso diz quantas) |
| DjVu | `djvutxt` (djvulibre-bin), uma chamada por página | página | só a camada de texto; sem ela, falha com mensagem clara |
| EPUB | ZIP + OPF: ordem da lombada, títulos h1–h6 | seção | EPUB com DRM é recusado; sumário (nav) ignorado |
| FB2, FBZ | XML: seções aninhadas, epígrafes, versos | seção | o corpo de notas de rodapé é ignorado |
| DOCX | estilos "Título n"/"Heading n" e níveis de estrutura; tabelas viram linhas `a \| b` | seção | sem títulos no arquivo, usa as heurísticas do TXT |
| ODT | `text:h` com `outline-level` | seção | |
| HTML | h1–h6; scripts e estilos ignorados | seção | |
| MOBI, AZW3 | pacote `mobi` converte para EPUB/HTML e segue o caminho do EPUB | seção | com DRM, falha com mensagem clara |
| TXT, MD | heurísticas de título | seção | |

Segurança: o XML passa por `defusedxml` (sem entidades externas ou expansão em cadeia), ZIPs têm teto
de 300 MB descompactados, e o conteúdo é conferido contra a extensão antes de gravar (um `.epub` que não
é ZIP é recusado na hora).

Ficam de fora: RTF, `.doc` antigo, `.fb2.zip` (use `.fbz`) e OCR de escaneados. O MOBI foi testado só com a
biblioteca simulada; teste com um arquivo seu. DjVu em Docker precisa do `djvulibre-bin`, já no Dockerfile;
no Windows, rodando fora do Docker, instale o DjVuLibre.
