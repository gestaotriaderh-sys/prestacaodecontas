import io

import pytest

from app import create_app, formatar_moeda, parse_valor


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "teste.db"),
            "UPLOAD_FOLDER": str(tmp_path / "comprovantes"),
        }
    )
    return app.test_client()


def custo(client, **extra):
    dados = {
        "data": "2026-10-01",
        "valor": "100,00",
        "recebedor": "Hortifruti Silva",
        "forma_pagamento": "PIX",
        "tipo": "despesa",
    }
    dados.update(extra)
    return client.post("/custos/novo", data=dados, content_type="multipart/form-data")


def test_parse_e_formatacao():
    assert parse_valor("1.234,56") == 123456
    assert parse_valor("R$ 10") == 1000
    assert parse_valor("10.5") == 1050
    assert parse_valor("-5,00", positivo=False) == -500
    with pytest.raises(ValueError):
        parse_valor("0")
    with pytest.raises(ValueError):
        parse_valor("abc")
    assert formatar_moeda(123456789) == "R$ 1.234.567,89"
    assert formatar_moeda(-500) == "-R$ 5,00"


def test_registra_custo_com_comprovante(client):
    r = custo(client, comprovante=(io.BytesIO(b"%PDF-1.4 teste"), "nota.pdf"))
    assert r.status_code == 302
    pagina = client.get("/custos").get_data(as_text=True)
    assert "Hortifruti Silva" in pagina
    assert "R$ 100,00" in pagina
    assert "/comprovantes/" in pagina


def test_rejeita_comprovante_invalido(client):
    r = custo(client, comprovante=(io.BytesIO(b"x"), "virus.exe"))
    assert r.status_code == 400
    assert "Hortifruti" not in client.get("/custos").get_data(as_text=True)


def test_valida_campos_obrigatorios(client):
    r = custo(client, recebedor="", tipo="outro")
    assert r.status_code == 400


def test_entrada_unica_por_dia(client):
    client.post("/entradas", data={"data": "2026-10-01", "valor": "500"})
    client.post("/entradas", data={"data": "2026-10-01", "valor": "700"})
    pagina = client.get("/entradas").get_data(as_text=True)
    assert "R$ 700,00" in pagina
    assert "R$ 500,00" not in pagina


def test_relatorio_saldo_e_caixa(client):
    client.post(
        "/configuracoes",
        data={"saldo_inicial": "1.000,00", "saldo_inicial_data": "2026-09-30"},
    )
    # Antes da abertura: não entra no caixa
    client.post("/entradas", data={"data": "2026-09-01", "valor": "9999"})
    # Antes do período do relatório
    client.post("/entradas", data={"data": "2026-09-30", "valor": "200"})
    custo(client, data="2026-09-30", valor="50")
    # Dentro do período
    client.post("/entradas", data={"data": "2026-10-01", "valor": "800"})
    client.post("/entradas", data={"data": "2026-10-02", "valor": "600"})
    custo(client, data="2026-10-01", valor="300")
    custo(client, data="2026-10-02", valor="150", tipo="funcionario", recebedor="João")
    # Depois do período
    custo(client, data="2026-10-05", valor="999")

    pagina = client.get("/relatorio?inicio=2026-10-01&fim=2026-10-03").get_data(
        as_text=True
    )
    # caixa anterior = 1000 + 200 - 50 = 1150
    assert "R$ 1.150,00" in pagina
    assert "R$ 1.400,00" in pagina  # entradas
    assert "R$ 450,00" in pagina  # custos
    assert "R$ 950,00" in pagina  # saldo do período
    assert "R$ 2.100,00" in pagina  # caixa final
    assert "João" in pagina
    assert "R$ 999,00" not in pagina


def test_editar_e_excluir_custo(client):
    custo(client)
    r = client.post(
        "/custos/1/editar",
        data={
            "data": "2026-10-02",
            "valor": "250,00",
            "recebedor": "Açougue",
            "forma_pagamento": "Dinheiro",
            "tipo": "despesa",
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302
    pagina = client.get("/custos").get_data(as_text=True)
    assert "Açougue" in pagina and "R$ 250,00" in pagina
    client.post("/custos/1/excluir")
    assert "Açougue" not in client.get("/custos").get_data(as_text=True)


def test_paginas_abrem(client):
    for url in ["/", "/custos", "/custos/novo", "/entradas", "/relatorio", "/configuracoes"]:
        assert client.get(url).status_code == 200


def test_relatorio_comecando_na_abertura_mostra_saldo_inicial(client):
    client.post(
        "/configuracoes",
        data={"saldo_inicial": "2.000,00", "saldo_inicial_data": "2026-10-01"},
    )
    client.post("/entradas", data={"data": "2026-10-01", "valor": "500"})
    pagina = client.get("/relatorio?inicio=2026-10-01&fim=2026-10-01").get_data(
        as_text=True
    )
    assert "R$ 2.000,00" in pagina  # caixa no início
    assert "R$ 2.500,00" in pagina  # caixa final
