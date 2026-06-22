# Passo a passo de montagem — Régua de cobrança de inadimplência (MAG Seguros + Z-API)

## 1. Visão geral

A Régua de cobrança de inadimplência é uma ferramenta **local em Python** (pacote instalável `seguros`, comando `regua`) que, uma vez por dia, puxa os inadimplentes da "Plataforma dos Produtores" da MAG Seguros (aba Vida Individual, um SPA Salesforce Lightning/Experience Cloud), gera o link de pagamento consolidado e dispara uma régua de cobrança automatizada: **WhatsApp no dia 0** (via Z-API) e **e-mail de follow-up no dia 2** (via Gmail SMTP). Ela resolve o trabalho manual e repetitivo de cobrar inadimplentes um a um, mantendo estado em SQLite (quem está em régua, o que já foi disparado, quem pagou), rastreando conversão (tempo até pagar), oferecendo um painel web local para supervisão humana e um agente conversacional inbound que entende as respostas do cliente (opt-out "SAIR", "já paguei", remarcar boleto, reenviar link). O modo padrão e seguro é **dry-run** (não envia nada nem altera a MAG); `--live` habilita os envios e a mutação reais.

## 2. Pré-requisitos

- **Python 3.11+** (testado até 3.14).
- **Google Chrome instalado** na máquina (o contexto persistente do Playwright usa `channel='chrome'`, e o login humano abre o Chrome normal).
- **Runtime do Chromium do Playwright**: `python -m playwright install chromium`.
- **Credenciais Z-API**: `ZAPI_INSTANCE_ID` e `ZAPI_TOKEN` (obrigatórias em `--live`); `ZAPI_CLIENT_TOKEN` opcional (token de segurança da conta, só vai no header `Client-Token` se preenchido).
- **Gmail com App Password** (opcional, para o e-mail do dia 2): `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` (App Password de **16 caracteres**, exige **2FA ativo** — a senha normal da conta dá erro de autenticação). Sem isso, o canal de e-mail cai em dry-run silenciosamente.
- **Para o agente inbound em produção**: URL pública (ngrok para teste / VPS ou PaaS para produção) e `ZAPI_WEBHOOK_SECRET` definido (vazio = webhook rejeita tudo, fail-closed).
- **Dependências de runtime** (via `requirements.txt`): `playwright>=1.44`, `python-dotenv>=1.0`, `requests>=2.31`, `PyYAML>=6.0`, `tzdata>=2024.1`, `fastapi>=0.110`, `uvicorn>=0.29`.
- **Dependências de dev** (via `requirements-dev.txt`): `pytest>=8.0`, `ruff>=0.5`.

