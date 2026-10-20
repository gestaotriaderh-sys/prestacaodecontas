# Prestação de Contas — Restaurante

Programa para o gerente registrar os custos do restaurante, lançar o valor apurado
de cada dia e gerar relatórios de prestação de contas por período.

Funciona no navegador, mas roda no próprio computador (os dados ficam salvos
localmente, na pasta `instance/`).

## Funcionalidades

- **Custos**: valor, recebedor, data, forma de pagamento (dinheiro, PIX, cartão,
  transferência, boleto, cheque), tipo (*despesa* ou *pagamento de funcionário*),
  descrição e comprovante anexado (imagem ou PDF, até 20 MB). Os custos podem ser
  filtrados, editados e excluídos.
- **Valor apurado**: um lançamento por dia com o total de entradas.
- **Relatório**: escolha a data de início e de fim e veja
  - todos os custos do período (com link para cada comprovante);
  - a entrada, os custos, o saldo e o caixa de cada dia;
  - o total de entradas, o total de custos (separado entre despesas e funcionários)
    e o **saldo do período**;
  - o **caixa ao final do período**.

  O botão *Imprimir / salvar PDF* gera uma versão para impressão.
- **Configurações**: saldo inicial de caixa e data de abertura. O caixa é calculado
  como `saldo inicial + entradas − custos`, a partir da data de abertura.

## Como usar

Requer [Python 3.10+](https://www.python.org/downloads/) (no Windows, marque
*"Add Python to PATH"* na instalação).

- **Windows**: dê dois cliques em `iniciar.bat`. Na primeira vez ele instala o que
  for necessário; depois abre o navegador em <http://localhost:5000>.
- **Linux/macOS**: execute `./iniciar.sh` e abra <http://localhost:5000>.

Manualmente:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip ...
.venv/bin/python app.py
```

Na primeira utilização, vá em **Configurações** e informe o saldo de caixa atual.

> O programa fica acessível a outros aparelhos da mesma rede (ex.: celular do gerente)
> pelo endereço `http://IP-DO-COMPUTADOR:5000`. Ele não tem login — não o exponha à internet.

## Backup

Todos os dados estão na pasta `instance/`:
`instance/prestacao.db` (lançamentos) e `instance/comprovantes/` (arquivos anexados).
Copie essa pasta regularmente para um pendrive ou nuvem.

## Testes

```bash
pip install pytest
python -m pytest
```
