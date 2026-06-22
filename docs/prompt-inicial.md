# Prompt inicial — Régua de cobrança MAG + Z-API

> Primeiro prompt que deu origem a este projeto, reproduzido verbatim (encoding UTF-8 corrigido).
> Data: 18/06/2026, 23:41 (horário local) · Origem: sessão Claude Code f4fd4982-…935c

---
Leia todo prompt abaixo e execute de forma profissional, elegante, use multiagentes, use goal nos lugaes que preicsar pra multiiteração.
Liguei modo plan para batermos martelo no plano.

Alem disso, ja adianto que tenho refeencias de como usar ZAPI em outro projeto que você pode consultar os .md aqui:
C:\aurex-app\docs\aurex-aurora

# Prompt executivo — Régua de cobrança de inadimplência (MAG Seguros + Z-API)

> Cole este documento inteiro como instrução inicial no Claude Code. Ele descreve **o que construir**. Você (Claude Code) decide a estrutura de arquivos, dependências e implementação.

---

## 1. Objetivo

Construir uma ferramenta **local** (roda na máquina de um corretor de seguros) que, **1x por dia**, puxa a lista de clientes inadimplentes da Plataforma dos Produtores da MAG Seguros, gera o link de pagamento de cada um e dispara uma **régua de comunicação** automática: WhatsApp no dia 0 e e-mail 2 dias depois, se ainda não tiver pago. O alvo é poupar o corretor do trabalho manual diário de cobrança.

## 2. Escopo desta versão (mantenha enxuto)

**Dentro:**
- Apenas **MAG Seguros**, aba "Clientes VI" (Vida Individual).
- Um único corretor, rodando localmente. Login manual.
- Régua fixa: **Dia 0 → WhatsApp + link**; **Dia 2 → e-mail + link** (se persistir); **depois, nada**.
- Respeitar consentimento por canal (a plataforma expõe isso — ver §6).

**Fora (NÃO construir agora):**
- Multi-corretor / multi-seguradora (Prudential, MetLife etc.) — só **deixe o conector MAG isolado num módulo próprio** pra facilitar isso no futuro. Sem abstração prematura além disso.
- Painel web, dashboards, renegociação, IA conversacional.
- Tratamento de expiração de link de pagamento (ver §5, limitação conhecida).

## 3. Stack sugerida (mínima, local)

- **Python 3.11+**
- **Playwright** (Chromium, contexto persistente) — para login humano + automação da UI Salesforce.
- **SQLite** — estado da régua (arquivo único local). *Não usar Supabase/Postgres nesta versão.*
- **Z-API** (WhatsApp não oficial) — envio via `send-text`.
- **smtplib** (Gmail SMTP) — e-mail.
- Agendamento: o corretor roda **um comando 1x ao dia** (ver §4 sobre login). Opcionalmente documentar cron/Agendador de Tarefas, mas a presença humana é necessária para o login.

## 4. Autenticação — CRÍTICO (human-in-the-loop, SEM quebra de captcha)

A tela de login tem reCAPTCHA. **NÃO implementar resolução automática de captcha. NÃO usar serviços de captcha-solving, nem stealth/fingerprint spoofing para burlar detecção.** O login é um passo **humano**.

Implementação:
1. Playwright com **contexto persistente** (`user_data_dir` dedicado), navegador **headed**.
2. Ao iniciar, verificar se a sessão salva ainda está autenticada (tentar abrir uma página interna autenticada e checar).
3. Se **não** estiver: abrir a tela de login, **pausar e exibir uma mensagem no console** ("Faça login e resolva o captcha na janela do navegador, depois pressione ENTER aqui"). O humano loga e resolve o captcha **manualmente**.
4. Após autenticado: persistir cookies/sessão e **seguir sozinho** com todo o resto.
5. Nas execuções seguintes, reutilizar a sessão salva; só pedir login de novo quando expirar.

URLs relevantes:
- Login (humano): `https://identidade.mag.com.br/...` (OIDC) → redireciona para a plataforma.
- Inadimplências: `https://plataformadosprodutores.mag.com.br/s/inadimplencias?orderBy=Inadimplencia_Data_Vencimento__c&typeOrderBy=DESC&cliente=naoTrabalhado&cliente=trabalhadoParcialmente`
- Meus Clientes (VI): `https://plataformadosprodutores.mag.com.br/s/clientes?status=ativo&tipoCliente=VI&page=1&pageSize=10&orderBy=premioVI&typeOrderBy=DESC`

> A plataforma é **Salesforce Experience Cloud** (Lightning SPA). Dirija a UI real com Playwright (clicar/esperar elementos), que é o caminho robusto dado que o fluxo já está mapeado abaixo. Use esperas explícitas por elementos, não `sleep` fixo.

## 5. Mapa do fluxo no site (o robô executa exatamente isto)

