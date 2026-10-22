from scripts.gen_env import gerar

EXEMPLO = """\
POSTGRES_USER=app
POSTGRES_DB=meu_banco
POSTGRES_PASSWORD=             # segredo (gerado)
DB_PORT=5433
DATABASE_URL=
API_KEY=                       # segredo (gerado)
DOMAIN=localhost
"""


def _como_dict(texto: str) -> dict[str, str]:
    pares = {}
    for linha in texto.splitlines():
        chave, _, resto = linha.partition("=")
        pares[chave] = resto.split("#")[0].strip()
    return pares


def test_preenche_segredos_e_monta_a_url() -> None:
    env = _como_dict(gerar(EXEMPLO))
    assert len(env["POSTGRES_PASSWORD"]) >= 32
    assert len(env["API_KEY"]) >= 32
    assert env["DATABASE_URL"] == (
        f"postgresql+psycopg://app:{env['POSTGRES_PASSWORD']}@localhost:5433/meu_banco"
    )


def test_nao_mexe_no_que_nao_e_segredo() -> None:
    env = _como_dict(gerar(EXEMPLO))
    assert env["DOMAIN"] == "localhost"
    assert env["POSTGRES_DB"] == "meu_banco"


def test_cada_execucao_gera_valores_diferentes() -> None:
    assert gerar(EXEMPLO) != gerar(EXEMPLO)
