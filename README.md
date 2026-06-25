# BrunoPonto

Automatiza o registro de ponto no sistema **Sólides/Tangerino**, executando os horários configurados de forma automática via agendamento.

---

## Funcionalidades

- Registro automático de ponto via navegador (Chrome, Edge ou Firefox)
- Múltiplos schedules com dias da semana, horários e datas de vigência
- Notificação nativa do Windows 5 minutos antes de cada batida
- Notificação de confirmação após o registro (sucesso ou erro)
- Indicador visual na bandeja: verde → normal, amarelo → < 30 min, âmbar → < 10 min, vermelho → executando
- Barra de progresso regressiva até a próxima batida
- Tooltip da bandeja mostra o próximo ponto e o tempo restante em tempo real
- Minimiza para a bandeja do sistema (system tray)
- Inicia automaticamente com o Windows
- Modo teste: abre o navegador e preenche os campos sem clicar em Registrar
- Modo férias: suspende todas as batidas durante um período configurado
- Watchdog: alerta no Telegram se o scheduler ficar inativo por mais de X horas
- Alerta de demora: avisa se o Selenium demorar mais que o esperado
- Bot do Telegram com comandos para consultar status e histórico
- Dead man's switch via healthchecks.io: alerta externo se o app parar de responder
- PIN com opção de mostrar/ocultar
- Configurações salvas localmente por máquina

---

## Como usar

1. Execute o `BrunoPonto.exe`
2. Preencha o **Código do Empregador** e o **PIN** na seção de credenciais
3. Clique em **Salvar**
4. Adicione os schedules desejados em **// schedules**
5. O programa ficará rodando em segundo plano e registrará o ponto automaticamente nos horários configurados

### Schedules

Cada schedule possui:

| Campo | Descrição |
|---|---|
| Nome | Identificação da batida (ex: Entrada, Almoço, Saída) |
| Horários | Um ou mais horários no formato HH:MM |
| Repetir em | Dias da semana em que a batida será executada |
| Iniciar em | Data a partir da qual o schedule passa a valer (DD/MM/AAAA) |
| Expira em | Data limite de execução, opcional (DD/MM/AAAA) |
| Habilitado | Ativa ou desativa o schedule sem excluí-lo |

Não é permitido ter dois schedules ativos com o mesmo horário nos mesmos dias.

### Modo teste

Com o modo teste ativo, o programa abre o navegador e preenche as credenciais, mas **não clica em Registrar**. Útil para validar a configuração antes de ativar em produção.

---

## Bot do Telegram

Configure o token e o chat ID na aba **Configurações → // telegram**. Comandos disponíveis:

| Comando | Descrição |
|---|---|
| `/?` | Lista todos os comandos |
| `/ping` | Confirma que o app está rodando |
| `/status` | Modo, próxima batida e último heartbeat |
| `/schedules` | Lista todos os schedules configurados |
| `/ferias` | Informa se o modo férias está ativo e as datas |
| `/log` | Últimas 5 linhas do log |
| `/dia` | Batidas reais do dia |
| `/semana` | Batidas reais dos últimos 7 dias |
| `/mes` | Batidas reais dos últimos 30 dias |
| `/teste_d` | Batidas de teste do dia |
| `/teste_s` | Batidas de teste dos últimos 7 dias |
| `/teste_m` | Batidas de teste dos últimos 30 dias |

---

## Dead man's switch (healthchecks.io)

O app envia um ping a cada minuto para a URL configurada em **Configurações → // healthchecks.io**. Se os pings pararem (app fechado ou travado), o healthchecks.io dispara um alerta externo.

Configure o webhook no healthchecks.io apontando para a API do Telegram para receber o alerta no mesmo bot.

---

## Requisitos para executar

Apenas o arquivo `BrunoPonto.exe` — sem instalação necessária.

Um dos navegadores abaixo deve estar instalado na máquina:
- Google Chrome
- Microsoft Edge
- Mozilla Firefox

---

## Arquivos gerados localmente

| Arquivo | Descrição |
|---|---|
| `bruno_ponto_config.json` | Configurações do usuário (credenciais e schedules) |
| `bruno_ponto_log.txt` | Log de execuções |

Esses arquivos são criados automaticamente na mesma pasta do executável e **não devem ser compartilhados**, pois contêm dados pessoais.

---

## Build (desenvolvedores)

Requisitos:

```bash
pip install selenium pystray pillow schedule pyinstaller
```

Gerar o executável:

```bash
python -m PyInstaller --clean BrunoPonto.spec
```

O arquivo `BrunoPonto.exe` será gerado na pasta `dist/`.

---

## Versão

**v2.0**
