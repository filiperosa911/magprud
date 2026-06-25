# Régua de Cobrança Automatizada — Prudential
### A história de como construímos uma automação que trabalha por você

---

> **Para quem é este documento**
> Este documento foi escrito para Wladimir Leis, Life Planner da Prudential, e explica — em linguagem simples — tudo que foi construído, os desafios enfrentados e onde chegamos. O objetivo é mostrar que automação real, que funciona em portais protegidos do mundo corporativo, é resultado de muito trabalho e tentativas, não de uma solução mágica.

---

## O ponto de partida: qual era o problema?

Todo corretor de seguros de vida enfrenta o mesmo problema mensal: clientes que atrasam o pagamento do prêmio. Quando isso acontece, o corretor precisa:

1. Entrar no portal da Prudential
2. Identificar quem está em atraso
3. Buscar o boleto de segunda via para cada cliente
4. Enviar esse boleto por WhatsApp com uma mensagem de cobrança
5. Acompanhar quem pagou e quem ainda não pagou

Feito manualmente, esse processo consome horas do corretor todos os meses — horas que poderiam ser dedicadas a prospecção, atendimento e fechamento de novos negócios.

A proposta foi: **automatizar tudo isso**.

---

## O que a automação faz (visão geral)

O sistema — chamado internamente de **Régua de Cobrança** — funciona assim:

1. **Acessa o portal da Prudential** com a sessão do corretor
2. **Identifica automaticamente** todos os clientes com parcelas em atraso
3. **Gera a segunda via do boleto** para cada cliente
4. **Salva o boleto em PDF** no computador
5. **Envia o boleto por WhatsApp** com uma mensagem personalizada
6. **Registra tudo** em um banco de dados local
7. **Verifica depois** se o cliente pagou, e encerra a cobrança quando isso acontecer

Tudo isso roda num painel simples acessado pelo navegador, sem precisar abrir o portal da Prudential ou escrever uma mensagem manualmente.

---

## Capítulo 1: A muralha — o sistema anti-robô da Prudential

O primeiro grande desafio apareceu logo no início: o portal da Prudential é protegido por um sistema chamado **Imperva Incapsula** — uma tecnologia sofisticada criada justamente para detectar e bloquear robôs.

Esse sistema analisa dezenas de características do navegador que acessa o site: o jeito que o mouse se move, o tempo entre cliques, a forma como as páginas carregam, e até detalhes técnicos invisíveis do navegador. Se detectar que não é um humano, bloqueia o acesso imediatamente.

A solução foi usar o **Chrome com uma sessão real** — um navegador completo que abre na tela, igualzinho ao que qualquer pessoa usa. O robô não simula um navegador; ele *é* um navegador. Mas o login, por questão de segurança (a Prudential usa verificação em duas etapas), precisa ser feito manualmente pelo corretor uma vez. Depois disso, o sistema guarda os "cookies" da sessão — é como se o portal reconhecesse que você já entrou — e o robô pode operar sozinho por semanas.

> **Em termos simples:** imagina que você tem um assistente. Mas para entrar no prédio, você precisa mostrar o crachá pessoalmente uma vez. Depois que você entrou, o assistente pode circular livremente com a sua identificação.

---

## Capítulo 2: Encontrando os clientes em atraso

Com a sessão ativa, o sistema navega até o **Relatório de Atraso** da Prudential. Esse relatório lista todos os clientes com parcelas vencidas e não pagas.

O desafio aqui foi que o portal da Prudential foi construído com tecnologia dos anos 2000 (páginas `.aspx`, formulários antigos) — o que exigiu entender como cada parte do sistema funciona antes de automatizá-la.

O robô:
- Filtra os clientes com atraso acima do mínimo configurado
- Lê o nome, número de apólice, valor da parcela e data de vencimento de cada linha da tabela
- Identifica o telefone de contato (quando disponível no próprio relatório)

