"""Ajustes que a interface altera sem reiniciar o app.

Precedência, da mais forte à mais fraca: parâmetro da própria pergunta > tabela `configuracao` >
`.env` (e os padrões do código). Só entram aqui os ajustes que valem na hora da consulta; provedor,
modelo de embeddings, chaves e reranker dependem de reiniciar (ou de reindexar) e ficam no `.env`.

Um ajuste sem linha na tabela não está sobrescrito: vale o `.env`. Apagar o campo na tela apaga a
linha, que é o "restaurar".
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.consulta.config import ConsultaSettings, get_consulta_settings
from app.db import get_session
from app.llm.config import LLMSettings, get_llm_settings

logger = logging.getLogger(__name__)

Tipo = Literal["int", "float", "bool", "texto", "escolha"]


@dataclass(frozen=True)
class Campo:
    chave: str
    alvo: Literal["consulta", "llm"]
    grupo: str
    rotulo: str
    ajuda: str
    tipo: Tipo
    minimo: float | None = None
    maximo: float | None = None
    opcoes: tuple[tuple[str, str], ...] = ()  # (valor, rótulo) do tipo "escolha"


CAMPOS: tuple[Campo, ...] = (
    Campo(
        "consulta_k",
        "consulta",
        "Busca",
        "Trechos por pergunta (k)",
        "Quantos trechos vão ao modelo na pergunta factual.",
        "int",
        1,
        10,
    ),
    Campo(
        "consulta_k_analise",
        "consulta",
        "Busca",
        "Trechos na análise",
        "A análise precisa de mais material.",
        "int",
        1,
        10,
    ),
    Campo(
        "consulta_candidatos",
        "consulta",
        "Busca",
        "Candidatos de cada busca",
        "Quantos trechos cada busca (vetorial e textual) entrega antes de fundir. "
        "Precisa ser maior ou igual ao maior k.",
        "int",
        1,
        100,
    ),
    Campo(
        "consulta_rrf_k",
        "consulta",
        "Busca",
        "Constante da fusão (RRF)",
        "60 é o valor da literatura; menor dá mais peso ao topo de cada lista.",
        "int",
        1,
        1000,
    ),
    Campo(
        "limiar_similaridade",
        "consulta",
        "Recusa",
        "Limiar de similaridade",
        "Sem reordenador, similaridade mínima do melhor trecho para haver evidência.",
        "float",
        0,
        1,
    ),
    Campo(
        "reranker_nota_minima",
        "consulta",
        "Recusa",
        "Nota mínima do reordenador",
        "Com reordenador, nota mínima do 1º trecho para haver evidência. Vazio: vale o limiar "
        "acima. A escala depende do modelo; meça antes.",
        "float",
        -100,
        100,
    ),
    Campo(
        "reranker_candidatos",
        "consulta",
        "Recusa",
        "Candidatos reordenados",
        "Quantos trechos da busca o reordenador reavalia (do maior k até 50).",
        "int",
        1,
        50,
    ),
    Campo(
        "llm_model",
        "llm",
        "Geração",
        "Modelo de geração",
        "Nome no Ollama (veja `ollama list`). Só vale com LLM_PROVIDER=ollama.",
        "texto",
    ),
    Campo(
        "llm_think",
        "llm",
        "Geração",
        "Raciocínio (qwen3…)",
        "Desligar evita respostas vazias em modelos que gastam os tokens raciocinando.",
        "bool",
    ),
    Campo(
        "llm_idioma",
        "llm",
        "Geração",
        "Idioma da resposta",
        "Em que língua o modelo responde. A frase de recusa não muda.",
        "escolha",
        opcoes=(
            ("pergunta", "o idioma da pergunta"),
            ("pt", "sempre português"),
            ("en", "sempre inglês"),
        ),
    ),
    Campo(
        "llm_max_tokens_saida",
        "llm",
        "Geração",
        "Tokens da resposta",
        "Limite de tokens gerados na pergunta factual.",
        "int",
        1,
        4000,
    ),
    Campo(
        "llm_max_tokens_analise",
        "llm",
        "Geração",
        "Tokens da análise",
        "Limite de tokens gerados na análise.",
        "int",
        1,
        4000,
    ),
    Campo(
        "limite_diario_gasto",
        "llm",
        "Geração",
        "Limite diário de gasto",
        "Em dólares; 0 desliga. Vale para modelos pagos.",
        "float",
        0,
        1000,
    ),
)
POR_CHAVE = {c.chave: c for c in CAMPOS}


def converter(campo: Campo, texto: str) -> object:
    """Texto digitado -> valor do tipo certo. ValueError com mensagem para a pessoa."""
    texto = texto.strip().replace(",", ".") if campo.tipo in ("int", "float") else texto.strip()
    if campo.tipo == "bool":
        if texto not in ("sim", "nao"):
            raise ValueError(f"{campo.rotulo}: escolha ligado ou desligado")
        return texto == "sim"
    if campo.tipo == "escolha":
        if texto not in {valor for valor, _ in campo.opcoes}:
            raise ValueError(f"{campo.rotulo}: escolha uma das opções")
        return texto
    if campo.tipo == "texto":
        if len(texto) > 120:
            raise ValueError(f"{campo.rotulo}: texto longo demais")
        return texto
    try:
        valor = int(texto) if campo.tipo == "int" else float(texto)
    except ValueError:
        esperado = "número inteiro" if campo.tipo == "int" else "número (use ponto)"
        raise ValueError(f"{campo.rotulo}: esperado {esperado}") from None
    if campo.minimo is not None and campo.maximo is not None:
        if not campo.minimo <= valor <= campo.maximo:
            raise ValueError(
                f"{campo.rotulo}: deve ficar entre {campo.minimo:g} e {campo.maximo:g}"
            )
    return valor


def texto_do_valor(campo: Campo, valor: object) -> str:
    if campo.tipo == "bool":
        return "sim" if valor else "nao"
    return str(valor)


def ler(session: Session) -> dict[str, str]:
    linhas = session.execute(text("SELECT chave, valor FROM configuracao")).all()
    return {chave: valor for chave, valor in linhas if chave in POR_CHAVE}


def _aplicar(
    consulta: ConsultaSettings, llm: LLMSettings, guardado: dict[str, str]
) -> tuple[ConsultaSettings, LLMSettings]:
    ajustes: dict[str, dict[str, object]] = {"consulta": {}, "llm": {}}
    for chave, valor in guardado.items():
        campo = POR_CHAVE[chave]
        ajustes[campo.alvo][chave] = converter(campo, valor)
    # Reconstrói com validação: um valor que viola uma regra entre campos não passa.
    nova_consulta = type(consulta)(**{**consulta.model_dump(), **ajustes["consulta"]})
    nova_llm = type(llm)(**{**llm.model_dump(), **ajustes["llm"]})
    return nova_consulta, nova_llm


def efetivas(session: Session) -> tuple[ConsultaSettings, LLMSettings]:
    """O .env com os ajustes do banco por cima. Se o banco tiver algo inválido (por exemplo,
    depois de o .env mudar), o app segue com o .env e avisa no log em vez de falhar."""
    base = get_consulta_settings(), get_llm_settings()
    guardado = ler(session)
    if not guardado:
        return base
    try:
        return _aplicar(*base, guardado)
    except (ValidationError, ValueError):
        logger.warning("configuração do banco inválida; usando o .env", exc_info=True)
        return base


def consulta_efetiva(session: Annotated[Session, Depends(get_session)]) -> ConsultaSettings:
    return efetivas(session)[0]


def llm_efetivo(session: Annotated[Session, Depends(get_session)]) -> LLMSettings:
    return efetivas(session)[1]


def salvar(session: Session, digitado: dict[str, str]) -> list[str]:
    """Grava os campos preenchidos e apaga os vazios. Com erros, devolve-os e não grava nada."""
    erros: list[str] = []
    para_gravar: dict[str, str] = {}
    for campo in CAMPOS:
        bruto = digitado.get(campo.chave, "").strip()
        if not bruto:
            continue
        try:
            para_gravar[campo.chave] = texto_do_valor(campo, converter(campo, bruto))
        except ValueError as erro:
            erros.append(str(erro))
    if not erros:
        try:
            _aplicar(get_consulta_settings(), get_llm_settings(), para_gravar)
        except ValidationError as erro:
            erros.extend(str(e["msg"]).removeprefix("Value error, ") for e in erro.errors())
        except ValueError as erro:
            erros.append(str(erro))
    if erros:
        return erros
    session.execute(
        text("DELETE FROM configuracao WHERE chave <> ALL(:ficam)"), {"ficam": list(para_gravar)}
    )
    for chave, valor in para_gravar.items():
        session.execute(
            text(
                "INSERT INTO configuracao (chave, valor) VALUES (:c, :v) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, atualizado_em = now()"
            ),
            {"c": chave, "v": valor},
        )
    session.commit()
    return []