**A) Descobrir inadimplentes** (página Inadimplências, filtros `naoTrabalhado` + `trabalhadoParcialmente`):
- Ler a tabela: para cada linha → `CPF`, `Cliente` (nome), `Inadimplência mais antiga` (data), `Valor total inadimplente`, `Status`. Paginar até o fim.

**B) Buscar contato + consentimento** (página Meus Clientes):
- Para cada CPF inadimplente, localizar o cliente e abrir o detalhe (seta `>`). Na seção **"Contato"** ler: `E-mail`, `Celular`, `Telefone`, e os 3 flags: **`Autoriza envio de WhatsApp`**, **`Autoriza envio de e-mail`**, `Autoriza envio de SMS`.
- A chave de junção entre as duas telas é o **CPF**.

**C) Gerar o link de pagamento** (de volta na Inadimplências, no detalhe do cliente):
- Abrir o detalhe do cliente → aba **"Não trabalhadas"**.
- Marcar **todas** as inscrições (flag no canto superior esquerdo da tabela).
- Clicar **"Cobrar inadimplência"** (canto superior direito).
- No modal, clicar **"Gerar link de pagamento"** → **capturar o link gerado**.
- **1 link consolidado por cliente** (cobre todas as inscrições selecionadas).
- ⚠️ Efeito colateral **autorizado**: isso move as competências para a aba **"Trabalhadas"** no MAG. É esperado (registra a tentativa) e dá idempotência de graça (o item sai do filtro `naoTrabalhado`).
- **Limitação conhecida (não tratar agora):** não sabemos a expiração do link. Gere **uma vez** no dia 0, salve no banco e **reutilize** o mesmo link no e-mail do dia 2. Deixe um `# TODO` sinalizando que, se links expirarem, será preciso regenerar.

## 6. Regra dura de consentimento (gate antes de qualquer envio)

Antes de cada disparo, ler o flag do canal para aquele cliente:
- WhatsApp só se **`Autoriza envio de WhatsApp = Sim`**.
- E-mail só se **`Autoriza envio de e-mail = Sim`**.
- Se = Não → **não enviar por esse canal**, registrar o motivo no log.

Consequência prática da régua + consentimento:
- WhatsApp=Sim → recebe WhatsApp no dia 0.
- WhatsApp=Não, e-mail=Sim → não recebe nada no dia 0; recebe **só** o e-mail no dia 2.
- Ambos=Não → não é contatado (respeitar).

Isso é exigência de LGPD/CDC **e** reduz risco de banimento no Z-API (só fala com quem autorizou).

## 7. Modelo de dados (SQLite)

Tabela `clientes_regua` (uma linha por cliente em régua):
- `cpf` (PK), `nome`
- `valor_inadimplente`, `vencimento_mais_antigo`
- `link_pagamento`
- `autoriza_whatsapp` (bool), `autoriza_email` (bool)
- `whatsapp_enviado_em` (datetime null)
- `email_enviado_em` (datetime null)
- `enrolled_em` (datetime — define o "dia 0")
- `status` (`em_regua` | `resolvido` | `opt_out`)
- `atualizado_em`

Tabela `opt_out`: `cpf`/`telefone`, `origem` (`sair_whatsapp` | `manual`), `data`.

Tabela `log_disparos` (auditoria): `cpf`, `canal`, `link`, `resultado`, `payload_resumo`, `data`.

## 8. Loop diário (ordem de execução)

1. **Login** (humano, §4).
2. **Descoberta:** puxar lista de inadimplentes (§5A). Para cada CPF que **não** está em `clientes_regua` (e não está em `opt_out`): novo inadimplente → buscar contato+consentimento (§5B), gerar link (§5C), `enrolled_em = hoje`, salvar. Se WhatsApp autorizado → enviar WhatsApp (§9) e setar `whatsapp_enviado_em`.
3. **Follow-up (dia 2):** para cada cliente com `status = em_regua`, `enrolled_em <= hoje-2 dias`, `email_enviado_em IS NULL`: re-checar se ainda está inadimplente (abrir detalhe do cliente e ver se as competências viraram **"Regularizadas"**). Se **ainda em aberto** e e-mail autorizado → enviar e-mail (§9) e setar `email_enviado_em`. Se já regularizou → `status = resolvido`.
4. **Reconciliação:** clientes que sumiram da inadimplência / viraram Regularizadas → `status = resolvido` (pagaram).
5. **Log** de tudo.

Respeitar **janela de horário** (ex.: 09h–18h, dias úteis — configurável no `.env`) para os envios. Não recontatar fora da cadência definida.

## 9. Canais de envio

### WhatsApp (Z-API, `send-text`)
- `POST https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text`
- Header obrigatório: `Client-Token: {ZAPI_CLIENT_TOKEN}` (token de segurança da conta).
- Body: `{ "phone": "<55+DDD+numero>", "message": "<texto+link>" }`
- **Normalização do telefone:** pegar `Celular` da ficha, remover tudo que não é dígito, garantir prefixo `55`, tratar o 9º dígito de celular (formato final típico: `55` + DDD(2) + `9` + 8 dígitos). Validar antes de enviar; se inválido, logar e pular.