Tudo isso em segundos, sem precisar fazer nenhum clique manual.

---

## Capítulo 3: A grande batalha — o boleto de segunda via

Aqui começa a parte mais difícil de toda a automação. E onde mais tempo foi investido.

Para gerar a segunda via do boleto, o portal da Prudential faz o seguinte:

1. O corretor clica em "Imprimir" na página da apólice
2. **Uma nova janela** se abre no navegador (chamada tecnicamente de "popup")
3. Dentro dessa janela, o boleto aparece como um **PDF** — renderizado diretamente pelo Chrome

Parece simples. Mas cada uma dessas etapas trouxe um problema diferente.

---

### Tentativa 1 — Capturar a janela no momento certo

O primeiro problema: a nova janela abria tão rápido que o robô não conseguia "pegá-la" a tempo. O sistema dizia que nenhuma janela havia sido aberta.

Foram testadas diferentes formas de detectar a abertura da janela: aguardar um tempo fixo, monitorar o número de janelas abertas, usar eventos do próprio navegador. Depois de várias tentativas, o método `context.on("page")` do Playwright (a biblioteca de automação usada) provou ser o mais confiável — ele registra um "ouvinte" que é avisado assim que qualquer nova janela for aberta.

---

### Tentativa 2 — Imprimir o PDF

Com a janela capturada, a primeira ideia foi usar o comando de impressão do Chrome para gerar um PDF. O Chrome tem uma função técnica chamada `printToPDF` que transforma qualquer página em PDF.

**Resultado:** o PDF gerado saía completamente em branco.

O motivo: o boleto usa estilos CSS que escondem o conteúdo quando a página é "impressa" pelo sistema — uma proteção que o portal usa para controlar como o documento é gerado.

---

### Tentativa 3 — Capturar o PDF direto da resposta do servidor

Quando o Chrome mostra um PDF, ele precisa baixar esse PDF de algum lugar. A ideia foi interceptar essa "conversa" entre o Chrome e o servidor da Prudential e pegar o PDF no meio do caminho.

**Resultado:** o evento de download já havia passado antes do robô conseguir "ouvir" — a janela abria rápido demais.

---

### Tentativa 4 — Baixar o PDF diretamente pelo endereço

O PDF vinha de um endereço específico do servidor (`PAG_DBClient_ExibeRelatorio.aspx`). A ideia foi acessar esse endereço diretamente, com os cookies da sessão, e baixar o PDF.

**Resultado:** o servidor retornou uma página de erro HTML — o endereço exigia parâmetros que só existiam no contexto do formulário preenchido, não numa requisição direta.

---

### Tentativas 5, 6 e 7 — Variações da mesma abordagem

Foram testadas mais três formas diferentes de interceptar o tráfego de rede entre o Chrome e o servidor para capturar os bytes do PDF:

- Ouvir respostas na página principal (o PDF estava na janela popup, não na página principal)
- Configurar o ouvinte antes de clicar (o evento chegava antes do ouvinte estar pronto)
- Usar rotas de interceptação do contexto (`context.route`) — chegou perto, mas não conclusivo

Cada falha ensinava algo novo sobre como o portal funciona. Nenhuma era um beco sem saída — eram degraus.

---

### A virada: o botão de download do PDF viewer

Depois de muita análise, a solução mais direta ficou evidente: **o Chrome já tem um botão de download** no próprio visualizador de PDF — a setinha para baixo no canto superior direito da janela. Se um humano clicasse nela, o PDF seria salvo. Por que não fazer o robô clicar nela?

Parece simples. Não foi.

---

### O problema do Shadow DOM

O botão de download do Chrome não é um elemento HTML comum. Ele fica dentro de uma estrutura chamada **Shadow DOM** — uma camada isolada do navegador, como um "documento dentro do documento", que protege os componentes internos do Chrome de interferências externas.