Instalação base:

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # PowerShell: Copy-Item .env.example .env  (depois edite as credenciais)
```

---

## 3. Passo a passo de montagem (na ordem real de construção)

> A ordem abaixo segue a evolução real do projeto: primeiro o esqueleto seguro e testável (dry-run com conector fake), depois o conector MAG real, a mensageria, a orquestração, o rastreio de sucesso/follow-up, o dashboard, o agente inbound e, por fim, a revisão matinal automática.

### Fase 1 — Esqueleto do pacote, configuração e utilitários transversais

**O que se constrói:** a casca instalável do pacote `seguros`, a configuração via `.env` com validação fail-fast, e os utilitários sem dependências internas (tempo/timezone, logging com máscara de PII, CPF, relatório CSV).

**Arquivos criados:**
- `C:\Seguros\seguros\__init__.py` — docstring + `__version__ = "0.1.0"`.
- `C:\Seguros\pyproject.toml` — metadados de `seguros-regua`, `requires-python >=3.11`, deps, `[project.scripts] regua = seguros.cli:main`, config de ruff e pytest (`testpaths=["tests"]`, line-length 100, py311).
- `C:\Seguros\requirements.txt` e `C:\Seguros\requirements-dev.txt`.
- `C:\Seguros\seguros\cpf.py` — normalização (11 dígitos com `zfill`) e formatação de CPF (chave de junção entre as telas da MAG).
- `C:\Seguros\seguros\clock.py` — relógio injetável (`set_now_override`/`now_utc`/`now_in`), `iso_utc`/`parse_iso` (estado em UTC com sufixo `Z`), `within_send_window`, `days_since`, `parse_hhmm`.
- `C:\Seguros\seguros\logging_setup.py` — console + arquivo rotativo diário (`logs/regua.log`, 30 dias) e máscaras de PII (CPF/telefone/e-mail) para LGPD.
- `C:\Seguros\seguros\config.py` — dataclass `Config` (frozen) + `load_config`: lê o `.env`, aplica defaults das URLs MAG e **valida tudo de uma vez** (`ConfigError`), exigindo credenciais em `--live`.
- `C:\Seguros\.env.example` — template comentado de toda a configuração; copiar para `.env`.
- `C:\Seguros\seguros\report.py` — `RunReport`/`ReportRow`: acumula decisões por (cliente, canal), escreve CSV em `reports/` (com a coluna `mensagem_renderizada` = exatamente o que seria enviado) e gera resumo de console.

**Comandos:**
```bash
pip install -e .   # usa o pyproject; expõe o comando 'regua'
```

**Decisão-chave:** **dry-run é o padrão e o tempo de estado é sempre ISO-UTC com `Z`**. O `load_config` falha rápido listando TODAS as variáveis inválidas de uma vez (ex.: `PAYMENT_LINK_TTL_DAYS` inválido vira `ConfigError`, não um traceback cru — bug pego na revisão adversarial inicial). O relógio é centralizado e injetável para evitar `datetime.now()` ingênuo e tornar os testes determinísticos.

---

### Fase 2 — Domínio (modelos + máquina de estado) e persistência SQLite

**O que se constrói:** o núcleo de estado da régua — modelos de domínio, os gates de decisão **puros (sem I/O)** e toda a persistência SQLite (schema idempotente + DAOs por corretor).

**Arquivos criados:**
- `C:\Seguros\seguros\domain\__init__.py` (vazio).
- `C:\Seguros\seguros\domain\models.py` — enums (`ReguaStatus` em_regua/resolvido/opt_out, `Canal`, `Modo` dry_run/live, `Resultado`, `Acao`), dataclass `ClienteRegua` (a linha de `clientes_regua`) e `Decision` (send/skip/defer).
- `C:\Seguros\seguros\domain\state.py` — gates puros `evaluate()`/`evaluate_whatsapp()`/`evaluate_email()`, ordem: **opt_out → tem_link → destino válido → idempotência → janela de horário**; o primeiro que falha vence; fora da janela vira **DEFER** (adia, não pula).
- `C:\Seguros\seguros\db\__init__.py` (vazio).
- `C:\Seguros\seguros\db\schema.sql` — schema canônico (`IF NOT EXISTS`): `clientes_regua` (PK `corretor_id+cpf`, CHECK status), `opt_out`, `log_disparos`, `status_checks`, `inbound_messages`, `reschedule_requests`, `schema_meta`; índices e CHECKs; convenções (datetime TEXT ISO-UTC Z, bool INTEGER 0/1, dinheiro em centavos).
- `C:\Seguros\seguros\db\connection.py` — `get_conn` (Row factory, `PRAGMA foreign_keys=ON`) e `init_db` (`executescript` do schema + `_migrate` com `ALTER TABLE ADD COLUMN`).
- `C:\Seguros\seguros\db\repository.py` — DAOs escopados por `corretor_id`: `ReguaRepository` (`exists`, `insert_enrollment`, `mark_whatsapp_sent`/`mark_email_sent`, `due_for_followup`, `pending_whatsapp`, `active_cpfs`, `mark_resolved`, `due_for_recheck`, `find_cpf_by_telefone`), `OptOutRepository`, `LogRepository`, `StatusCheckRepository`, `InboundRepository`, `RescheduleRepository`.

**Comandos:**
```bash
# Sem migração manual: init_db cria/atualiza o schema de forma idempotente.
python -c "from seguros.db.connection import init_db; from seguros.db.repository import ReguaRepository; c=init_db('./regua.sqlite'); print(ReguaRepository(c,'local').all_clientes())"
```

**Decisão-chave:** **idempotência via banco**. PK `(corretor_id, cpf)` + `repo.exists()` impedem re-enroll; `whatsapp_enviado_em`/`email_enviado_em` só gravam **após envio real confirmado** (dry-run nunca consome envio). Cada método mutante dá commit próprio, então um crash no meio do loop deixa o banco consistente e o próximo run retoma de onde parou. `mark_resolved` é idempotente (a 1ª detecção de pagamento vence, não sobrescreve `resolvido_em`) e calcula `tempo_ate_pagar_horas` + `conversao_atribuida`.

---

### Fase 3 — Conector fake e a fronteira de abstração

**O que se constrói:** a **única fronteira de seguradora** (ABC + DTOs) e um conector falso com dados canned, para rodar e testar o app end-to-end sem tocar na MAG/Playwright.

**Arquivos criados:**
- `C:\Seguros\seguros\connectors\base.py` — ABC `SeguradoraConnector` (`start`/`close`/`ensure_authenticated`/`discover_delinquents`/`fetch_contact`/`generate_payment_link`/`check_status`) + DTOs (`Delinquent`, `Contact`, `ClientStatus`, `PaymentLinkResult`, `WorkStatus`, `Situacao`, `CompetenciaStatus`) e exceções (`NotAuthenticated`, `SessionExpired`, `ClientNotFound`, `PaymentLinkNotCaptured`).
- `C:\Seguros\seguros\connectors\fake.py` — `FakeConnector` com 5 CPFs canned (matriz de consentimento + um telefone fixo inválido) e parâmetro `regularizados={...}` para simular pagamento.

**Decisão-chave:** **uma única fronteira de abstração em `connectors/base.py`**: nada acima do conector importa de `connectors.mag`. O `FakeConnector` implementa a mesma ABC, permitindo dry-run/relatório/gates 100% offline — é o alvo dos testes.

---

### Fase 4 — Mensageria: telefone, WhatsApp (Z-API), e-mail (SMTP), templates, intents

**O que se constrói:** a camada de canais de saída (e a base do inbound): normalização de telefone BR, cliente/sender Z-API com anti-ban, sender SMTP Gmail, templates seguros e o classificador de intenção offline.

**Arquivos criados:**
- `C:\Seguros\seguros\messaging\__init__.py` (vazio — importe sempre dos submódulos).
- `C:\Seguros\seguros\messaging\phone.py` — `canonical_brazilian_phone` (formato Z-API `55+DDD+9+8` dígitos, reinsere o 9º dígito, valida DDD, **rejeita fixo**) e `is_plausible_email`.
- `C:\Seguros\seguros\messaging\whatsapp.py` — `ZApiClient` (base `instances/{id}/token/{token}`, header `Client-Token`, `send_text` com retry cirúrgico de "smartphone is not responding" → `GET /restart` + backoff), `ZApiSender` (healthcheck + pacing aleatório `_pace` 20-45s), `DryRunWhatsApp`, e o dataclass **`SendResult`** (compartilhado também pelo e-mail).
- `C:\Seguros\seguros\messaging\email.py` — `SmtpSender` (`smtp.gmail.com:587` STARTTLS + login, multipart texto+HTML; `SmtpAuthError` é fatal e aborta o lote de e-mail) + `DryRunEmail`.
- `C:\Seguros\seguros\messaging\templates.py` — templates `string.Template` (WhatsApp dia0/follow-up, e-mail dia2 assunto/texto/HTML, respostas do agente `RESP_*`, avisos admin `NOTIFY_ADMIN_*`) + helpers BRL/`primeiro_nome` + `render()` com `safe_substitute`.
- `C:\Seguros\seguros\messaging\intents.py` — classificador rule-based offline (precedência `SAIR > JA_PAGUEI > RESCHEDULE > NOVO_LINK > SAUDACAO > DUVIDA`) + `extrair_data_desejada` com clamp em `[hoje+1 .. hoje+max_dias]`.

**Comandos:**
```bash
python -m seguros --test-whatsapp   # checa status Z-API e envia 1 msg de teste
```

**Decisão-chave:** **anti-ban explícito + `SendResult` uniforme**. Pacing aleatório (20-45s), `delayMessage`/`delayTyping` humanos, teto diário e disjuntor por falhas consecutivas protegem o número que envia. `sent=True` só em envio real confirmado (HTTP 200 do Z-API) — base da idempotência. Templates usam `string.Template`+`safe_substitute` (não `str.format`) para não quebrar com chaves literais em URLs/nomes. O padrão Z-API reusa o que foi comprovado no projeto Aurex (referência em `C:\aurex-app\docs\aurex-aurora`). A diferença `token` (da instância) vs `client_token` (de segurança da conta) foi esclarecida nesta fase.

---

### Fase 5 — Orquestração: o loop diário de 5 passos

**O que se constrói:** o cérebro que consome conector + repositórios + senders + gates e roda a régua diária, com fronteira de erro por cliente, disjuntores e idempotência.

**Arquivos criados:**
- `C:\Seguros\seguros\orchestrator.py` — classe `Orchestrator` + `CircuitBreaker`.
- `C:\Seguros\seguros\notify.py` — `NotificationService`: avisa o corretor (ex.: sessão MAG expirou) por WhatsApp/Z-API com fallback para console+log; nunca levanta exceção.

**Os 5 passos do `orch.run()`:**
1. Autentica, checa janela de envio (`within_send_window`) e healthcheck do WhatsApp.
2. (a) Retoma WhatsApp pendente de runs anteriores (`pending_whatsapp`); (b) descobre inadimplentes → para cada novo gera link, busca contato, monta `ClienteRegua` (**persiste enrollment SÓ em live**), despacha WhatsApp dia 0.
3. Follow-up dia 2 por e-mail para quem completou `followup_offset_days` (`due_for_followup`).
4. Reconciliação: quem saiu da inadimplência vira `REGULARIZADO`.
5. Log/relatório (CSV em `reports/` + resumo no console).

**Decisão-chave:** **dry-run não persiste mutação de estado** (nem enrollment nem `mark_resolved`) — é só preview, para que o run `--live` seguinte não pule o cliente por causa de um `exists()` prematuro. A **reconciliação é pulada quando a descoberta vem vazia**, para não marcar todos como resolvidos por erro de scraping.

---

### Fase 6 — CLI, wiring, lock por PID e entrypoint

**O que se constrói:** o entrypoint único de linha de comando que faz o dispatch por modo e o wiring de tudo.

**Arquivos criados:**
- `C:\Seguros\seguros\cli.py` — `parse_args` (argparse, força UTF-8), `load_config(live=...)`, configura logging, `init_db`, **lock por PID** (`regua.sqlite.lock`, detecta/readquire lock obsoleto de processo morto), fábricas `_build_notifier`/`_build_senders` (dry-run → `DryRunWhatsApp`/`DryRunEmail`; live → `ZApiSender` + `SmtpSender` se Gmail configurado), montagem do `Orchestrator` e **códigos de saída** (0 ok, 1 falha fatal, 2 config inválida, 3 login não validou, 4 lock ocupado, 5 disjuntor, 6 seletores reprovados).
- `C:\Seguros\seguros\__main__.py` — habilita `python -m seguros`.
- `C:\Seguros\README.md` — documenta instalação, dry-run vs `--live`, login humano e operação diária.

**Decisão-chave:** **lock por PID** evita execuções concorrentes; o `_build_senders` mantém o dry-run como caminho de primeira classe e torna o Gmail opcional sem derrubar o run.

---

### Fase 7 — Conector MAG (Playwright): login humano, sessão persistente, scraping, link

**O que se constrói:** a camada que fala com o SPA Salesforce da MAG via Playwright, escondendo toda a fragilidade de DOM atrás da interface `SeguradoraConnector`. Esta foi a parte mais difícil de destravar (login com reCAPTCHA + calibração de seletores).

**Arquivos criados:**
- `C:\Seguros\seguros\connectors\mag\__init__.py` (vazio; expõe `MagConnector`).
- `C:\Seguros\seguros\connectors\mag\selectors.py` — `SelectorConfig`: carrega `selectors.yaml` e resolve chaves pontilhadas em Locators (role/label/text/placeholder/title/css; suporta frame); lista chaves-folha para o validate.
- `C:\Seguros\seguros\connectors\mag\selectors.yaml` — mapa de seletores por chave lógica (XPATH proibido — não atravessa shadow DOM Lightning).
- `C:\Seguros\seguros\connectors\mag\scraping.py` — parsing BR (`parse_brl_to_cents`, datas, competência, Sim/Não), `wait_settled` (espera o spinner, **não** `networkidle`) e `scrape_table` (virtualização + paginação + dedupe por chave).
- `C:\Seguros\seguros\connectors\mag\login_browser.py` — `login_and_capture()` (Chrome NORMAL para login humano + captura de cookies via CDP), `find_chrome`, `load_cookies`/`save_cookies`.
- `C:\Seguros\seguros\connectors\mag\session.py` — `MagSession`: `launch_persistent_context` (Chrome disfarçado, locale/timezone, esconde overlays Hotjar, grant clipboard), reinjeção de cookies, detecção de auth por URL, `goto()` guarda-costas (recupera de erro Aura/Salesforce, detecta expiração).
- `C:\Seguros\seguros\connectors\mag\links.py` — fluxo "Cobrar inadimplência" → "Gerar link de pagamento" (**única via que MUTA a MAG**) + captura do link em cascata (rede → DOM → clipboard) e filtro anti-asset/social.
- `C:\Seguros\seguros\connectors\mag\connector.py` — `MagConnector`: amarra session/scraping/links, `_open_detail_by_cpf` (match pelo **CPF formatado**), conta competências, lê "Valor inadimplente".
- `C:\Seguros\seguros\connectors\mag\inspect_mode.py` — `run_inspect` (dump read-only de HTML/screenshot/aria/elementos atravessando shadow DOM) e `validate_selectors` (confere que cada chave resolve ≥1 elemento).

**Comandos (na ordem de calibração ao vivo):**
```bash
python -m seguros --login              # login humano 1x no Chrome NORMAL; resolve reCAPTCHA e captura cookies
python -m seguros --inspect            # dumpa DOM em artifacts/inspect/<ts> para calibrar selectors.yaml
python -m seguros --validate-selectors # confere que cada chave resolve >=1 elemento ao vivo
python -m seguros --fake               # roda o app end-to-end sem MAG
python -m seguros --live --limit 1     # 1º run real que exercita o modal 'Gerar link'
```

**Decisões-chave:**
- **Login humano-no-loop SEM quebra de captcha**: o reCAPTCHA não passa em navegador controlado por CDP/Playwright (fica em loading infinito), então o `--login` abre um **Chrome normal**, o humano resolve 1x e só os **cookies** são capturados via CDP. Auto-solve de captcha e stealth/fingerprint spoofing foram **recusados explicitamente** (decisão de segurança registrada na memória).
- **Sessão persistente + reinjeção de cookies**: a MAG usa cookie de SESSÃO volátil que não sobrevive em disco; os cookies são salvos em `session_cookies.json` e reinjetados a cada run (horizonte de 7 dias).
- **Detecção de auth por URL** (independente de seletor): redirect para `identidade.mag.com.br` = deslogado; host da plataforma = logado.
- **Seletores externos por chave lógica no YAML** (nunca strings literais no código); robustez `role+name > label > text > placeholder > title > css`; **XPATH proibido**.
- **Esperas pelo spinner do Lightning, não `networkidle`** (Salesforce faz polling e nunca fica ocioso); match de linha pelo **CPF formatado** (`123.456.789-09`) para nunca cobrar o cliente errado em `--live`.

---

### Fase 8 — Reorientação de regras de negócio (sem consent-gating, base inteira)

**O que se constrói:** ajuste de regras pedido pelo usuário — remover o consent-gating por canal (o corretor já se comunica via WhatsApp), **manter o opt-out**, e popular a base **inteira** de inadimplentes (mesmo quem já está sendo trabalhado entra, no máximo já como enviado).

**Arquivos afetados:** `C:\Seguros\seguros\domain\state.py` (consentimento deixa de ser gate), `C:\Seguros\seguros\orchestrator.py` e `C:\Seguros\seguros\db\repository.py` (varrer e popular a base completa).

**Decisão-chave:** **consentimento NÃO é gate de envio**; o controle de "não contatar" passa a ser exclusivamente o **opt-out**. `autoriza_whatsapp`/`autoriza_email` continuam sendo carregados da MAG, mas não bloqueiam. A detecção de pagamento usa o sinal confiável **"Valor inadimplente"** na tela do cliente (a ausência da linha sozinha não basta).

---

### Fase 9 — Rastreio de sucesso, follow-up dia 2 e agente inbound

**O que se constrói:** métricas de conversão pós-disparo (tempo de "devendo" → "não devendo" + histórico), o follow-up automático do dia 2, e o agente conversacional inbound via webhook Z-API. (Commit `98f678f`.)

**Arquivos criados/afetados:**
- Rastreio de sucesso: `mark_resolved` (com `tempo_ate_pagar_horas`, `conversao_atribuida`, `valor_recuperado_cents` via COALESCE), `status_checks` (histórico append-only), `due_for_recheck`/`touch_check` em `C:\Seguros\seguros\db\repository.py`.
- Follow-up dia 2: `due_for_followup(offset_days=2)` (`em_regua`, `email_enviado_em IS NULL`, `enrolled_em` há ≥ offset dias) com re-check de status antes de enviar.
- Agente inbound: `C:\Seguros\seguros\messaging\intents.py` (classificação + extração de data) e o despacho por intenção no service do dashboard (ver Fase 10).

**Decisão-chave:** **`JA_PAGUEI` NUNCA confia no texto** do cliente: relê o "Valor inadimplente" ao vivo na MAG antes de confirmar (0 = pago). `primeiro_disparo_em` (setado via COALESCE no 1º toque, WhatsApp OU e-mail) é a âncora do tempo-até-pagar; `conversao_atribuida=1` só se o pagamento veio DEPOIS do disparo. Respostas críticas (opt-out/pagamento) **furam o teto diário**.

---

### Fase 10 — Dashboard/painel (FastAPI + Tailwind), webhook e worker

**O que se constrói:** o painel web local que opera a régua com supervisão humana (pipeline com estágio HOLD/aprovação), serializa toda ação MAG por um worker de thread única, e expõe o webhook público do agente inbound. (Dashboard no commit `854d872`; ajustes de UI no `b859eba`.)

**Arquivos criados:**
- `C:\Seguros\seguros\dashboard\__init__.py` (vazio).
- `C:\Seguros\seguros\dashboard\worker.py` — `ConnectorWorker`: thread única (daemon) que detém a sessão Playwright (`MagConnector`) e serializa ações MAG via fila de Futures; `status().conector_ativo`.
- `C:\Seguros\seguros\dashboard\service.py` — `DashboardService`: métricas, pipeline (`discover`/`gerar_link` em HOLD/`disparar`/`follow_up`/`reconciliar`), agente inbound (`ingest_zapi_webhook`, `processar_inbound`, `_resolver_cpf`, `_despachar_inbound`, `_agir_*`), opt-out manual, healthcheck do DOM e `DashboardError`.
- `C:\Seguros\seguros\dashboard\webhook.py` — `/webhook/zapi/health` e `POST /webhook/zapi/{secret}`: parse do `ReceivedCallback`, auth fail-closed delegada ao service, processamento em `BackgroundTasks`.
- `C:\Seguros\seguros\dashboard\app.py` — fábrica `create_app`: auth por cookie em memória, `_guard` (`DashboardError`→HTTP 400), endpoints `/api/*`, páginas estáticas e registro do webhook.
- `C:\Seguros\seguros\dashboard\static\login.html` e `C:\Seguros\seguros\dashboard\static\index.html` — login e SPA do painel (Tailwind).
- Flag `--dashboard`/`--port` e `_run_dashboard` em `C:\Seguros\seguros\cli.py`.

**Comandos:**
```bash
python -m seguros --dashboard          # painel em http://127.0.0.1:8765
python -m seguros --dashboard --port 8765
# Produção do webhook: expor SOMENTE /webhook/* via ngrok e configurar no Z-API a URL .../webhook/zapi/<ZAPI_WEBHOOK_SECRET>
```

**Decisões-chave:**
- **Worker de thread única** detém a sessão Playwright e serializa TODA ação MAG (o Playwright sync não cruza threads; endpoints `def` rodam em threadpool).
- **Pipeline com HOLD/aprovação humana**: `gerar_link` deixa o cliente em "hold"; o disparo é um passo separado e explícito (nada sai sem ação no painel).
- **Webhook fail-closed**: segredo no PATH via `secrets.compare_digest`; vazio rejeita tudo; valida `instanceId`, descarta `fromMe` (anti-loop)/grupo/sem-texto; responde 200 rápido e processa em `BackgroundTasks`.
- **Idempotência do inbound por `message_id`** (gate atômico no `InboundRepository`).
- **Auth do painel é provisória** (cookie em memória, zera a cada restart) — por isso escuta só em `127.0.0.1` e o CLI alerta para expor só `/webhook/*` com `DASHBOARD_PASSWORD` não-vazio.
- **Trava de teste `WHATSAPP_OVERRIDE_TO`**: redireciona todo WhatsApp para o número do corretor e impede o webhook real de agir sobre clientes por telefone; esvaziar é o que liga produção.

---

### Fase 11 — Revisão matinal automática do DOM + ajustes de UI

**O que se constrói:** um health-check de seletores MAG que roda 1x/dia (quando a sessão está de pé) e alerta o corretor por WhatsApp se a MAG mudou o layout; mais o alinhamento fixo dos botões da direita na UI. (Commit `b859eba`.)

**Arquivos afetados:**
- `C:\Seguros\seguros\dashboard\service.py` — `health_selectors()` (usa `validate_selectors` do `inspect_mode`, read-only, focando seletores CRÍTICOS da lista), `_healthcheck_due()`, `_run_daily_healthcheck()`, `_maybe_daily_healthcheck()` (piggyback em `discover`), `_healthcheck_loop()` (thread de fundo a cada 30 min), `_alertar_corretor()`; campo `health` exposto em `metrics()`.
- `C:\Seguros\seguros\connectors\mag\inspect_mode.py` — `validate_selectors` (read-only) como base do health-check.
- `C:\Seguros\seguros\config.py` — `HEALTHCHECK_AUTO` (default true) e `HEALTHCHECK_HORA` (default 08:00).
- `C:\Seguros\seguros\dashboard\static\index.html` — alinhamento dos botões da direita em posições e proporções fixas.

**Decisão-chave:** **3 gatilhos com trava diária compartilhada** (thread de fundo a cada 30 min só se `conector_ativo`; piggyback no `discover`; guard de 1x/dia por data). **Só marca como feito se a sessão MAG respondeu** — se a sessão estiver fora do ar, a revisão é adiada (não conserta, apenas detecta e alerta para recalibrar com `--inspect`).

---

### Fase 12 — Testes e qualidade (transversal)

**O que se constrói:** suíte pytest 100% offline (84 testes passando, ruff limpo) usando `FakeConnector` e relógio injetável.

**Arquivos criados:**
- `C:\Seguros\tests\conftest.py` — fixture `make_config` (Config em `tmp_path`, janela sempre aberta, `healthcheck_auto=False`).
- `C:\Seguros\tests\test_config.py`, `test_state.py`, `test_orchestrator.py` (end-to-end com FakeConnector + DryRun senders), `test_repository.py`, `test_templates.py`, `test_phone.py`, `test_report.py`, `test_intents.py`, `test_inbound.py` (inclui `test_healthcheck_due`), `test_session_auth.py`, `test_work_status.py`.

**Comandos:**
```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

**Decisão-chave:** **relógio injetável centralizado** (`clock.set_now_override`) em vez de `datetime.now()` torna todo teste de tempo determinístico; o `FakeConnector` implementa a mesma ABC, permitindo testar o app inteiro sem MAG/Playwright/rede.

---

## 4. Como rodar

**Dry-run (padrão e seguro)** — descobre, casa contatos, gera link em preview e renderiza as mensagens; **NÃO** envia nem altera a MAG; **NÃO** persiste enrollment:
```bash
python -m seguros
```

**Modo offline (sem MAG/Playwright)** — usa o `FakeConnector`:
```bash
python -m seguros --fake
```

**Live (para valer)** — exige `ZAPI_INSTANCE_ID`/`ZAPI_TOKEN`; gera link real (clica "Cobrar") e envia WhatsApp/e-mail. Antes do 1º live, calibre os seletores (`--login`, `--inspect`, `--validate-selectors`):
```bash
python -m seguros --live --limit 1     # primeiro teste live recomendado (1 cliente)
python -m seguros --live               # operação completa
```

**Dashboard/painel** — sobe FastAPI em `127.0.0.1` e abre o navegador:
```bash
python -m seguros --dashboard          # http://127.0.0.1:8765
```

**Utilitários:**
```bash
python -m seguros --test-whatsapp           # status Z-API + 1 msg de teste
python -m seguros --add-optout 12345678909  # adiciona CPF ao opt-out e sai
```

**Códigos de saída do `main`:** 0 ok · 1 falha fatal · 2 config inválida · 3 login não validou · 4 lock ocupado · 5 disjuntor · 6 seletores reprovados.

---

## 5. Decisões de arquitetura

- **Login humano sem captcha**: o reCAPTCHA não passa em navegador automatizado; `--login` abre um Chrome normal, o humano resolve 1x e o Playwright reusa a sessão. Auto-solve de captcha e stealth foram recusados por decisão de segurança.
- **Dry-run como padrão**: rodar sem flags só descobre/casa/renderiza, nunca clica "Cobrar" nem envia; em dry-run o enrollment **não** é persistido (senão o `exists()` faria o `--live` seguinte pular o cliente). `--live` é explícito e exige credenciais.
- **Idempotência por canal**: `*_enviado_em` é gravado SÓ após envio real confirmado; `exists()` + lock por PID evitam duplicar envios e execuções concorrentes; o inbound deduplica por `message_id`.
- **Consentimento LGPD pelo opt-out (não por gate de canal)**: o controle de "não contatar" é o opt-out; os logs mascaram CPF/telefone/e-mail; gates puros em `domain/state.py` garantem "na dúvida, não envia".
- **Sessão Playwright persistente**: `launch_persistent_context` com `user_data_dir`; como a MAG usa cookie de sessão volátil, os cookies são salvos em `session_cookies.json` e reinjetados a cada run (horizonte de 7 dias).
- **Conector MAG isolado**: toda a fragilidade de DOM fica no pacote `connectors/mag` + `selectors.yaml`; o resto do app só conhece a interface `SeguradoraConnector` e os DTOs.
- **Conector fake para testes**: o `FakeConnector` implementa a mesma ABC, permitindo rodar e testar o app end-to-end 100% offline.
- **Anti-ban e disjuntores explícitos**: teto diário de WhatsApp, pacing aleatório (20-45s), `CircuitBreaker` por `max_sends_per_run` e abort após N falhas consecutivas.
- **Estado sempre em ISO-UTC com `Z`** e relógio centralizado/injetável em `clock.py`.

---

## 6. Pendências / próximos passos conhecidos

- **Calibração ao vivo dos selectors**: o nível lista foi calibrado em 2026-06-19, mas as chaves `detail.*`/`contact.*`/`modal.*` só fecham num segundo `--inspect`, e o modal "Gerar link de pagamento" só é confirmado no 1º `--live --limit 1` (o inspect nunca clica "Cobrar" para não mutar). Recalibrar editando o YAML quando a MAG muda o layout.
- **Expiração do link de pagamento desconhecida**: o mesmo link do dia 0 é reusado no e-mail do dia 2; `PAYMENT_LINK_TTL_DAYS` existe mas fica vazio até descobrirem o prazo — **o sistema ainda não regenera link vencido automaticamente** (limitação conhecida).
- **Seam LLM de intenção não implementado**: `intent_llm.py` é citado nos docstrings, e `USAR_LLM_INTENT`/`ANTHROPIC_API_KEY` já estão preparados, mas o caminho LLM **ainda NÃO existe** no repositório (a classificação é 100% rule-based).
- **Opt-out automático via "SAIR" depende do webhook público**: por ora, sem o webhook exposto, só funciona via `--add-optout`.
- **Deploy do agente inbound**: o webhook precisa de URL pública (ngrok para teste; VPS ou PaaS como Vercel/Railway para produção); pendências de produção registradas.
- **Auth do painel é fraca/provisória** (cookie em memória, zera a cada restart): exponha apenas `/webhook/*` e defina `DASHBOARD_PASSWORD` não-vazio se receber webhooks reais.
- **Lock por PID** (`regua.sqlite.lock`): o código tenta detectar PID morto e readquirir, mas após crash/kill pode ser preciso apagar o arquivo manualmente.
- **Gmail opcional**: sem `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`, o canal de e-mail do dia 2 cai em dry-run mesmo em `--live` (apenas warning no log).
- **`_migrate` só ADICIONA colunas**: não renomeia/remove nem altera CHECKs — mudança incompatível de schema exige migração manual.
- **`mark_resolved` não roda em dry-run**: rodar sempre em dry-run nunca fecha conversão no banco.
- **Trava de teste ativa**: enquanto `WHATSAPP_OVERRIDE_TO` estiver preenchido, todo WhatsApp vai para o número do corretor e o webhook não age sobre clientes por telefone — **esvaziar é o que liga a produção**.