### E-mail (Gmail SMTP)
- `smtplib` → `smtp.gmail.com:587`, STARTTLS, login com o e-mail Gmail + **App Password** (16 caracteres).
- ⚠️ App Password exige **verificação em 2 etapas ativa** na conta Google. Documentar isso no README.
- Limite ~500 destinatários/dia em conta Gmail comum (suficiente pra um corretor).

## 10. Templates de mensagem

Variáveis: `{primeiro_nome}`, `{competencia}`, `{valor_total}`, `{link_pagamento}`, `{nome_corretor}`, `{corretora}`.

**WhatsApp (dia 0):**
```
Olá, {primeiro_nome}, tudo bem?
Aqui é {nome_corretor}, da {corretora} — sua corretora parceira MAG Seguros.
Identifiquei uma pendência no seu seguro referente a {competencia}, no valor de {valor_total}.
Para regularizar de forma rápida e segura, é só acessar o link de pagamento abaixo:
{link_pagamento}
Se já tiver pago, pode desconsiderar. Qualquer dúvida, estou à disposição por aqui.
Caso não queira mais receber estes lembretes por WhatsApp, responda SAIR.
```

**E-mail (dia 2)** — assunto: `Pendência no seu seguro MAG — {competencia}`:
```
Olá, {primeiro_nome},

Identifiquei uma pendência no seu seguro MAG referente a {competencia}, no valor de {valor_total}, ainda em aberto.

Você pode regularizar de forma rápida e segura por este link:
{link_pagamento}

Se o pagamento já tiver sido feito, por favor desconsidere este e-mail.
Fico à disposição para qualquer dúvida.

Atenciosamente,
{nome_corretor} — {corretora}
```

## 11. Opt-out ("SAIR")

- Manter a tabela `opt_out`. CPFs/telefones nela **nunca** são contatados (sobrepõe o consentimento da MAG).
- Baseline: o corretor pode adicionar manualmente.
- Opcional (deixar como `# TODO` documentado, não obrigatório nesta versão): captar respostas "SAIR" via **webhook do Z-API** (precisa expor URL pública — ex.: ngrok/VPS) e inserir automaticamente em `opt_out`.

## 12. Configuração (`.env`)

```
# Z-API
ZAPI_INSTANCE_ID=
ZAPI_TOKEN=
ZAPI_CLIENT_TOKEN=

# Gmail
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=

# Identidade da corretora (entra nas mensagens)
NOME_CORRETOR=
NOME_CORRETORA=

# Operação
HORARIO_INICIO=09:00
HORARIO_FIM=18:00
DIAS_UTEIS_APENAS=true
DB_PATH=./regua.sqlite
PLAYWRIGHT_USER_DATA_DIR=./.mag_session
```

Nunca commitar segredos. Gerar `.env.example`.

## 13. Modo de teste e segurança (padrão = DRY-RUN)

- **Padrão dry-run** (sem flag): faz login (humano) + descoberta + casamento de contatos + checagem de consentimento + **renderiza** as mensagens que *seriam* enviadas, mas **NÃO clica "Cobrar inadimplência"** (não muta o MAG) e **NÃO envia** WhatsApp/e-mail. Gera um relatório (console + CSV) do que faria.
- **`--live`**: habilita a geração real de link (clicar Cobrar) e os envios reais.
- Logar cada ação. Em `--live`, idempotência: nunca disparar 2x o mesmo canal para o mesmo cliente na mesma régua (checar `whatsapp_enviado_em` / `email_enviado_em`).

## 14. O que NÃO fazer (resumo)

- ❌ Resolver/contornar captcha automaticamente.
- ❌ Disparo em massa / fora da régua / fora do horário.
- ❌ Contatar quem não autorizou o canal ou está em `opt_out`.
- ❌ Re-cobrar (clicar Cobrar) um cliente já em régua.
- ❌ Supabase/Postgres, painel web, ou multi-tenant nesta versão.

## 15. Entregáveis

Código Python rodável localmente, com README curto (pré-requisitos: Python, `playwright install chromium`, App Password do Gmail, credenciais Z-API), comando único de execução (dry-run e `--live`), `.env.example` e o schema SQLite criado automaticamente na primeira execução.

---

### Referências úteis
- **Z-API — Send text / Send document / Webhooks:** https://developer.z-api.io
- **Padrão de integração Z-API (env vars + webhook):** repo `matheussousamartins/finance-agent-whatsapp` (GitHub) — usa `ZAPI_INSTANCE_ID/TOKEN/CLIENT_TOKEN` e webhook via ngrok, mesmo shape que usaremos para o opt-out.
- A automação da plataforma MAG (Salesforce Experience Cloud) é específica deste projeto — não há repositório de referência; siga o mapa da §5.