# 🚀 Comece aqui — guia de acesso ao projeto

Bem-vindo! Este guia te leva do zero até **rodar tudo** o que foi construído —
inclusive **sem precisar de credenciais** (modo offline). Leitura de ~5 min.

> **O que é isto, em 1 frase:** uma ferramenta **local** (roda na máquina do
> corretor) que puxa os clientes inadimplentes dos portais das seguradoras
> (**MAG** e **Prudential**), e dispara uma régua de cobrança por **WhatsApp**
> (e e-mail), com supervisão humana. Login é **humano** (nunca quebra captcha/OTP),
> e o padrão é **dry-run** (não envia nada sem você mandar).

---

## 1. Pegar o código

```bash
git clone https://github.com/saavedra88/seguros.git
cd seguros
```

> Existe também o remote `controle-seguros`; o código completo está no **`seguros`**
> (acima). Se preferir o outro, peça pro Kike fazer `git push origin main`.

## 2. Instalar (pré-requisitos)

- **Python 3.11+** (testado até 3.14)
- **Google Chrome** instalado (o login humano e a automação usam o Chrome real)

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt      # só p/ rodar os testes
python -m playwright install chromium
```

## 3. Configurar

```bash
cp .env.example .env            # Windows/PowerShell: Copy-Item .env.example .env
```

Abra o `.env` e preencha o essencial (tudo tem comentário no arquivo):

| Variável | Pra quê |
|---|---|
| `ZAPI_INSTANCE_ID`, `ZAPI_TOKEN` | WhatsApp via Z-API (obrigatório só em `--live`) |
| `NOME_CORRETOR`, `NOME_CORRETORA` | entram nas mensagens |
| `WHATSAPP_OVERRIDE_TO` | **trava de segurança** — com o **seu** número aqui, TODO WhatsApp vai pra você, nunca pro cliente. **Esvazie só em produção.** |
| `INSURER` | seguradora padrão do CLI (`mag`/`prudential`) — opcional |

> **Sem segredos no repositório.** `.env`, cookies de sessão (`.mag_session/`,
> `.prudential_session/`), `artifacts/` e o banco (`regua.sqlite`) são ignorados
> pelo git. Você cria o seu `.env` localmente.

## 4. Conferir que está tudo são — **sem tocar em nada externo**

```bash
pytest          # 96 testes, 100% offline
ruff check .    # lint limpo
python -m seguros --fake     # roda a RÉGUA INTEIRA com dados falsos (offline, sem portal/credencial)
```

O `--fake` é a melhor porta de entrada: ele descobre "inadimplentes" canned, casa
contatos, renderiza as mensagens que **seriam** enviadas e gera um CSV em
`reports/` — **sem MAG, sem Prudential, sem enviar nada**.

## 5. Ver o painel web

```bash
python -m seguros --dashboard          # abre http://127.0.0.1:8765
```

Na tela de login você **escolhe a seguradora** (MAG ou Prudential). Se
`DASHBOARD_PASSWORD` estiver vazio, qualquer senha entra (login provisório local).
O painel mostra o funil, KPIs, lista de clientes, conversas (agente inbound) e
auditoria.

## 6. As duas seguradoras

Cada uma exige **login humano** (decisão de projeto: captcha/OTP nunca automatizado).

### MAG (Salesforce / Plataforma dos Produtores)
```bash
python -m seguros --login        # login humano 1x (resolve o captcha no Chrome)
python -m seguros                # dry-run: descobre, casa, renderiza (NÃO envia)
python -m seguros --live         # envia de verdade (respeitando a trava de teste)
```

### Prudential (Life Planner / ASP.NET)
Os tokens da Prudential **expiram em minutos**, então login e operação são na
**mesma janela**:
```bash
python -m seguros --insurer prudential    # abre o Chrome -> você loga na janela (OTP)
                                          # -> ENTER no terminal -> descobre na sessão viva
python -m seguros --insurer prudential --live   # idem, enviando
```
No **painel**: escolha Prudential → **Descobrir** → logue na janela que abrir → o
painel detecta o login sozinho (polling) e segue.

> A Prudential é chaveada por **Apólice** (não CPF); telefone e valor vêm na
> própria grade do "Relatório de Atraso". Já está **calibrada e verificada**
> (extrai 32 apólices reais). Como ela não tem link de pagamento, a régua opera em
> **modo lembrete** (sem link).

## 7. Modelo de segurança (importante)

- **Dry-run é o padrão.** Sem `--live`, nada é enviado nem alterado no portal.
- **Trava `WHATSAPP_OVERRIDE_TO`:** enquanto tem um número, todo WhatsApp vai pra
  ele (seu número de teste). Esvaziar é o que "liga" produção.
- **Login humano sempre** — nada de quebrar captcha/OTP ou stealth.
- **Tudo local** — os dados ficam na máquina (SQLite), não há nuvem.

## 8. Mapa do que ler / onde está cada coisa

| Arquivo | O que é |
|---|---|
| [README.md](../README.md) | Visão geral + **handover** (arquitetura, multi-seguradora, operação) |
| [docs/MONTAGEM-PASSO-A-PASSO.md](MONTAGEM-PASSO-A-PASSO.md) | Como foi **construído**, fase a fase (13 fases) |
| [docs/prompt-inicial.md](prompt-inicial.md) | O **briefing original** que iniciou o projeto |
| `seguros/cli.py` | Entrypoint / comandos |
| `seguros/orchestrator.py` | O loop diário da régua (5 passos) |
| `seguros/connectors/` | `base.py` (fronteira), `mag/`, `prudential/`, `factory.py`, `fake.py` |
| `seguros/dashboard/` | Painel web (FastAPI): `app`, `service`, `worker`, `webhook`, `static/` |
| `seguros/messaging/` | WhatsApp (Z-API), e-mail, telefone, templates, intents (agente inbound) |
| `seguros/db/` · `seguros/domain/` | Persistência (SQLite) · modelos e gates puros |
| `tests/` | pytest (offline) |

## 9. Estado atual (junho/2026)

- **MAG** — régua ponta a ponta: descoberta, geração de link, dia 0 (WhatsApp),
  dia 2 (e-mail), detecção de pagamento, dashboard, agente inbound. Os seletores de
  detalhe podem precisar de recalibração ao vivo (`--inspect`) se a MAG mudar o DOM.
- **Prudential** — conector **calibrado e verificado**; régua em **modo lembrete**.
  Pendência conhecida: no painel, o botão por cliente ainda é "Gerar link" (deveria
  ser "Disparar", já que é lembrete) — operar pelo CLI por enquanto.

## 10. FAQ rápido

- **"Não tenho login da MAG/Prudential."** → Use `python -m seguros --fake` e o
  `--dashboard`. Você vê a régua inteira funcionando offline, com dados de exemplo.
- **"Tenho medo de mandar mensagem pra cliente."** → Deixe `WHATSAPP_OVERRIDE_TO`
  preenchido com o seu número e/ou só use dry-run (sem `--live`). Nada vaza.
- **"O painel pediu pra logar numa janela e travou."** → É o login da seguradora;
  faça o login no Chrome que abriu — o painel segue sozinho ao cair na plataforma.