Os seletores normais de automação não conseguem enxergar dentro dessa camada. Foi necessário escrever um código JavaScript personalizado que "mergulha" recursivamente por dentro dessas camadas ocultas até encontrar o botão:

```
Página → Shadow Root → Shadow Root → ... → cr-icon-button (botão de download)
```

Isso funcionou. O botão foi encontrado. O robô clicou.

---

### O golpe do Google Drive

Primeira execução com o novo código: em vez de baixar o PDF para o computador, o Chrome abriu uma janela pedindo para **salvar no Google Drive**.

O motivo: existem **dois botões** com aparência parecida no visualizador de PDF do Chrome — o de download local e o de salvar no Google Drive. Ambos internamente têm o mesmo identificador (`id="save"`). O robô estava encontrando o do Google Drive primeiro.

A solução foi usar um atributo mais específico: o botão de download local tem `iron-icon="cr:file-download"`, enquanto o do Google Drive tem um ícone diferente. Com esse detalhe, o robô passou a encontrar o botão certo.

---

### Resultado final

Após todas essas tentativas, o fluxo completo funciona:

1. Robô clica em "Imprimir"
2. Popup abre com o boleto em PDF
3. Robô aguarda o PDF carregar completamente
4. JavaScript percorre as camadas internas do Chrome e encontra o botão de download correto
5. Robô clica no botão
6. PDF é salvo em `C:\Users\...\roboleto\boletos\{apólice}.pdf`

---

## Capítulo 4: O WhatsApp — enviando o boleto ao cliente

Com o boleto salvo, o sistema precisa enviá-lo ao cliente por WhatsApp.

Para isso, a automação está integrada ao **Z-API**, um serviço que permite enviar mensagens e arquivos pelo WhatsApp de forma programática. O sistema:

- Abre a conversa com o número do cliente
- Envia uma mensagem de texto personalizada (com nome, valor e vencimento)
- Anexa o boleto em PDF

Tudo registrado no banco de dados local para controle e histórico.

---

## Onde estamos hoje

| Funcionalidade | Status |
|---|---|
| Login com sessão real no portal Prudential | ✅ Funcionando |
| Leitura automática dos clientes em atraso | ✅ Funcionando |
| Geração de segunda via e download do PDF | ✅ Funcionando |
| Envio por WhatsApp via Z-API | ✅ Funcionando |
| Painel de controle no navegador | ✅ Funcionando |
| Verificação de pagamento (cliente pagou?) | ✅ Funcionando |

---

## O próximo passo: número autenticado pela Meta

Atualmente o envio de WhatsApp acontece via Z-API, conectado a um número comum. O próximo passo é migrar para uma ferramenta **licenciada oficialmente pela Meta** — a empresa dona do WhatsApp.

Isso significa que as mensagens serão enviadas **pelo número real e verificado do Life Planner Wladimir**, com o selo de autenticidade da Meta. As vantagens são:

- **Credibilidade:** o cliente recebe a mensagem do número que já conhece, do corretor que atende ele
- **Segurança:** uso dentro das regras oficiais do WhatsApp, sem risco de bloqueio do número
- **Profissionalismo:** o disparo vem de um número autenticado, não de uma conexão paralela

Essa integração está prevista como próxima etapa do projeto.

---

## Considerações finais

Este projeto não foi construído com um único comando ou uma ferramenta pronta. Foi construído entendendo como o portal da Prudential funciona por dentro, testando e falhando, ajustando e testando de novo.

Cada tentativa que não funcionou revelou uma camada a mais do problema. Cada solução encontrada foi específica para esse portal, essa seguradora, esse fluxo de trabalho.

O resultado é uma automação que reproduz exatamente o que um humano faria — mas em segundos, sem erros, 24 horas por dia, sem precisar abrir o computador para isso.

---

*Documento elaborado em junho de 2026.*
*Desenvolvido sob medida para Life Planner Wladimir Leis — Prudential do Brasil.*
