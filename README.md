# BrunoPonto

Automatiza o registro de ponto no sistema **Sólides/Tangerino**, executando os horários configurados de forma automática via agendamento.

---

## Funcionalidades

- Registro automático de ponto via navegador (Chrome, Edge ou Firefox)
- Múltiplos schedules com dias da semana, horários e datas de vigência
- Aviso popup 5 minutos antes de cada batida
- Popup de confirmação após o registro
- Minimiza para a bandeja do sistema (system tray)
- Inicia automaticamente com o Windows
- Modo teste: abre o navegador e preenche os campos sem clicar em Registrar
- PIN com opção de mostrar/ocultar
- Configurações salvas localmente por máquina

---

## Como usar

1. Execute o `BrunoPonto.exe`
2. Preencha o **Código do Empregador** e o **PIN** na seção de credenciais
3. Clique em **Salvar**
4. Adicione os schedules desejados em **Horários**
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

### Modo teste

Com o modo teste ativo, o programa abre o navegador e preenche as credenciais, mas **não clica em Registrar**. Útil para validar a configuração antes de ativar em produção. Após clicar em `[ run ]`, o modo é desativado automaticamente.

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

**v1.1**
