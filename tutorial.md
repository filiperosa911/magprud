# Tutorial — Régua de Cobrança (MAG + Prudential)

Ferramenta local para corretor de seguros automatizar a cobrança de clientes inadimplentes.
Este tutorial cobre a rotina diária de uso, as funcionalidades do painel e as principais dúvidas.

---

## Índice

1. [Configuração inicial (feita uma única vez)](#1-configuração-inicial-feita-uma-única-vez)
2. [Rotina de login](#2-rotina-de-login)
3. [Subindo o painel](#3-subindo-o-painel)
4. [O Pipeline — etapas do cliente](#4-o-pipeline--etapas-do-cliente)
5. [Descobrindo inadimplentes — MAG](#5-descobrindo-inadimplentes--mag)
6. [Descobrindo inadimplentes — Prudential](#6-descobrindo-inadimplentes--prudential)
7. [Gerando o link de pagamento (HOLD)](#7-gerando-o-link-de-pagamento-hold)
8. [Disparando as mensagens](#8-disparando-as-mensagens)
9. [Follow-up (Dia 2)](#9-follow-up-dia-2)
10. [Reconciliação — quem já pagou?](#10-reconciliação--quem-já-pagou)
11. [Métricas e KPIs](#11-métricas-e-kpis)
12. [Principais Dúvidas](#12-principais-dúvidas)

---

## 1. Configuração inicial (feita uma única vez)

### Pré-requisitos instalados
- Python 3.11+
- Google Chrome
- Ambiente virtual criado em `.venv/`
- Dependências instaladas via `pip install -r requirements.txt`
- Chromium do Playwright instalado via `python -m playwright install chromium`

### Arquivo `.env`
O arquivo `.env` na raiz do projeto contém todas as configurações. Os campos essenciais para o uso básico (scraping + painel) são:

```ini
NOME_CORRETOR=Wladimir Leis
```

Os demais campos (Z-API, Gmail) só são necessários quando a integração com WhatsApp for ativada.

**Regra de ouro: nunca commite o `.env` no GitHub.** Ele contém credenciais e dados sensíveis.

---

## 2. Rotina de login

### MAG

O login da MAG precisa ser feito **uma única vez** (ou quando a sessão expirar, o que leva alguns dias). Os cookies ficam salvos localmente e são reusados automaticamente.

Abra o terminal na pasta do projeto e execute:

```powershell
.\.venv\Scripts\python -m seguros --login
```

Um Chrome normal vai abrir na tela de login da MAG. Faça o login normalmente (usuário, senha, captcha/OTP). Quando cair na plataforma, volte ao terminal e pressione **ENTER**. Os cookies são salvos em `.mag_session/`.

> **Atenção:** nunca rode `--login` com o painel já aberto. Os dois abrem o Chrome no mesmo perfil e entram em conflito. Feche o painel antes de fazer o login.

### Prudential

A Prudential **não tem login separado**. Os tokens expiram em minutos, então o login acontece na mesma hora em que os dados são buscados (dentro do painel, pelo botão Descobrir). O passo a passo está na seção 6.

### Sessão expirou?

Se o painel mostrar "Sessão MAG expirada":

1. Feche o painel (`Ctrl+C` no terminal)
2. Rode `.\.venv\Scripts\python -m seguros --login`
3. Suba o painel novamente

---

## 3. Subindo o painel

```powershell
.\.venv\Scripts\python -m seguros --dashboard
```

O navegador abre automaticamente em `http://127.0.0.1:8765`.

Para usar outra porta:

```powershell
.\.venv\Scripts\python -m seguros --dashboard --port 8888
```

Para fechar, pressione `Ctrl+C` no terminal.

---

## 4. O Pipeline — etapas do cliente

Cada cliente percorre as seguintes etapas no painel:

| Etapa | O que significa |
|---|---|
| **Descoberto** | O robô encontrou o cliente como inadimplente na plataforma |
| **Link gerado (HOLD)** | O link de pagamento foi gerado mas ainda não foi enviado |
| **Disparado** | A mensagem de cobrança foi enviada ao cliente |
| **Resolvido** | O pagamento foi detectado (valor inadimplente = R$ 0) |

O cliente também pode estar em **Opt-out** (pediu para não receber mais mensagens).

---

## 5. Descobrindo inadimplentes — MAG

No painel, clique em **Descobrir**. O robô vai:

1. Acessar a plataforma da MAG com os cookies salvos
2. Ler todos os clientes inadimplentes (paginando até o fim)
3. Buscar o contato (telefone, e-mail) de cada um
4. Salvar tudo no banco local `regua.sqlite`
5. Popular o pipeline na tela

Isso não envia nada nem altera nada na MAG — é só leitura.

---

## 6. Descobrindo inadimplentes — Prudential

Na tela de login do painel, selecione **Prudential** e entre. Depois clique em **Descobrir**.

Um Chrome vai abrir. Faça o login normalmente na plataforma Life Planner (usuário, senha, OTP). O painel detecta automaticamente quando você cai no relatório de apólices em atraso e segue sozinho — não é necessário fazer mais nada no terminal.

> **Atenção:** não feche o Chrome durante esse processo. A sessão da Prudential expira em minutos, então login e busca de dados acontecem na mesma janela.

---

## 7. Gerando o link de pagamento (HOLD)

Disponível apenas para clientes da **MAG** (a Prudential não fornece link de pagamento — opera em modo lembrete).

No pipeline, selecione um cliente e clique em **Gerar link (HOLD)**. O robô clica em "Cobrar" na plataforma da MAG, captura o link gerado e o armazena no banco — mas **não envia nada ainda**. O cliente fica em status HOLD até você disparar manualmente.

Isso permite revisar antes de enviar.

---

## 8. Disparando as mensagens

Com o link gerado (ou no caso da Prudential, sem link), clique em **Disparar** no painel para enviar a mensagem ao cliente.

> **Trava de teste:** enquanto `WHATSAPP_OVERRIDE_TO` estiver preenchido no `.env`, **todas as mensagens vão para o seu número**, nunca para o cliente. A coluna "Destino" no painel mostra para onde vai. Só esvazie esse campo quando estiver pronto para enviar de verdade.

As mensagens usam o seguinte formato:

> *Olá, João! Aqui é a IA do Wladimir Leis, seu corretor de seguros.*
> *Percebi que você tem um prêmio em aberto referente a Jan/2025, no valor de R$ 320,00 — pode ser algum erro na cobrança ou algo que passou despercebido.*
> *Para resolver de forma rápida e segura, é só acessar o link abaixo:*
> *https://...*

Para editar o texto das mensagens, abra o arquivo `seguros/messaging/templates.py`.

---

## 9. Follow-up (Dia 2)

Dois dias após o primeiro disparo, o robô pode enviar um segundo lembrete. Antes de enviar, ele **re-checa na plataforma** se o cliente já pagou. Se pagou, marca como resolvido e não envia — evita incomodar quem já quitou.

No painel, o botão **Follow-up** aparece para os clientes elegíveis (com mais de 2 dias desde o disparo e sem pagamento detectado).

O prazo de 2 dias pode ser ajustado no `.env`:

```ini
FOLLOWUP_OFFSET_DAYS=2
```

---

## 10. Reconciliação — quem já pagou?

Clique em **Reconciliar** no painel. O robô acessa a tela de cada cliente na MAG e lê o campo "Valor inadimplente":

- **R$ 0** → marca como Resolvido, registra o tempo que levou para pagar
- **Valor > 0** → segue em aberto

> A MAG remove o cliente da lista de inadimplências assim que você clica "Cobrar" — por isso a ausência da lista não significa que pagou. O sinal confiável é o valor na tela do cliente.

---

## 11. Métricas e KPIs

O painel exibe na aba **Pipeline**:

| Métrica | O que mede |
|---|---|
| Em aberto | Clientes ainda na régua |
| Conversão | % de clientes que pagaram após o disparo |
| Recuperado | Valor total em R$ de pagamentos detectados |
| Tempo médio até pagar | Média de horas entre o disparo e o pagamento |
| Em HOLD | Clientes com link gerado aguardando disparo |

Também há gráficos de funil, pagamentos por dia e distribuição por status MAG.

---

## 12. Principais Dúvidas

### Sobre os dados

**O que significam os status da MAG (naoTrabalhado, trabalhadoParcialmente, trabalhado)?**
Esses status vêm diretamente da plataforma MAG e indicam se alguma ação de cobrança já foi feita. `naoTrabalhado` = nunca teve cobrança. `trabalhadoParcialmente` = pelo menos uma competência foi cobrada mas ainda há pendência. `trabalhado` = todas as competências foram trabalhadas. O robô apenas lê esses status — quem os atualiza é a MAG quando você clica "Cobrar".

**A data de vencimento do prêmio fica salva em algum lugar?**
Sim. O campo `vencimento_mais_antigo` é salvo no banco com a data completa no formato `YYYY-MM-DD` (dia, mês e ano). É a data de vencimento da competência mais antiga em aberto do cliente.

**Onde ficam salvos os dados do pipeline? Por quanto tempo?**
No arquivo `regua.sqlite` na raiz do projeto. Para visualizar, use o programa gratuito [DB Browser for SQLite](https://sqlitebrowser.org/). Os dados não expiram automaticamente — ficam lá até o status mudar para `resolvido` ou você limpar manualmente.

**Os dados da MAG e da Prudential ficam no mesmo banco?**
Sim, ambos ficam na mesma tabela `clientes_regua` do `regua.sqlite`. Hoje são separados internamente por um identificador de seguradora (`corretor_id`), mas o painel os exibe em abas separadas por login. Uma coluna unificada com a origem (MAG ou Prudential) pode ser adicionada futuramente.

**O link de pagamento gerado fica salvo onde?**
Na coluna `link_pagamento` da tabela `clientes_regua`, junto com `link_gerado_em` (data e hora em que foi gerado). Ele permanece salvo até o cliente resolver ou o banco ser limpo.

---

### Sobre o disparo

**O destino da mensagem só é preenchido depois que gero o link?**
Não. O telefone do cliente é preenchido durante o "Descobrir", quando o robô busca os contatos. O "destino" exibido no painel reflete o campo `WHATSAPP_OVERRIDE_TO` do `.env` — enquanto preenchido com seu número, é ele que aparece (trava de teste). Não depende de gerar o link.

**Como funciona a trava de teste?**
Enquanto `WHATSAPP_OVERRIDE_TO` tiver um número no `.env`, toda mensagem vai para aquele número — nunca para o cliente. O painel mostra "Nº teste" no topo como aviso. Para enviar de verdade, esvazie esse campo no `.env` e reinicie o painel.

**O robô pode enviar para alguém que já pagou?**
Não, se você usar o Follow-up corretamente. Antes de enviar o segundo lembrete, o robô verifica na plataforma se o cliente ainda está inadimplente. Se pagou, cancela o envio e marca como resolvido.

**Por que o link de pagamento não funciona no iPhone?**
O iOS não torna links clicáveis em mensagens de quem não é contato (proteção anti-phishing). No Android funciona normalmente. Solução: pedir ao cliente para salvar o número, ou usar uma conta WhatsApp Business verificada.

---

### Sobre a operação

**Posso rodar o `--login` com o painel aberto?**
Não. Os dois abrem o Chrome no mesmo perfil e entram em conflito. Feche o painel antes de fazer o login.

**O robô envia mensagens fora do horário comercial?**
Não. Por padrão, só envia entre 09:00 e 18:00 em dias úteis. Isso é configurável no `.env`:
```ini
HORARIO_INICIO=09:00
HORARIO_FIM=18:00
DIAS_UTEIS_APENAS=true
```

**Tem algum limite de mensagens por dia?**
Sim. O padrão é 70 mensagens/dia (`MAX_WHATSAPP_POR_DIA=70`) com pausas aleatórias entre envios para evitar bloqueio no WhatsApp.

**A MAG pode mudar o layout e quebrar o robô?**
Sim, é um risco real. O sistema tem um health-check automático que verifica os seletores 1x/dia. Se algum quebrar, aparece uma faixa vermelha no painel. Para recalibrar, rode:
```powershell
.\.venv\Scripts\python -m seguros --inspect
```

**Como faço um cliente sair da régua manualmente?**
```powershell
.\.venv\Scripts\python -m seguros --add-optout 12345678909
```
Substitua pelo CPF (só números). O cliente não receberá mais mensagens.

**Posso testar o sistema sem enviar nada?**
Sim. O modo padrão (sem `--live`) é dry-run — descobre os inadimplentes, calcula o que enviaria e gera um relatório CSV em `reports/`, sem clicar "Cobrar" nem enviar nenhuma mensagem.

---

### Sobre as mensagens

**Onde edito o texto das mensagens enviadas aos clientes?**
No arquivo `seguros/messaging/templates.py`. Cada variável é um template diferente. As variáveis disponíveis dentro de cada mensagem são: `${primeiro_nome}`, `${nome_corretor}`, `${competencia}`, `${valor_total}`, `${link_pagamento}`.

**O que acontece quando o cliente responde?**
O agente inbound classifica a resposta e age automaticamente:
- **"SAIR"** → cancela o opt-out e confirma
- **"Já paguei"** → verifica na MAG antes de responder
- **"Pago semana que vem"** → extrai a data e avisa o Wladimir
- **"O link não abre"** → reenvia o link salvo
- **"Isso é golpe?"** → escala para o corretor responder manualmente
