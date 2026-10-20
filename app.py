"""Prestação de Contas - registro de custos e entradas de um restaurante."""

import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

FORMAS_PAGAMENTO = [
    "Dinheiro",
    "PIX",
    "Cartão de débito",
    "Cartão de crédito",
    "Transferência bancária",
    "Boleto",
    "Cheque",
]

TIPOS_CUSTO = {
    "despesa": "Despesa",
    "funcionario": "Pagamento de funcionário",
}

EXTENSOES_PERMITIDAS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS custos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL,
    valor_centavos INTEGER NOT NULL,
    recebedor TEXT NOT NULL,
    forma_pagamento TEXT NOT NULL,
    tipo TEXT NOT NULL,
    descricao TEXT,
    comprovante TEXT,
    comprovante_nome_original TEXT,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_custos_data ON custos (data);

CREATE TABLE IF NOT EXISTS entradas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL UNIQUE,
    valor_centavos INTEGER NOT NULL,
    observacao TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracoes (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Utilitários de formatação
# ---------------------------------------------------------------------------


def parse_valor(texto, positivo=True):
    """Converte '1.234,56', '1234.56' ou '1234' em centavos (int).

    Com positivo=False aceita zero e valores negativos (ex.: saldo inicial).
    """
    if texto is None:
        raise ValueError("Valor não informado.")
    texto = texto.strip().replace("R$", "").replace(" ", "")
    if not texto:
        raise ValueError("Valor não informado.")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ValueError("Valor inválido. Use o formato 1.234,56.")
    if positivo and valor <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    return int((valor * 100).quantize(Decimal("1")))


def parse_data(texto):
    try:
        return datetime.strptime((texto or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Data inválida.")


def formatar_moeda(centavos):
    centavos = centavos or 0
    sinal = "-" if centavos < 0 else ""
    centavos = abs(centavos)
    inteiro, resto = divmod(centavos, 100)
    inteiro_fmt = f"{inteiro:,}".replace(",", ".")
    return f"{sinal}R$ {inteiro_fmt},{resto:02d}"


def formatar_data(valor):
    if isinstance(valor, str):
        valor = datetime.strptime(valor, "%Y-%m-%d").date()
    return valor.strftime("%d/%m/%Y")


def centavos_para_input(centavos):
    sinal = "-" if centavos < 0 else ""
    inteiro, resto = divmod(abs(centavos), 100)
    return f"{sinal}{inteiro},{resto:02d}"


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "troque-esta-chave"),
        DATABASE=os.path.join(app.instance_path, "prestacao.db"),
        UPLOAD_FOLDER=os.path.join(app.instance_path, "comprovantes"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 20 MB por envio
    )
    if config:
        app.config.update(config)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.jinja_env.filters["moeda"] = formatar_moeda
    app.jinja_env.filters["data_br"] = formatar_data
    app.jinja_env.filters["valor_input"] = centavos_para_input
    app.jinja_env.globals["TIPOS_CUSTO"] = TIPOS_CUSTO
    app.jinja_env.globals["FORMAS_PAGAMENTO"] = FORMAS_PAGAMENTO

    # -- banco de dados ------------------------------------------------------

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        get_db().executescript(SCHEMA)

    def get_config(chave, padrao=None):
        row = get_db().execute(
            "SELECT valor FROM configuracoes WHERE chave = ?", (chave,)
        ).fetchone()
        return row["valor"] if row else padrao

    def set_config(chave, valor):
        db = get_db()
        db.execute(
            "INSERT INTO configuracoes (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, valor),
        )
        db.commit()

    def saldo_inicial():
        """Saldo de caixa na data de abertura (configurado pelo gerente)."""
        return int(get_config("saldo_inicial_centavos", "0")), get_config(
            "saldo_inicial_data"
        )

    def caixa_ate(data_limite):
        """Caixa acumulado até o fim do dia `data_limite` (inclusive).

        O saldo inicial é o caixa existente antes do dia de abertura; lançamentos
        anteriores à abertura são ignorados.
        """
        db = get_db()
        inicial, data_abertura = saldo_inicial()
        params = [data_limite.isoformat()]
        filtro_abertura = ""
        if data_abertura:
            filtro_abertura = " AND data >= ?"
            params.append(data_abertura)
            if data_limite.isoformat() < data_abertura:
                return inicial
        entradas = db.execute(
            "SELECT COALESCE(SUM(valor_centavos), 0) FROM entradas WHERE data <= ?"
            + filtro_abertura,
            params,
        ).fetchone()[0]
        custos = db.execute(
            "SELECT COALESCE(SUM(valor_centavos), 0) FROM custos WHERE data <= ?"
            + filtro_abertura,
            params,
        ).fetchone()[0]
        return inicial + entradas - custos

    # -- comprovantes --------------------------------------------------------

    def salvar_comprovante(arquivo):
        if not arquivo or not arquivo.filename:
            return None, None
        nome_original = os.path.basename(arquivo.filename)
        ext = nome_original.rsplit(".", 1)[-1].lower() if "." in nome_original else ""
        if ext not in EXTENSOES_PERMITIDAS:
            raise ValueError(
                "Comprovante deve ser imagem (PNG, JPG, GIF, WEBP) ou PDF."
            )
        nome_salvo = f"{uuid.uuid4().hex}.{ext}"
        arquivo.save(os.path.join(app.config["UPLOAD_FOLDER"], nome_salvo))
        return nome_salvo, nome_original

    def remover_comprovante(nome):
        if nome:
            caminho = os.path.join(app.config["UPLOAD_FOLDER"], nome)
            if os.path.exists(caminho):
                os.remove(caminho)

    @app.route("/comprovantes/<path:nome>")
    def comprovante(nome):
        return send_from_directory(app.config["UPLOAD_FOLDER"], nome)

    # -- painel --------------------------------------------------------------

    @app.route("/")
    def index():
        db = get_db()
        hoje = date.today()
        inicio_mes = hoje.replace(day=1)
        entradas_mes = db.execute(
            "SELECT COALESCE(SUM(valor_centavos), 0) FROM entradas "
            "WHERE data BETWEEN ? AND ?",
            (inicio_mes.isoformat(), hoje.isoformat()),
        ).fetchone()[0]
        custos_mes = db.execute(
            "SELECT COALESCE(SUM(valor_centavos), 0) FROM custos "
            "WHERE data BETWEEN ? AND ?",
            (inicio_mes.isoformat(), hoje.isoformat()),
        ).fetchone()[0]
        entrada_hoje = db.execute(
            "SELECT * FROM entradas WHERE data = ?", (hoje.isoformat(),)
        ).fetchone()
        ultimos_custos = db.execute(
            "SELECT * FROM custos ORDER BY data DESC, id DESC LIMIT 8"
        ).fetchall()
        return render_template(
            "index.html",
            hoje=hoje,
            entradas_mes=entradas_mes,
            custos_mes=custos_mes,
            caixa_atual=caixa_ate(hoje),
            entrada_hoje=entrada_hoje,
            ultimos_custos=ultimos_custos,
        )

    # -- custos --------------------------------------------------------------

    @app.route("/custos")
    def custos():
        db = get_db()
        inicio = request.args.get("inicio") or ""
        fim = request.args.get("fim") or ""
        tipo = request.args.get("tipo") or ""
        sql = "SELECT * FROM custos WHERE 1=1"
        params = []
        if inicio:
            sql += " AND data >= ?"
            params.append(inicio)
        if fim:
            sql += " AND data <= ?"
            params.append(fim)
        if tipo in TIPOS_CUSTO:
            sql += " AND tipo = ?"
            params.append(tipo)
        sql += " ORDER BY data DESC, id DESC"
        lista = db.execute(sql, params).fetchall()
        total = sum(c["valor_centavos"] for c in lista)
        return render_template(
            "custos.html", custos=lista, total=total, inicio=inicio, fim=fim, tipo=tipo
        )

    def ler_form_custo():
        dados = {
            "data": request.form.get("data", ""),
            "valor": request.form.get("valor", ""),
            "recebedor": request.form.get("recebedor", "").strip(),
            "forma_pagamento": request.form.get("forma_pagamento", ""),
            "tipo": request.form.get("tipo", ""),
            "descricao": request.form.get("descricao", "").strip(),
        }
        erros = []
        try:
            dados["data_obj"] = parse_data(dados["data"])
        except ValueError as e:
            erros.append(str(e))
        try:
            dados["valor_centavos"] = parse_valor(dados["valor"])
        except ValueError as e:
            erros.append(str(e))
        if not dados["recebedor"]:
            erros.append("Informe o recebedor.")
        if dados["forma_pagamento"] not in FORMAS_PAGAMENTO:
            erros.append("Selecione a forma de pagamento.")
        if dados["tipo"] not in TIPOS_CUSTO:
            erros.append("Selecione se é despesa ou pagamento de funcionário.")
        return dados, erros

    @app.route("/custos/novo", methods=["GET", "POST"])
    def novo_custo():
        if request.method == "POST":
            dados, erros = ler_form_custo()
            nome_salvo = nome_original = None
            if not erros:
                try:
                    nome_salvo, nome_original = salvar_comprovante(
                        request.files.get("comprovante")
                    )
                except ValueError as e:
                    erros.append(str(e))
            if erros:
                for e in erros:
                    flash(e, "erro")
                return render_template("custo_form.html", form=dados, custo=None), 400
            db = get_db()
            db.execute(
                "INSERT INTO custos (data, valor_centavos, recebedor, forma_pagamento, "
                "tipo, descricao, comprovante, comprovante_nome_original, criado_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dados["data_obj"].isoformat(),
                    dados["valor_centavos"],
                    dados["recebedor"],
                    dados["forma_pagamento"],
                    dados["tipo"],
                    dados["descricao"] or None,
                    nome_salvo,
                    nome_original,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            db.commit()
            flash("Custo registrado com sucesso.", "ok")
            if request.form.get("continuar"):
                return redirect(url_for("novo_custo"))
            return redirect(url_for("custos"))
        form = {"data": date.today().isoformat()}
        return render_template("custo_form.html", form=form, custo=None)

    def buscar_custo(custo_id):
        custo = get_db().execute(
            "SELECT * FROM custos WHERE id = ?", (custo_id,)
        ).fetchone()
        if custo is None:
            abort(404)
        return custo

    @app.route("/custos/<int:custo_id>/editar", methods=["GET", "POST"])
    def editar_custo(custo_id):
        custo = buscar_custo(custo_id)
        if request.method == "POST":
            dados, erros = ler_form_custo()
            nome_salvo, nome_original = (
                custo["comprovante"],
                custo["comprovante_nome_original"],
            )
            if not erros:
                try:
                    novo, novo_original = salvar_comprovante(
                        request.files.get("comprovante")
                    )
                except ValueError as e:
                    erros.append(str(e))
                else:
                    if novo:
                        remover_comprovante(custo["comprovante"])
                        nome_salvo, nome_original = novo, novo_original
                    elif request.form.get("remover_comprovante"):
                        remover_comprovante(custo["comprovante"])
                        nome_salvo = nome_original = None
            if erros:
                for e in erros:
                    flash(e, "erro")
                return (
                    render_template("custo_form.html", form=dados, custo=custo),
                    400,
                )
            db = get_db()
            db.execute(
                "UPDATE custos SET data = ?, valor_centavos = ?, recebedor = ?, "
                "forma_pagamento = ?, tipo = ?, descricao = ?, comprovante = ?, "
                "comprovante_nome_original = ? WHERE id = ?",
                (
                    dados["data_obj"].isoformat(),
                    dados["valor_centavos"],
                    dados["recebedor"],
                    dados["forma_pagamento"],
                    dados["tipo"],
                    dados["descricao"] or None,
                    nome_salvo,
                    nome_original,
                    custo_id,
                ),
            )
            db.commit()
            flash("Custo atualizado.", "ok")
            return redirect(url_for("custos"))
        form = dict(custo)
        form["valor"] = centavos_para_input(custo["valor_centavos"])
        return render_template("custo_form.html", form=form, custo=custo)

    @app.route("/custos/<int:custo_id>/excluir", methods=["POST"])
    def excluir_custo(custo_id):
        custo = buscar_custo(custo_id)
        db = get_db()
        db.execute("DELETE FROM custos WHERE id = ?", (custo_id,))
        db.commit()
        remover_comprovante(custo["comprovante"])
        flash("Custo excluído.", "ok")
        return redirect(request.form.get("voltar") or url_for("custos"))

    # -- entradas (valor apurado do dia) -------------------------------------

    @app.route("/entradas", methods=["GET", "POST"])
    def entradas():
        db = get_db()
        if request.method == "POST":
            erros = []
            data_txt = request.form.get("data", "")
            valor_txt = request.form.get("valor", "")
            observacao = request.form.get("observacao", "").strip() or None
            try:
                data_obj = parse_data(data_txt)
            except ValueError as e:
                erros.append(str(e))
            try:
                valor = parse_valor(valor_txt)
            except ValueError as e:
                erros.append(str(e))
            if erros:
                for e in erros:
                    flash(e, "erro")
            else:
                existente = db.execute(
                    "SELECT id FROM entradas WHERE data = ?", (data_obj.isoformat(),)
                ).fetchone()
                if existente:
                    db.execute(
                        "UPDATE entradas SET valor_centavos = ?, observacao = ? "
                        "WHERE id = ?",
                        (valor, observacao, existente["id"]),
                    )
                    flash(
                        f"Valor apurado de {formatar_data(data_obj)} atualizado.", "ok"
                    )
                else:
                    db.execute(
                        "INSERT INTO entradas (data, valor_centavos, observacao, "
                        "criado_em) VALUES (?, ?, ?, ?)",
                        (
                            data_obj.isoformat(),
                            valor,
                            observacao,
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                    flash(
                        f"Valor apurado de {formatar_data(data_obj)} registrado.", "ok"
                    )
                db.commit()
                return redirect(url_for("entradas"))

        editar_id = request.args.get("editar", type=int)
        form = {"data": date.today().isoformat()}
        if editar_id:
            e = db.execute("SELECT * FROM entradas WHERE id = ?", (editar_id,)).fetchone()
            if e:
                form = {
                    "data": e["data"],
                    "valor": centavos_para_input(e["valor_centavos"]),
                    "observacao": e["observacao"] or "",
                }
        lista = db.execute(
            "SELECT * FROM entradas ORDER BY data DESC LIMIT 60"
        ).fetchall()
        return render_template("entradas.html", entradas=lista, form=form)

    @app.route("/entradas/<int:entrada_id>/excluir", methods=["POST"])
    def excluir_entrada(entrada_id):
        db = get_db()
        db.execute("DELETE FROM entradas WHERE id = ?", (entrada_id,))
        db.commit()
        flash("Entrada excluída.", "ok")
        return redirect(url_for("entradas"))

    # -- relatório -----------------------------------------------------------

    @app.route("/relatorio")
    def relatorio():
        inicio_txt = request.args.get("inicio")
        fim_txt = request.args.get("fim")
        if not inicio_txt or not fim_txt:
            hoje = date.today()
            return render_template(
                "relatorio.html",
                gerado=False,
                inicio=hoje.replace(day=1).isoformat(),
                fim=hoje.isoformat(),
            )
        try:
            inicio = parse_data(inicio_txt)
            fim = parse_data(fim_txt)
        except ValueError:
            flash("Datas inválidas.", "erro")
            return redirect(url_for("relatorio"))
        if fim < inicio:
            flash("A data final deve ser igual ou posterior à data inicial.", "erro")
            return redirect(url_for("relatorio"))

        db = get_db()
        periodo = (inicio.isoformat(), fim.isoformat())
        lista_custos = db.execute(
            "SELECT * FROM custos WHERE data BETWEEN ? AND ? ORDER BY data, id",
            periodo,
        ).fetchall()
        lista_entradas = db.execute(
            "SELECT * FROM entradas WHERE data BETWEEN ? AND ? ORDER BY data",
            periodo,
        ).fetchall()

        entradas_por_dia = {e["data"]: e["valor_centavos"] for e in lista_entradas}
        custos_por_dia = {}
        for c in lista_custos:
            custos_por_dia[c["data"]] = custos_por_dia.get(c["data"], 0) + c[
                "valor_centavos"
            ]

        caixa_anterior = caixa_ate(inicio - timedelta(days=1))
        data_abertura = saldo_inicial()[1]
        dias = []
        acumulado = caixa_anterior
        d = inicio
        while d <= fim:
            chave = d.isoformat()
            ent = entradas_por_dia.get(chave, 0)
            cus = custos_por_dia.get(chave, 0)
            if not data_abertura or chave >= data_abertura:
                acumulado += ent - cus
            dias.append(
                {
                    "data": chave,
                    "entrada": ent,
                    "tem_entrada": chave in entradas_por_dia,
                    "custos": cus,
                    "saldo": ent - cus,
                    "caixa": acumulado,
                }
            )
            d += timedelta(days=1)

        total_entradas = sum(entradas_por_dia.values())
        total_custos = sum(custos_por_dia.values())
        total_despesas = sum(
            c["valor_centavos"] for c in lista_custos if c["tipo"] == "despesa"
        )
        total_funcionarios = total_custos - total_despesas
        por_forma = {}
        for c in lista_custos:
            por_forma[c["forma_pagamento"]] = (
                por_forma.get(c["forma_pagamento"], 0) + c["valor_centavos"]
            )

        return render_template(
            "relatorio.html",
            gerado=True,
            inicio=inicio.isoformat(),
            fim=fim.isoformat(),
            custos=lista_custos,
            dias=dias,
            total_entradas=total_entradas,
            total_custos=total_custos,
            total_despesas=total_despesas,
            total_funcionarios=total_funcionarios,
            por_forma=sorted(por_forma.items(), key=lambda x: -x[1]),
            saldo_periodo=total_entradas - total_custos,
            caixa_anterior=caixa_anterior,
            caixa_final=acumulado,
            gerado_em=datetime.now(),
        )

    # -- configurações -------------------------------------------------------

    @app.route("/configuracoes", methods=["GET", "POST"])
    def configuracoes():
        if request.method == "POST":
            erros = []
            valor_txt = request.form.get("saldo_inicial", "").strip() or "0"
            data_txt = request.form.get("saldo_inicial_data", "").strip()
            try:
                valor = parse_valor(valor_txt, positivo=False)
            except ValueError as e:
                erros.append(str(e))
            if data_txt:
                try:
                    parse_data(data_txt)
                except ValueError as e:
                    erros.append(str(e))
            if erros:
                for e in erros:
                    flash(e, "erro")
            else:
                set_config("saldo_inicial_centavos", str(valor))
                set_config("saldo_inicial_data", data_txt)
                flash("Configurações salvas.", "ok")
                return redirect(url_for("configuracoes"))
        inicial, data_abertura = saldo_inicial()
        return render_template(
            "configuracoes.html",
            saldo_inicial=centavos_para_input(inicial),
            saldo_inicial_data=data_abertura or "",
        )

    @app.errorhandler(413)
    def arquivo_grande(e):
        flash("Arquivo muito grande. O limite é 20 MB.", "erro")
        return redirect(request.referrer or url_for("index"))

    return app


app = create_app()

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    print(f"\n  Prestação de Contas rodando em: http://localhost:{porta}\n")
    app.run(host="0.0.0.0", port=porta, debug=False)
