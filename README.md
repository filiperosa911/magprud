# Régua de cobrança de inadimplência — Seguros (MAG + Z-API)

Ferramenta **local** para corretor de seguros automatizar a cobrança de clientes
inadimplentes. Hoje integra a **MAG Seguros** (Plataforma dos Produtores) ponta a
ponta; a **Prudential** (Life Planner) está em exploração (ver
[Multi-seguradora](#multi-seguradora-prudential-em-andamento)).

O que ela faz, por cliente, numa **régua de cobrança**:

1. **Descobre** os inadimplentes na plataforma da seguradora;
2. **Gera o link de pagamento** consolidado (clicando "Cobrar" na MAG);
3. **Dia 0:** dispara **WhatsApp + link**;
4. **Dia 2:** **follow-up sutil** (re-checando antes se o cliente já pagou);
5. **Detecta o pagamento**, mede tempo-até-pagar e taxa de conversão;
6. **Responde o cliente** quando ele reage no WhatsApp (opt-out, "já paguei",
   "quero remarcar", dúvida) — o **agente inbound**.

Tem um **painel web** (dashboard) que controla a jornada com etapa de **HOLD**
(gera o link mas só dispara quando você manda), métricas, caixa de conversas e
auditoria. Por padrão roda em **DRY-RUN** (não envia nada nem altera a seguradora).

> 🤝 **Este README é um handover.** Tem tudo pra você clonar, configurar e rodar —
> incluindo o caminho para plugar o agente de respostas no **N8n** (ver
> [Integração com N8n](#integração-com-n8n-agente-de-respostas)).

---

## Índice

- [Como funciona (visão geral)](#como-funciona-visão-geral)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Configuração (`.env`)](#configuração-env)
- [Primeiro login (captcha/OTP é humano)](#primeiro-login-captchaotp-é-humano)
- [Rodando](#rodando)
- [O painel (dashboard)](#o-painel-dashboard)
- [A régua de cobrança](#a-régua-de-cobrança)
- [Agente inbound (respostas do cliente)](#agente-inbound-respostas-do-cliente)
- [Integração com N8n (agente de respostas)](#integração-com-n8n-agente-de-respostas)
- [Revisão matinal do DOM (health-check)](#revisão-matinal-do-dom-health-check)
- [Indo para produção](#indo-para-produção)
- [Segurança e LGPD](#segurança-e-lgpd)
- [Testes e qualidade](#testes-e-qualidade)
- [Troubleshooting](#troubleshooting)
- [Limitações conhecidas e roadmap](#limitações-conhecidas-e-roadmap)
- [Multi-seguradora (Prudential em andamento)](#multi-seguradora-prudential-em-andamento)
- [Estrutura do projeto](#estrutura-do-projeto)

---

## Como funciona (visão geral)

```
            ┌──────────────────────── seu computador (local) ────────────────────────┐
            │                                                                          │
  Plataforma│   ┌─────────────┐   Playwright    ┌──────────────┐                       │
  da        │   │ Chrome       │ ◀────(worker)──▶│ ConnectorWorker│  fila serial p/ MAG  │
  seguradora│◀──│ (.mag_session)│                └──────┬───────┘                       │
  (MAG)     │   └─────────────┘                         │                               │
            │                                    ┌──────▼───────┐    SQLite local        │
  WhatsApp  │   ┌──────────┐   HTTP              │DashboardService│──▶ regua.sqlite       │
  (Z-API)  ◀┼──▶│ ZApiSender│◀───────────────────│ (FastAPI)     │                       │
            │   └──────────┘                     └──────┬───────┘                       │
            │                                    painel ▼  http://127.0.0.1:8765         │
            └──────────────────────────────────────────────────────────────────────────┘
```

- **Captcha/login é humano** (uma vez). O reCAPTCHA **não passa** em navegador
  automatizado, então o `--login` abre um **Chrome normal**, você resolve, e a
  sessão (cookies) fica salva e é reusada. As telas internas não têm captcha.
- **Worker de thread única**: o Playwright não é thread-safe, então toda ação na
  seguradora é serializada por um worker dedicado (uma janela do Chrome aberta).
- **SQLite local**: o estado de cada cliente (`regua.sqlite`) e a sessão
  (`.mag_session/`) ficam **na sua máquina** — os dados dos seus clientes não
  saem daqui. Tudo gitignored.

---

## Pré-requisitos

| Requisito | Detalhe |
|---|---|
| **Python 3.11+** | Testado em 3.14. |
| **Google Chrome** | A automação usa o canal `chrome` (não o Chromium do Playwright). |
| **Conta Z-API** | Uma instância conectada a um número de WhatsApp. Dá `ZAPI_INSTANCE_ID`, `ZAPI_TOKEN` e (se a conta exigir) `ZAPI_CLIENT_TOKEN`. |
| **Gmail App Password** *(opcional)* | Só para o e-mail do dia 2. Exige verificação em 2 etapas: `Conta Google → Segurança → Senhas de app`. |
| **Acesso à plataforma** | Login da MAG (Plataforma dos Produtores). |

---

## Instalação

```bash
git clone https://github.com/saavedra88/seguros.git
cd seguros

python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium

# cria seu .env a partir do modelo
copy .env.example .env      # Windows
# cp .env.example .env      # Linux/macOS
```

Depois edite o `.env` (próxima seção).

---

## Configuração (`.env`)

O `.env.example` é a referência comentada. **Nunca commite o `.env`** (já está no
`.gitignore`). Variáveis:

### Z-API (WhatsApp) — obrigatório para enviar
| Var | O que é |
|---|---|
| `ZAPI_INSTANCE_ID` | ID da instância Z-API. |
| `ZAPI_TOKEN` | Token da instância. |
| `ZAPI_CLIENT_TOKEN` | Token de segurança da **conta** (só se ativado no painel Z-API; senão deixe vazio). |

### Identidade da corretora (entra nas mensagens)
| Var | O que é |
|---|---|
| `NOME_CORRETOR` | Seu nome (assina as mensagens). |
| `NOME_CORRETORA` | Nome da corretora. |

### Trava de teste e avisos
| Var | O que é |
|---|---|
| `WHATSAPP_OVERRIDE_TO` | **TRAVA DE SEGURANÇA.** Se preenchido, **todo WhatsApp vai para este número** (o seu), **nunca para o cliente**. Esvazie só quando for para produção. |
| `NOTIFY_WHATSAPP_TO` | Seu número, para avisos do sistema (sessão expirou, DOM quebrou). |
| `ADMIN_WHATSAPP` | Número do admin que recebe avisos de remarcação/dúvida do agente inbound. Vazio = cai no `NOTIFY_WHATSAPP_TO`. |

### Agente inbound (webhook Z-API)
| Var | O que é |
|---|---|
| `ZAPI_WEBHOOK_SECRET` | Segredo que vai no **path** do webhook (`/webhook/zapi/<segredo>`). **Vazio = o webhook REJEITA tudo** (fail-closed). |
| `USAR_LLM_INTENT` | `false` por padrão (classificador é por regras, offline). |
| `ANTHROPIC_API_KEY` | Só se ligar o seam LLM (opcional, ainda não implementado). |
| `RESCHEDULE_MAX_DIAS` | Teto de dias para a data de remarcação (`30`). |

### Revisão matinal do DOM
| Var | O que é |
|---|---|
| `HEALTHCHECK_AUTO` | `true` = checa os seletores 1x/dia (só com a sessão de pé). |
| `HEALTHCHECK_HORA` | A partir de que hora (`08:00`). |

### Gmail (e-mail dia 2 — opcional)
| Var | O que é |
|---|---|
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | App Password de 16 chars. Sem isso, o canal de e-mail fica desligado. |

### Operação, anti-ban e janela
| Var | Default | O que é |
|---|---|---|
| `HORARIO_INICIO` / `HORARIO_FIM` | `09:00`/`18:00` | Janela de envio. |
| `DIAS_UTEIS_APENAS` | `true` | Só envia em dia útil. |
| `TIMEZONE` | `America/Sao_Paulo` | |
| `FOLLOWUP_OFFSET_DAYS` | `2` | Dias até o follow-up. |
| `MAX_WHATSAPP_POR_DIA` | `70` | Teto diário (anti-ban). |
| `PACING_MIN_S` / `PACING_MAX_S` | `20`/`45` | Pausa aleatória entre disparos. |
| `MAX_SENDS_PER_RUN` | `200` | Disjuntor por execução. |
| `MAX_FALHAS_CONSECUTIVAS` | `5` | Aborta o lote após N falhas seguidas. |

### Armazenamento, sessão e painel
| Var | Default | O que é |
|---|---|---|
| `DB_PATH` | `./regua.sqlite` | Banco local. |
| `PLAYWRIGHT_USER_DATA_DIR` | `./.mag_session` | Perfil/cookies do Chrome. |
| `CORRETOR_ID` | `local` | ID local (chave de tenant no futuro). |
| `DASHBOARD_PASSWORD` | *(vazio)* | Senha do painel. **Vazio = qualquer senha entra** (ok local; obrigatório antes de expor publicamente). |

### URLs da MAG
`MAG_LOGIN_URL`, `MAG_INADIMPLENCIAS_URL`, `MAG_CLIENTES_URL` — têm defaults; o
`.env.example` traz os valores.

---

## Primeiro login (captcha/OTP é humano)

```bash
python -m seguros --login
```

Abre um **Chrome normal** na tela de login da MAG. Faça o login (usuário, senha,
captcha/OTP), **espere cair na plataforma** e volte ao terminal e pressione ENTER.
Os cookies são salvos em `.mag_session/session_cookies.json` e reusados nos
próximos runs. A sessão dura dias; quando expira, a ferramenta avisa.

> ⚠️ **Não rode `--login` com o painel aberto.** Os dois abrem um Chrome no mesmo
> perfil e brigam pelo arquivo. Feche o painel, rode o `--login`, suba o painel de novo.

---

## Rodando

```bash
# DRY-RUN (padrão e seguro): descobre, casa contatos e RENDERIZA o que enviaria,
# sem clicar "Cobrar" e sem enviar nada. Gera um CSV em reports/.
python -m seguros

# PAINEL WEB (recomendado para o dia a dia) — http://127.0.0.1:8765
python -m seguros --dashboard
python -m seguros --dashboard --port 8888   # outra porta

# PARA VALER: gera link real (clica Cobrar) + envia
python -m seguros --live
python -m seguros --live --limit 1          # 1º teste: um cliente só

# Outras
python -m seguros --fake                    # offline, dados fake (demo/teste)
python -m seguros --test-whatsapp           # testa Z-API (manda 1 msg ao número de teste)
python -m seguros --add-optout 12345678909  # opt-out manual de um CPF
python -m seguros --inspect                 # calibrar selectors.yaml (Playwright Inspector)
python -m seguros --validate-selectors      # conferir se os seletores resolvem
python -m seguros --login                   # (re)autenticar
```

Flags úteis: `--env <caminho>` (outro `.env`), `--log-level DEBUG`.

---

## O painel (dashboard)

`python -m seguros --dashboard` sobe o FastAPI local e abre o navegador. Login pela
`DASHBOARD_PASSWORD` (vazio = provisório). Abas:

- **Pipeline** — a jornada de cada cliente: `Descoberto → Link gerado (HOLD) → Disparado → Resolvido`.
  - **Descobrir**: lê os inadimplentes e popula (sem cobrar/enviar).
  - **Gerar link (HOLD)**: clica "Cobrar" na MAG, captura o link e **segura**.
  - **Disparar / Reenviar / Follow-up**: envia o WhatsApp para o **destino mostrado**.
  - **Reconciliar**: re-checa quem pagou (lê o "Valor inadimplente" na tela do cliente).
  - KPIs: em aberto, **conversão**, recuperado, **tempo médio até pagar**, em HOLD; gráficos (funil, pagamentos/dia, status MAG, valor por etapa).
- **Conversas 💬** — caixa de teste do agente (**Simular resposta** por CPF, sem
  ngrok), **remarcações pendentes** (com "Reenviar link") e o histórico de mensagens
  recebidas com a intenção detectada.
- **Auditoria** — log de todos os disparos.

> A **trava de teste** (`WHATSAPP_OVERRIDE_TO`) aparece no topo ("Nº teste"): com
> ela setada, todo envio vai para o seu número e a coluna "Destino" mostra isso.

---

## A régua de cobrança

| Etapa | O que acontece |
|---|---|
| **Dia 0** | WhatsApp com o link de pagamento (`WHATSAPP_DIA0`). |
| **Dia 2** | Follow-up **sutil** (`WHATSAPP_FOLLOWUP`). Antes de enviar, **re-checa o pagamento ao vivo** — se já pagou, marca resolvido e não incomoda. |
| **Detecção de pagamento** | A MAG tira o cliente da lista assim que você "Cobra" (mesmo sem pagar), então **ausência ≠ pago**. O sinal confiável é o **"Valor inadimplente" na tela do cliente** (R$ 0 = pagou). É o que a **Reconciliação** usa. |
| **Métricas de sucesso** | `mark_resolved` calcula tempo-até-pagar e atribui a conversão se o pagamento veio **depois** do disparo. |

Os textos das mensagens estão em `seguros/messaging/templates.py` (fáceis de editar).

---

## Agente inbound (respostas do cliente)

Quando o cliente responde no WhatsApp, o agente entende e age. Hoje o classificador
é **por regras** (offline, custo zero), com guardas anti-falso-positivo:

| Intenção | Gatilho (exemplos) | Ação |
|---|---|---|
| **SAIR** | "quero sair", "não quero mais receber" | opt-out (CPF + telefone) + confirma |
| **JÁ PAGUEI** | "já paguei", "quitei" | **re-checa na MAG** (nunca confia no texto) → confirma ou avisa que ainda consta |
| **REMARCAR** | "pago semana que vem", "dia 25" | extrai a data, registra, **avisa o admin** |
| **NOVO LINK** | "o link não abre, manda de novo" | reenvia o link salvo |
| **DÚVIDA** | "isso é golpe?", "quanto é?" | escala pro admin |
| **SAUDAÇÃO** | "oi", "obrigado" | silêncio (anti-loop) |

**Como o webhook funciona (arquitetura atual):**
- Endpoint público `POST /webhook/zapi/{secret}` (no mesmo FastAPI, mas **sem** o
  cookie do painel — autenticação própria por **segredo no path**, fail-closed,
  validação de instância, filtra `fromMe`/grupo/não-texto).
- Idempotência atômica por `messageId` (Z-API reenvia em lentidão).
- Processa em background e responde 200 rápido.
- Endpoints de apoio (protegidos por cookie): `POST /api/inbound/simular`
  (testar sem ngrok, por CPF), `GET /api/inbound`, `GET /api/reschedules`,
  `POST /api/reschedules/{id}/regenerar-link`.

> 🔒 Enquanto `WHATSAPP_OVERRIDE_TO` estiver setado, o webhook **real não age** por
> telefone (só a simulação por CPF) — segurança na fase de teste.

---

## Integração com N8n (agente de respostas)

> 🎯 **Objetivo do handover:** rodar o agente que responde os clientes **no N8n**
> (provavelmente com um LLM), em vez do classificador por regras embutido.

O agente atual e o N8n fazem a **mesma coisa** (recebem a mensagem do cliente e
respondem/agem). Você tem duas formas de plugar o N8n. Em **ambas** o N8n precisa
de uma **URL pública** (o WhatsApp/Z-API ou o app precisam alcançá-lo) — em
desenvolvimento, use **ngrok**; em produção, um VPS/host.

### Padrão A — N8n é o cérebro (recomendado)

```
Cliente → Z-API → [Webhook N8n] → (LLM entende+responde) → [HTTP Request → Z-API /send-text]
                                          │
                                          └─(ações de negócio)→ API deste app (régua/MAG)
```

1. **Aponte o webhook do Z-API para o N8n** (nó *Webhook* do N8n), em vez de
   `/webhook/zapi/...`. O Z-API manda o payload `ReceivedCallback`
   (`phone`, `text.message`, `messageId`, `fromMe`, `isGroup`, `senderName`).
2. No N8n: filtre `fromMe`/grupo, classifique/responda com um nó de **LLM**, e
   **envie a resposta** com um nó *HTTP Request* para o Z-API:
   `POST https://api.z-api.io/instances/{id}/token/{token}/send-text`
   header `Client-Token: {client_token}`, body `{ "phone": "...", "message": "..." }`.
3. Para as **ações de negócio** (marcar opt-out, registrar remarcação, reenviar
   link, conferir pagamento na MAG), o N8n chama a **API deste app**. Isso mantém o
   N8n burro quanto à seguradora — quem fala com a MAG (Playwright) é este app.

> ⚙️ **O que falta para o Padrão A (1 ajuste de código):** os endpoints de ação
> hoje são protegidos pelo **cookie** do painel. Para o N8n chamá-los
> servidor-a-servidor, exponha-os com um **token de API** (ex.: variável
> `API_TOKEN` no `.env` + checagem de header `Authorization: Bearer ...` nos
> endpoints `/api/...`). Endpoints que valem expor: opt-out por CPF, registrar
> remarcação, reenviar link, "conferir pagamento (reconciliar) por CPF". É uma
> adição pequena e isolada em `seguros/dashboard/app.py` + `service.py`.

### Padrão B — Este app encaminha eventos para o N8n

Mantém o webhook deste app recebendo do Z-API, mas **encaminha** cada mensagem (e
os avisos de admin) para um *Webhook* do N8n (ex.: variável `N8N_WEBHOOK_URL`),
que então orquestra (LLM, CRM, notificações). Útil se você quer manter a
idempotência/auditoria locais e usar o N8n só como orquestrador downstream.
Requer uma pequena adição: um `POST` para `N8N_WEBHOOK_URL` em
`DashboardService.processar_inbound` / `_notificar_admin`.

### Contrato de dados (para o N8n parsear)

Payload de entrada do Z-API (campos que importam):
```json
{ "instanceId": "...", "messageId": "...", "phone": "5551999998888",
  "fromMe": false, "isGroup": false, "senderName": "...",
  "text": { "message": "texto do cliente" } }
```
Para responder, basta `POST .../send-text` com `{ "phone", "message" }`.

> 📌 **Recomendação:** comece pelo **Padrão A** com ngrok apontando **só** para o
> N8n (o Z-API fala com o N8n; este app continua local cuidando da régua e da MAG).
> Adicione o `API_TOKEN` quando o N8n precisar disparar ações na régua.

---

## Revisão matinal do DOM (health-check)

A MAG é um SPA que pode mudar de layout e quebrar a automação. 1x/dia (a partir de
`HEALTHCHECK_HORA`, **só com a sessão de pé**), a ferramenta confere os seletores
críticos. Se algum quebrar, **avisa por WhatsApp** (`NOTIFY_WHATSAPP_TO`) e mostra
uma **faixa vermelha** no painel pedindo recalibração (`--inspect`). Há também o
botão 🩺 no painel para checar na hora.

---

## Indo para produção

1. **Desligue a trava de teste:** esvazie `WHATSAPP_OVERRIDE_TO` (passa a enviar ao
   cliente real). Faça isso só quando tiver confiança.
2. **Webhook inbound** (receber respostas): precisa de URL pública.
   - **ngrok** (teste): exponha **só** o `/webhook/*` (ou o N8n). Defina
     `ZAPI_WEBHOOK_SECRET` e configure a mesma URL/segredo no painel Z-API.
   - **Defina `DASHBOARD_PASSWORD`** (não-vazio) antes de qualquer exposição pública —
     o painel tem auth fraca; nunca exponha a porta inteira.
3. **Número do admin:** preencha `ADMIN_WHATSAPP`.
4. **⚠️ Link clicável no iPhone:** o WhatsApp do **iOS não torna links clicáveis em
   mensagens de quem não é contato** (anti-phishing) — aparece como texto. No
   Android funciona. Mitigações: pedir ao cliente para salvar o número, ou usar uma
   **conta WhatsApp Business verificada** (links clicáveis para não-contatos). Não é
   bug do código.

---

## Segurança e LGPD

- **Segredos ficam locais.** `.env` (tokens Z-API, seu número, senha Gmail),
  `regua.sqlite` (PII dos clientes) e `.mag_session/` (cookies de login) estão **todos
  no `.gitignore`** — **nunca** suba pro GitHub.
- CPF/telefone são **mascarados** nos logs.
- WhatsApp com teto diário + pacing aleatório (anti-ban); opt-out respeitado por CPF
  **e** telefone.
- O webhook público é fail-closed (segredo no path, `secrets.compare_digest`).

## Testes e qualidade

```bash
pip install -r requirements-dev.txt   # se existir; senão: pip install pytest ruff
pytest          # ~84 testes
ruff check seguros tests
```

## Troubleshooting

| Sintoma | Causa / solução |
|---|---|
| **"Sessão MAG expirada"** no painel | O painel estava aberto antes do login, ou a sessão caiu. **Feche o painel → `python -m seguros --login` → suba o painel.** |
| `--login` não salva / Chrome conflita | Você rodou com o painel aberto (dois Chromes no mesmo perfil). Feche o painel primeiro. |
| Faixa vermelha "revisão do DOM" | A MAG mudou o layout. Rode `python -m seguros --inspect` e ajuste `selectors.yaml`. |
| Z-API 403 no `/status` | A conta exige `ZAPI_CLIENT_TOKEN`. Preencha no `.env`. |
| Webhook recusa tudo (401) | `ZAPI_WEBHOOK_SECRET` vazio (fail-closed) ou segredo errado na URL. |
| Link vira texto no iPhone | Comportamento do iOS para não-contatos (ver [produção](#indo-para-produção)). |

## Limitações conhecidas e roadmap

- **Prudential**: conector ainda não implementado (exploração feita — ver abaixo).
- **Expiração do link de pagamento** (MAG) é desconhecida — `PAYMENT_LINK_TTL_DAYS`
  fica pronto para quando se souber.
- **Seam LLM** do agente: variáveis existem (`USAR_LLM_INTENT`/`ANTHROPIC_API_KEY`),
  mas o módulo ainda é só rule-based. Para LLM, o caminho recomendado é o **N8n**.
- **API token** para N8n chamar as ações (ver [N8n](#integração-com-n8n-agente-de-respostas)).
- **Análise histórica** de conversão ao longo do tempo (planejada; rende mais com
  volume de dados).

---

## Multi-seguradora (Prudential em andamento)

A arquitetura já prevê várias seguradoras: a fronteira é a ABC
`SeguradoraConnector` (`seguros/connectors/base.py`), com `MagConnector` como a
implementação atual. Uma nova seguradora pluga aqui.

**Estado da Prudential (exploração, pausada):**
- Portal **Life Planner** (AEM) em `lifeplanner.prudential.com.br` (SSO OIDC via
  `pob-sso.prudential.com.br`); os sistemas reais são **ASP.NET** em
  `saa.prudential.com.br/DBClient/*.aspx`.
- Inadimplência = **"Relatório de Atraso"** (`PAG_DBClient_ApoliceAtraso.aspx`):
  formulário de filtro (**Dias Atraso**, Segurado, Forma Pagto....) com botões
  **Filtrar · Imprimir · Excel** (o Excel pode ser a fonte de dados ideal).
- Ferramenta genérica de login/inspeção criada: `python tools/insurer_login.py <URL_LOGIN> prudential`.
- **Perguntas em aberto antes de implementar:** como o cliente paga um prêmio em
  atraso (tem 2ª via/link, ou é débito automático e a cobrança é só lembrete?); o
  que o "Filtrar" retorna / o Excel baixa; onde vem o contato (relatório ou Carteira
  de Clientes).

---

## Estrutura do projeto

```
seguros/
  cli.py · config.py · clock.py · logging_setup.py · notify.py · orchestrator.py · report.py · cpf.py
  db/         → schema.sql, connection.py, repository.py (DAO; único lugar com SQL)
  domain/     → models.py (entidades/enums), state.py (gates puros)
  messaging/  → phone.py, templates.py, intents.py (classificador), whatsapp.py (Z-API), email.py (SMTP)
  connectors/
    base.py   → SeguradoraConnector (ABC) + DTOs  ← fronteira de abstração (multi-seguradora)
    fake.py   → FakeConnector (offline)
    mag/      → connector.py, session.py, scraping.py, links.py, selectors.py/.yaml, inspect_mode.py, login_browser.py
  dashboard/  → app.py (FastAPI), service.py (lógica), worker.py (thread do Playwright),
                webhook.py (inbound Z-API), static/ (index.html, login.html)
tools/        → insurer_login.py (login/inspeção genérico p/ nova seguradora)
tests/        → pytest (repos, intents, inbound, estado, telefone, templates, ...)
```

---

*Life Planner® é marca da The Prudential. Este projeto é uma ferramenta local
independente do corretor para gestão da própria carteira.*
