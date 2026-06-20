# Régua de cobrança de inadimplência — MAG Seguros + Z-API

Ferramenta **local** (1 corretor, 1 máquina) que, 1x por dia:

1. faz login na **Plataforma dos Produtores da MAG** (Salesforce Experience Cloud);
2. descobre os **inadimplentes** (aba *Clientes VI* / Vida Individual);
3. gera **1 link de pagamento consolidado** por cliente;
4. dispara uma **régua fixa**: **Dia 0 → WhatsApp + link**; **Dia 2 → e-mail + link** (se ainda em aberto); depois, nada.

Respeita **consentimento por canal** (LGPD/CDC) e **opt-out**. Por padrão roda em **DRY-RUN** (não envia nada nem altera a MAG).

> ⚠️ **Captcha é humano.** Não há quebra automática de captcha. O reCAPTCHA **não passa** num navegador automatizado (fica girando pra sempre), então o `--login` abre um **Chrome normal** apontado para o mesmo perfil: você resolve o captcha **uma vez**, fecha o Chrome, e o Playwright reusa a sessão salva (as telas internas da plataforma não têm captcha). A sessão persiste por dias/semanas; quando expira, a ferramenta avisa você para rodar `--login` de novo.

---

## Pré-requisitos

- **Python 3.11+** (testado em 3.14).
- **Google Chrome** instalado (a automação usa o canal `chrome`).
- **App Password do Gmail** (16 caracteres) — exige **verificação em 2 etapas** ativa na conta Google:
  `Conta Google → Segurança → Verificação em duas etapas → Senhas de app`.
- **Credenciais Z-API** (instância conectada a um número de WhatsApp): `ZAPI_INSTANCE_ID`, `ZAPI_TOKEN`, `ZAPI_CLIENT_TOKEN`.

## Instalação

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env      # depois edite o .env com suas credenciais
```

(No Windows/PowerShell: `Copy-Item .env.example .env`.)

## Configuração

Edite o `.env` (veja `.env.example` para a lista completa e comentada). Nunca commite o `.env`.

## Uso

```bash
# DRY-RUN (padrão e seguro): descobre, casa contatos, checa consentimento e
# RENDERIZA as mensagens que enviaria — sem clicar "Cobrar" e sem enviar nada.
python -m seguros

# Pré-autenticar (resolver login + captcha uma vez; a sessão fica salva).
# Abre um Chrome NORMAL (sem automação) — o reCAPTCHA só funciona nele.
# Faça o login, espere cair na plataforma e FECHE o Chrome para salvar a sessão.
python -m seguros --login

# Calibrar os seletores da MAG (1ª vez) — abre o Playwright Inspector
python -m seguros --inspect
# Conferir se os seletores resolvem (após calibrar)
python -m seguros --validate-selectors

# PARA VALER: gera link real (clica Cobrar) e envia WhatsApp/e-mail
python -m seguros --live
# Primeiro teste live recomendado: 1 cliente só
python -m seguros --live --limit 1

# Adicionar um CPF ao opt-out (nunca mais contatado)
python -m seguros --add-optout 12345678909

# Rodar offline com dados fake (sem MAG/Playwright) — útil p/ demo e testes
python -m seguros --fake

# Testar a conexão Z-API (envia 1 msg ao número de teste)
python -m seguros --test-whatsapp

# PAINEL WEB (dashboard do gestor) — abre em http://127.0.0.1:8765
python -m seguros --dashboard
```

## Dashboard (painel do gestor)

`python -m seguros --dashboard` sobe um painel local (FastAPI) e abre o navegador.
Tem **tela de login** (futurista) — senha em `DASHBOARD_PASSWORD` (vazio = provisório,
qualquer senha entra). Ele é os "olhos do gestor" e controla a **jornada de cada
cliente** em etapas:

- **KPIs + gráficos**: valor em aberto, recuperado, taxa de resolução, funil do
  pipeline, consentimento, valor por etapa.
- **Pipeline com HOLD**: cada cliente passa por `Descoberto → Link gerado (HOLD) → Disparado → Resolvido`.
  - **Descobrir**: lê os inadimplentes e enfileira (sem cobrar/enviar).
  - **Gerar link (HOLD)**: clica "Cobrar" na MAG, captura o link e **segura** (não envia).
  - **Disparar**: envia o WhatsApp para o **destino mostrado ao lado** do cliente.
- **Número de teste**: campo no topo. Se preenchido, **todo disparo vai para esse número**
  (o seu), nunca para o cliente — a coluna "Destino" mostra exatamente para onde vai.
  Esvazie para enviar ao cliente real (produção).
- **Auditoria**: aba com o histórico de disparos.
- Opt-out por cliente direto na tabela.

> O painel mantém o Chrome da MAG aberto (você vê a automação acontecendo). Requer
> sessão válida (`--login` se expirar).

O run gera um **CSV** em `reports/` (com a coluna `mensagem_renderizada` = exatamente o
que seria enviado) e um **resumo no console**.

## Operação diária (sugerida)

1. De manhã, abra a plataforma MAG e faça login (marque "manter-me conectado" se houver).
   Ou rode `python -m seguros --login` uma vez.
2. Rode `python -m seguros` (dry-run) e confira o CSV.
3. Quando estiver confortável, rode `python -m seguros --live`.
4. Opcional: agende o passo 3 no **Agendador de Tarefas do Windows** para logo após seu login matinal.

Enquanto a sessão MAG estiver viva, a régua roda sem você clicar nada. Quando expirar,
você recebe um aviso (WhatsApp no seu próprio número, se `NOTIFY_WHATSAPP_TO` estiver setado).

## Calibração dos seletores (importante)

A MAG é um SPA Salesforce cujo DOM exato só conhecemos rodando no site real. Todos os
seletores ficam em `seguros/connectors/mag/selectors.yaml`. No primeiro setup:

1. `python -m seguros --login`
2. `python -m seguros --inspect` → use o Inspector e os dumps em `artifacts/inspect/` para
   ajustar `selectors.yaml`.
3. `python -m seguros --validate-selectors` → todas as chaves devem passar.

Recalibrar depois de uma atualização da MAG = editar o YAML, sem mexer no código.

## Segurança e LGPD

- Só contata quem **autorizou o canal** na MAG e **não está em opt-out**.
- WhatsApp tem teto diário (`MAX_WHATSAPP_POR_DIA`) e *pacing* aleatório (anti-ban).
- CPF/telefone são **mascarados** nos logs. O banco (`regua.sqlite`) e a sessão
  (`.mag_session/`) são **locais** e gitignored — os dados dos seus clientes ficam na sua máquina.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

## Limitações conhecidas (TODO)

- **Expiração do link de pagamento** é desconhecida. O mesmo link do dia 0 é reusado no
  e-mail do dia 2. Se descobrir o prazo, preencha `PAYMENT_LINK_TTL_DAYS` (regenera links velhos).
- **Opt-out automático via "SAIR"** não está ligado (precisa de URL pública / webhook Z-API).
  Por ora, adicione opt-outs com `--add-optout`.

## Estrutura

```
seguros/
  cli.py · config.py · clock.py · logging_setup.py · notify.py · orchestrator.py · report.py · cpf.py
  db/         → schema.sql, connection.py, repository.py (DAO; único lugar com SQL)
  domain/     → models.py (entidades/enums), state.py (gates puros)
  messaging/  → phone.py, templates.py, whatsapp.py (Z-API), email.py (SMTP)
  connectors/
    base.py   → SeguradoraConnector (ABC) + DTOs  ← única fronteira de abstração
    fake.py   → FakeConnector (offline)
    mag/      → connector.py, session.py, scraping.py, links.py, selectors.py/.yaml, inspect_mode.py
```
