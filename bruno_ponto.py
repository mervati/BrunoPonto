"""
Bruno Ponto
===========
App de registro automático de ponto na Sólides.

Dependências:
    pip install selenium schedule pystray pillow

Tkinter já vem com o Python no Windows/macOS.
No Linux:  sudo apt install python3-tk

O Selenium 4.6+ baixa o driver do Chrome/Edge/Firefox automaticamente
via Selenium Manager — sem configuração manual de chromedriver.
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import schedule
import time
import logging
import json
import os
import sys
from datetime import datetime, timedelta

import ctypes
import subprocess
import base64

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options   import Options as ChromeOptions
    from selenium.webdriver.edge.options     import Options as EdgeOptions
    from selenium.webdriver.firefox.options  import Options as FirefoxOptions
    from selenium.webdriver.common.action_chains import ActionChains
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

import urllib.request
import urllib.parse
import tempfile

# ──────────────────────────────────────────────────────
#  CONSTANTES
# ──────────────────────────────────────────────────────
APP_NAME    = "Bruno Ponto"
APP_VERSION = "2.4"
URL_PONTO   = "https://app.tangerino.com.br/Tangerino/"
GITHUB_REPO = "mervati/BrunoPonto"

# Quando empacotado como .exe pelo PyInstaller, __file__ aponta para a pasta
# temporária de extração. sys.executable aponta para o .exe de verdade.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CFG_FILE = os.path.join(_BASE_DIR, "bruno_ponto_config.json")
LOG_FILE = os.path.join(_BASE_DIR, "bruno_ponto_log.txt")

_MSG_PADRAO = (
    "✅ Ponto batido com sucesso!\n"
    "📅 {dia_semana}, {data} às {hora}.\n\n"
    "Registro automático via bruno.ponto v{versao} 🟢"
)

DEFAULT_CONFIG = {
    "codigo_empregador":  "",
    "pin":                "",
    "telegram_token":     "",
    "telegram_chat_id":   "",
    "telegram_mensagem":  _MSG_PADRAO,
    "schedules":          [],
    "modo_teste":         True,
    "watchdog_ativo":       True,
    "watchdog_horas":       2,
    "last_heartbeat":       None,
    "alerta_demora_ativo":  True,
    "alerta_demora_seg":    60,
    "ferias_ativo":         False,
    "ferias_inicio":        None,             # "YYYY-MM-DD"
    "ferias_fim":           None,             # "YYYY-MM-DD"
    "hc_ping_url":          "",               # healthchecks.io ping URL
    "notificacoes_ativas":  True,             # notificações nativas do Windows
}

CORES = {
    "bg":         "#0b0f12",
    "panel":      "#111820",
    "card":       "#161f2c",
    "section_bg": "#161f2c",
    "green":      "#10b981",
    "green_d":    "#0d9488",
    "red":        "#ef4444",
    "amber":      "#f59e0b",
    "text":       "#e2e8f0",
    "muted":      "#64748b",
    "border":     "#1f293d",
    "neon":       "#10b981",
    "input_bg":   "#0b0f12",
    "purple":     "#10b981",
    "purple_d":   "#0d9488",
    "teste":      "#f59e0b",
}

def _criar_icone_tray(cor="#00FF41"):
    size = 64
    img  = Image.new("RGBA", (size, size), (9, 9, 9, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, size - 8, size - 8], fill=cor)
    draw.polygon([(24, 20), (24, 44), (44, 32)], fill="#090909")
    return img

_ICONE_VERDE   = None
_ICONE_AMARELO = None
_ICONE_VERMELHO = None

def _icones_tray():
    global _ICONE_VERDE, _ICONE_AMARELO, _ICONE_VERMELHO
    if HAS_TRAY and _ICONE_VERDE is None:
        _ICONE_VERDE    = _criar_icone_tray("#00FF41")
        _ICONE_AMARELO  = _criar_icone_tray("#F59E0B")
        _ICONE_VERMELHO = _criar_icone_tray("#EF4444")

# AUMID do PowerShell — usado para mostrar toasts sem registro de app
_PS_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"

def _win_toast(titulo: str, mensagem: str):
    """Exibe notificação nativa do Windows via PowerShell."""
    xml = (
        '<toast>'
        '<visual><binding template="ToastGeneric">'
        f'<text>{titulo}</text>'
        f'<text>{mensagem}</text>'
        '</binding></visual>'
        '</toast>'
    )
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null
$d=[Windows.Data.Xml.Dom.XmlDocument]::new()
$d.LoadXml('{xml}')
$t=[Windows.UI.Notifications.ToastNotification]::new($d)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_PS_AUMID}').Show($t)
"""
    try:
        enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-EncodedCommand", enc],
            creationflags=0x08000000
        )
    except Exception as e:
        log.warning(f"win_toast erro: {e}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # migra formato antigo (horarios + dias_semana → schedules)
            if "horarios" in cfg and "schedules" not in cfg:
                dias = cfg.get("dias_semana", [0, 1, 2, 3, 4])
                cfg["schedules"] = [
                    {"nome": h, "horarios": [h], "dias": dias,
                     "data_fim": None, "ativo": True}
                    for h in cfg.get("horarios", [])
                ]
                cfg.pop("horarios", None)
                cfg.pop("dias_semana", None)
            # migra schedule com horario singular → horarios lista
            for s in cfg.get("schedules", []):
                if "horario" in s and "horarios" not in s:
                    s["horarios"] = [s.pop("horario")]
                s.setdefault("data_inicio", None)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ──────────────────────────────────────────────────────
#  AUTOSTART (registro do Windows)
# ──────────────────────────────────────────────────────
_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_KEY  = "BrunoPonto"

def _get_autostart() -> bool:
    if not HAS_WINREG:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _REG_KEY)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def _set_autostart(enable: bool):
    if not HAS_WINREG:
        return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, _REG_KEY, 0, winreg.REG_SZ,
                              f'"{sys.executable}" --minimized')
        else:
            try:
                winreg.DeleteValue(key, _REG_KEY)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        log.error(f"Autostart erro: {e}")


# ──────────────────────────────────────────────────────
#  SELENIUM — DRIVER E AUTOMAÇÃO
# ──────────────────────────────────────────────────────
_driver      = None
_driver_lock = threading.Lock()


def _criar_driver():
    """Tenta criar WebDriver na ordem: Chrome → Edge → Firefox."""
    erros = []

    _PREFS = {
        "profile.default_content_setting_values.geolocation":        1,
        "profile.default_content_setting_values.media_stream_camera": 2,
        "profile.default_content_setting_values.media_stream_mic":    2,
        "profile.default_content_setting_values.notifications":       2,
    }

    try:
        opts = ChromeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--use-fake-device-for-media-stream")
        opts.add_argument("--disable-features=MediaStreamTrack")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_experimental_option("prefs", _PREFS)
        return webdriver.Chrome(options=opts)
    except Exception as e:
        erros.append(f"Chrome: {e}")

    try:
        opts = EdgeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--use-fake-device-for-media-stream")
        opts.add_argument("--disable-features=MediaStreamTrack")
        opts.add_experimental_option("prefs", _PREFS)
        return webdriver.Edge(options=opts)
    except Exception as e:
        erros.append(f"Edge: {e}")

    try:
        opts = FirefoxOptions()
        opts.set_preference("permissions.default.camera", 2)
        opts.set_preference("permissions.default.microphone", 2)
        return webdriver.Firefox(options=opts)
    except Exception as e:
        erros.append(f"Firefox: {e}")

    raise RuntimeError(
        "Nenhum navegador suportado encontrado (Chrome, Edge ou Firefox).\n"
        + "\n".join(erros)
    )


def _driver_ativo(driver) -> bool:
    """Retorna True se a janela do navegador ainda estiver aberta."""
    try:
        _ = driver.current_url
        return True
    except Exception:
        return False


def _mover_cursor_para_elemento(driver, elemento):
    """Move o cursor do SO para o centro do elemento no browser (Windows)."""
    try:
        loc  = elemento.location   # posição no viewport
        size = elemento.size       # largura/altura do elemento

        # posição da janela do browser na tela
        win_x = driver.execute_script("return window.screenX;")
        win_y = driver.execute_script("return window.screenY;")

        # altura da barra de ferramentas do Chrome (diferença outer - inner)
        toolbar = driver.execute_script(
            "return window.outerHeight - window.innerHeight;"
        )

        # escala DPI (ex: 1.25 em telas 125%)
        dpr = driver.execute_script("return window.devicePixelRatio;") or 1

        # coordenada do centro do botão em pixels de tela
        cx = int((win_x + loc["x"] + size["width"]  / 2) * dpr)
        cy = int((win_y + toolbar  + loc["y"] + size["height"] / 2) * dpr)

        if sys.platform == "win32":
            ctypes.windll.user32.SetCursorPos(cx, cy)
        else:
            import subprocess
            if subprocess.run(["which", "xdotool"], capture_output=True).returncode == 0:
                subprocess.run(["xdotool", "mousemove", str(cx), str(cy)])
    except Exception as e:
        log.warning(f"Mover cursor para elemento: {e}")


def _preencher_formulario(driver, cfg: dict, modo_teste: bool = False):
    """Clica na aba Registrar Ponto, preenche credenciais e (se não for teste) clica em Registrar."""
    wait = WebDriverWait(driver, 20)

    # Aguarda as abas carregarem e clica em "Registrar Ponto"
    aba = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(@href,'baterPonto')]")
    ))
    aba.click()
    time.sleep(1.5)  # aguarda navegação completar após clique na aba

    # Aguarda o campo Código do Empregador aparecer (id fixo da página)
    cod_input = wait.until(EC.element_to_be_clickable((By.ID, "codigoEmpregador")))
    pin_input = driver.find_element(By.ID, "codigoPin")

    cod_input.clear()
    cod_input.send_keys(cfg["codigo_empregador"])
    pin_input.clear()
    pin_input.send_keys(cfg["pin"])

    time.sleep(0.5)

    btn = wait.until(EC.element_to_be_clickable((By.ID, "registraPonto")))

    if modo_teste:
        # Move o cursor do SO para cima do botão Registrar
        ActionChains(driver).move_to_element(btn).perform()
        _mover_cursor_para_elemento(driver, btn)
        time.sleep(0.5)
        return

    # Modo real: clica no botão Registrar
    btn.click()


# ──────────────────────────────────────────────────────
#  LÓGICA DE PONTO
# ──────────────────────────────────────────────────────def _mover_mouse_z():
    """Move o mouse em Z sem pyautogui — usa ctypes no Windows."""
    try:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            cx, cy = sw // 2, sh // 2
            for px, py in [(cx-100,cy-50),(cx+100,cy-50),(cx-100,cy+50),(cx+100,cy+50),(cx,cy)]:
                user32.SetCursorPos(px, py)
                time.sleep(0.2)
        else:
            import subprocess
            if subprocess.run(["which","xdotool"], capture_output=True).returncode == 0:
                for px, py in [(700,300),(900,300),(700,500),(900,500),(800,400)]:
                    subprocess.run(["xdotool","mousemove",str(px),str(py)])
                    time.sleep(0.2)
    except Exception as e:
        log.warning(f"Mover mouse: {e}")


DIAS_PT = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
           "Sexta-feira", "Sábado", "Domingo"]

def _msg_telegram(hora_label: str, modo_teste: bool, template: str = "", cfg: dict = None) -> str:
    now        = datetime.now()
    dia_semana = DIAS_PT[now.weekday()]
    data_fmt   = now.strftime("%d/%m/%Y")
    prefixo    = "[TESTE] " if modo_teste else ""
    tmpl       = template.strip() or _MSG_PADRAO

    dia_semana_prox = data_prox = hora_prox = "—"
    if cfg is not None:
        prox = proximo_ponto(cfg)
        if prox:
            dia_semana_prox = DIAS_PT[prox.weekday()]
            data_prox       = prox.strftime("%d/%m/%Y")
            hora_prox       = prox.strftime("%H:%M")

    try:
        corpo = tmpl.format(
            dia_semana=dia_semana, data=data_fmt, hora=hora_label, versao=APP_VERSION,
            dia_semana_prox=dia_semana_prox, data_prox=data_prox, hora_prox=hora_prox,
        )
    except KeyError:
        corpo = tmpl
    return f"{prefixo}{corpo}"

def executar_acao(cfg: dict, app_ref, hora_label: str):
    """Executa a ação de ponto (modo real ou teste)."""
    global _driver
    modo_teste = cfg.get("modo_teste", True)
    agora      = datetime.now().strftime("%H:%M:%S")

    if not HAS_SELENIUM:
        app_ref.root.after(0, lambda: app_ref.add_log(
            "ERRO: selenium não instalado.  (pip install selenium)", "erro"))
        return

    if cfg.get("ferias_ativo", False):
        inicio = cfg.get("ferias_inicio")
        fim    = cfg.get("ferias_fim")
        hoje_d = datetime.now().date()
        try:
            em_ferias = True
            if inicio and hoje_d < datetime.strptime(inicio, "%Y-%m-%d").date():
                em_ferias = False
            if fim and hoje_d > datetime.strptime(fim, "%Y-%m-%d").date():
                em_ferias = False
            if em_ferias:
                msg_fer = f"Modo férias ativo — batida ignorada ({datetime.now().strftime('%d/%m/%Y')})."
                log.info(msg_fer)
                app_ref.root.after(0, lambda: app_ref.add_log(msg_fer, "info"))
                return
        except Exception:
            pass
    app_ref._update_heartbeat()
    prefixo = "[TESTE] " if modo_teste else ""
    msg = f"{prefixo}Abrindo navegador para registrar ponto às {hora_label} ({agora})..."
    log.info(msg)
    app_ref.root.after(0, lambda: app_ref.add_log(msg, "teste" if modo_teste else "info"))

    def _registrar():
        global _driver
        app_ref._set_tray_estado("executando")
        try:
            t_inicio = time.time()
            with _driver_lock:
                if _driver is None or not _driver_ativo(_driver):
                    _driver = _criar_driver()

                _driver.get(URL_PONTO)
                _preencher_formulario(_driver, cfg, modo_teste=modo_teste)

            duracao = time.time() - t_inicio
            limite  = int(cfg.get("alerta_demora_seg", 60))
            if cfg.get("alerta_demora_ativo", True) and duracao > limite:
                now        = datetime.now()
                dia_semana = DIAS_PT[now.weekday()]
                data_fmt   = now.strftime("%d/%m/%Y")
                msg_demora = (
                    f"⏱️ Batida demorou mais que o esperado!\n"
                    f"📅 {dia_semana}, {data_fmt} às {hora_label}.\n\n"
                    f"Duração: {int(duracao)}s (limite: {limite}s)\n"
                    f"O ponto pode ter sido registrado com atraso 🟡"
                )
                app_ref._enviar_telegram(msg_demora)
                aviso = f"Atenção: execução demorou {int(duracao)}s (limite {limite}s)"
                log.warning(aviso)
                app_ref.root.after(0, lambda: app_ref.add_log(aviso, "teste"))

            if modo_teste:
                ok_msg = f"[TESTE] Navegador aberto e campos preenchidos — clique NÃO executado ({hora_label})"
                log.info(ok_msg)
                app_ref.root.after(0, lambda: app_ref.add_log(ok_msg, "teste"))
                time.sleep(10)
                with _driver_lock:
                    try:
                        _driver.quit()
                    except Exception:
                        pass
                    _driver = None
                app_ref.root.after(0, lambda: app_ref.show_alert(hora_label, agora, modo_teste=True))
                app_ref._enviar_telegram(_msg_telegram(hora_label, modo_teste=True,
                    template=cfg.get("telegram_mensagem", ""), cfg=cfg))
                app_ref._set_tray_estado("normal")
            else:
                ok_msg = f"✓ Ponto registrado às {hora_label}"
                log.info(ok_msg)
                app_ref.root.after(0, lambda: app_ref.add_log(ok_msg, "ok"))
                time.sleep(10)
                with _driver_lock:
                    try:
                        _driver.quit()
                    except Exception:
                        pass
                    _driver = None
                app_ref.root.after(0, lambda: app_ref.show_alert(hora_label, agora, modo_teste=False))
                app_ref._enviar_telegram(_msg_telegram(hora_label, modo_teste=False,
                    template=cfg.get("telegram_mensagem", ""), cfg=cfg))
                app_ref._set_tray_estado("normal")

        except Exception as e:
            err = f"Erro ao registrar: {e}"
            log.error(err)
            app_ref.root.after(0, lambda: app_ref.add_log(err, "erro"))
            app_ref._set_tray_estado("normal")
            now        = datetime.now()
            dia_semana = DIAS_PT[now.weekday()]
            data_fmt   = now.strftime("%d/%m/%Y")
            msg_falha  = (
                f"❌ Falha ao bater o ponto!\n"
                f"📅 {dia_semana}, {data_fmt} às {hora_label}.\n\n"
                f"Erro: {e}\n\n"
                f"Verifique o app bruno.ponto 🔴"
            )
            app_ref._enviar_telegram(msg_falha)

    threading.Thread(target=_registrar, daemon=True).start()


# ──────────────────────────────────────────────────────
#  SCHEDULER
# ──────────────────────────────────────────────────────
DIAS_MAP = {
    0: "monday", 1: "tuesday", 2: "wednesday",
    3: "thursday", 4: "friday", 5: "saturday", 6: "sunday",
}

def _cinco_min_antes(hora_str: str) -> str:
    h, m = map(int, hora_str.split(":"))
    total = h * 60 + m - 5
    if total < 0:
        total += 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"

def _mostrar_aviso(app_ref, hora_label: str):
    if app_ref.cfg.get("notificacoes_ativas", True):
        _win_toast("Bruno Ponto", f"Ponto automático em 5 minutos  →  {hora_label}")

def rebuild_schedule(cfg: dict, app_ref):
    schedule.clear()
    hoje = datetime.now().date()

    for s in cfg.get("schedules", []):
        if not s.get("ativo", True):
            continue
        data_inicio = s.get("data_inicio")
        if data_inicio:
            try:
                if hoje < datetime.strptime(data_inicio, "%Y-%m-%d").date():
                    continue
            except Exception:
                pass
        data_fim = s.get("data_fim")
        if data_fim:
            try:
                if hoje > datetime.strptime(data_fim, "%Y-%m-%d").date():
                    continue
            except Exception:
                pass
        for hora in s.get("horarios", []):
            for dia in s.get("dias", []):
                nome_dia = DIAS_MAP.get(dia)
                if nome_dia:
                    getattr(schedule.every(), nome_dia).at(hora).do(
                        executar_acao, cfg=cfg, app_ref=app_ref, hora_label=hora
                    )
                    aviso_hora = _cinco_min_antes(hora)
                    getattr(schedule.every(), nome_dia).at(aviso_hora).do(
                        _mostrar_aviso, app_ref=app_ref, hora_label=hora
                    )

    return schedule.next_run()


def _horario_anterior(cfg: dict):
    """Retorna o horário agendado ativo imediatamente anterior ao atual."""
    agora   = datetime.now()
    hoje    = agora.weekday()
    melhor  = None
    for s in cfg.get("schedules", []):
        if not s.get("ativo", True):
            continue
        if hoje not in s.get("dias", []):
            continue
        for hora_str in s.get("horarios", []):
            try:
                h, m = map(int, hora_str.split(":"))
                dt = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                if dt < agora and (melhor is None or dt > melhor):
                    melhor = dt
            except Exception:
                pass
    return melhor


def proximo_ponto(cfg: dict):
    """Retorna o datetime da próxima batida real, ignorando os avisos de 5min."""
    agora = datetime.now()
    hoje  = agora.weekday()  # 0=seg … 6=dom
    candidatos = []

    for s in cfg.get("schedules", []):
        if not s.get("ativo", True):
            continue
        data_fim = s.get("data_fim")
        if data_fim:
            try:
                if agora.date() > datetime.strptime(data_fim, "%Y-%m-%d").date():
                    continue
            except Exception:
                pass
        for hora_str in s.get("horarios", []):
            try:
                h, m = map(int, hora_str.split(":"))
            except Exception:
                continue
            for dia in s.get("dias", []):
                diff      = (dia - hoje) % 7
                candidato = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                candidato += timedelta(days=diff)
                if candidato <= agora:
                    candidato += timedelta(days=7)
                candidatos.append(candidato)

    return min(candidatos) if candidatos else None


def _ler_batidas_log(from_date) -> list:
    """Lê o log e retorna [(date, [(hora, tipo)])] a partir de from_date."""
    raw      = {}
    pat_real  = "✓ Ponto registrado às "
    pat_teste = "Navegador aberto e campos preenchidos — clique NÃO executado ("
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for linha in f:
                try:
                    data = datetime.strptime(linha[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if data < from_date:
                    continue
                hora = tipo = None
                if pat_real in linha:
                    idx  = linha.index(pat_real) + len(pat_real)
                    hora = linha[idx:idx + 5].strip()
                    tipo = "real"
                elif pat_teste in linha:
                    idx  = linha.index(pat_teste) + len(pat_teste)
                    hora = linha[idx:idx + 5].strip()
                    tipo = "teste"
                if hora and tipo and len(hora) == 5 and ":" in hora:
                    raw.setdefault(data, []).append((hora, tipo))
    except Exception as e:
        log.error(f"Erro ao ler log de batidas: {e}")
    return sorted(raw.items())


# ──────────────────────────────────────────────────────
#  JANELA DE AVISO (5 min antes)
# ──────────────────────────────────────────────────────
class AvisoWindow(tk.Toplevel):
    def __init__(self, parent, hora):
        super().__init__(parent)
        self.title("bruno.ponto")
        self.configure(bg=CORES["bg"])
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.attributes("-toolwindow", True)   # sem botão na barra de tarefas

        w, h = 320, 110
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = sw - w - 16
        y  = sh - h - 56   # acima da barra de tarefas
        self.geometry(f"{w}x{h}+{x}+{y}")

        tk.Frame(self, bg=CORES["amber"], height=3).pack(fill="x")

        body = tk.Frame(self, bg=CORES["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=(8, 8))

        top = tk.Frame(body, bg=CORES["bg"])
        top.pack(fill="x")
        tk.Label(top, text="Bruno Ponto  —  aviso",
                 font=("Consolas", 9, "bold"),
                 bg=CORES["bg"], fg=CORES["amber"]).pack(side="left")

        tk.Label(body, text=f"Ponto automático em 5 minutos  →  {hora}",
                 font=("Consolas", 10),
                 bg=CORES["bg"], fg=CORES["text"]).pack(anchor="w", pady=(4, 0))

        tk.Label(body, text="O registro será feito automaticamente.",
                 font=("Consolas", 8),
                 bg=CORES["bg"], fg=CORES["muted"]).pack(anchor="w")

        tk.Button(body, text="OK",
                  bg=CORES["amber"], fg=CORES["bg"],
                  font=("Consolas", 9, "bold"),
                  relief="flat", cursor="hand2",
                  activebackground=CORES["amber"],
                  activeforeground=CORES["bg"],
                  padx=14,
                  command=self.destroy).pack(anchor="e", pady=(6, 0))

        # fecha sozinho após 30 minutos
        self.after(1_800_000, self.destroy)


# ──────────────────────────────────────────────────────
#  JANELA DE ALERTA CUSTOMIZADA
# ──────────────────────────────────────────────────────
class AlertaWindow(tk.Toplevel):
    def __init__(self, parent, hora, agora, modo_teste):
        super().__init__(parent)
        self.title("bruno.ponto")
        self.configure(bg=CORES["bg"])
        self.resizable(False, False)

        self.update_idletasks()
        w, h = 400, 230
        x = parent.winfo_rootx() + (parent.winfo_width()  - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # borda verde no topo
        tk.Frame(self, bg=CORES["green"], height=2).pack(fill="x")

        cor  = CORES["amber"] if modo_teste else CORES["green"]
        tag  = "[ TEST ]" if modo_teste else "[ OK ]"
        sub  = "hover executado — click não realizado" if modo_teste else "ponto registrado com sucesso"

        tk.Label(self, text=tag,
                 font=("Consolas", 20, "bold"),
                 bg=CORES["bg"], fg=cor).pack(pady=(18, 4))

        tk.Label(self, text=f"horario={hora}  real={agora}",
                 font=("Consolas", 11),
                 bg=CORES["bg"], fg=CORES["text"]).pack()

        tk.Label(self, text=f"// {sub}",
                 font=("Consolas", 9),
                 bg=CORES["bg"], fg=CORES["muted"]).pack(pady=4)

        tk.Button(self, text="[ fechar ]",
                  bg=CORES["bg"], fg=cor,
                  font=("Consolas", 10, "bold"),
                  relief="flat", cursor="hand2",
                  highlightbackground=cor,
                  highlightthickness=1,
                  activebackground=CORES["bg"],
                  activeforeground=cor,
                  command=self.destroy).pack(pady=14)



# ──────────────────────────────────────────────────────
#  JANELA EDITAR / CRIAR SCHEDULE
# ──────────────────────────────────────────────────────
class EditarScheduleWindow(tk.Toplevel):
    # índice 0=Seg … 6=Dom  (mesma ordem interna do restante do código)
    DIAS_LABELS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

    def __init__(self, parent, schedule_entry=None, existing_schedules=None,
                 callback=None, edit_index=None):
        super().__init__(parent)
        titulo = "Editar Disparador" if edit_index is not None else "Novo Disparador"
        self.title(titulo)
        self.configure(bg=CORES["bg"])
        self.resizable(False, False)
        self.grab_set()
        self.callback   = callback
        self.edit_index = edit_index
        self.existing   = existing_schedules or []
        s = schedule_entry or {}

        w, h = 500, 580
        x = parent.winfo_rootx() + (parent.winfo_width()  - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        tk.Frame(self, bg=CORES["green"], height=2).pack(fill="x")

        body = tk.Frame(self, bg=CORES["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=12)

        # ── Nome ──────────────────────────────────────
        nome_row = tk.Frame(body, bg=CORES["bg"])
        nome_row.pack(fill="x", pady=(0, 10))
        tk.Label(nome_row, text="Nome:", font=("Consolas", 10),
                 bg=CORES["bg"], fg=CORES["text"], width=14, anchor="w").pack(side="left")
        self.nome_var = tk.StringVar(value=s.get("nome", ""))
        tk.Entry(nome_row, textvariable=self.nome_var, font=("Consolas", 11),
                 bg=CORES["input_bg"], fg=CORES["green"],
                 insertbackground=CORES["green"], relief="flat", bd=4,
                 width=28).pack(side="left")

        # ── Seção: Configurações ───────────────────────
        self._section(body, "Configurações")

        # Iniciar em
        ini_row = tk.Frame(body, bg=CORES["bg"])
        ini_row.pack(fill="x", pady=4)
        tk.Label(ini_row, text="Iniciar em:", font=("Consolas", 10),
                 bg=CORES["bg"], fg=CORES["text"], width=14, anchor="w").pack(side="left")
        self.data_ini_var = tk.StringVar(value=self._iso_to_br(
            s.get("data_inicio") or datetime.now().strftime("%Y-%m-%d")))
        tk.Entry(ini_row, textvariable=self.data_ini_var, font=("Consolas", 11),
                 bg=CORES["input_bg"], fg=CORES["green"],
                 insertbackground=CORES["green"], relief="flat", bd=4,
                 width=14).pack(side="left")
        tk.Label(ini_row, text=" DD/MM/AAAA", font=("Consolas", 8),
                 bg=CORES["bg"], fg=CORES["muted"]).pack(side="left")

        # Horários
        hora_row = tk.Frame(body, bg=CORES["bg"])
        hora_row.pack(fill="x", pady=4)
        tk.Label(hora_row, text="Horários:", font=("Consolas", 10),
                 bg=CORES["bg"], fg=CORES["text"], width=14, anchor="w").pack(side="left")

        hora_right = tk.Frame(hora_row, bg=CORES["bg"])
        hora_right.pack(side="left", fill="x", expand=True)

        lb_frame = tk.Frame(hora_right, bg=CORES["input_bg"],
                            highlightbackground=CORES["green"], highlightthickness=1)
        lb_frame.pack(fill="x")
        self._hora_listbox = tk.Listbox(lb_frame,
                                        bg=CORES["input_bg"], fg=CORES["green"],
                                        selectbackground=CORES["green"],
                                        selectforeground=CORES["bg"],
                                        font=("Consolas", 11),
                                        relief="flat", bd=0,
                                        activestyle="none", height=3)
        self._hora_listbox.pack(fill="x", padx=4, pady=2)
        for h in sorted(s.get("horarios", [])):
            self._hora_listbox.insert(tk.END, h)

        hora_btns = tk.Frame(hora_right, bg=CORES["bg"])
        hora_btns.pack(anchor="w", pady=(2, 0))
        tk.Button(hora_btns, text="Adicionar", font=("Consolas", 9),
                  bg=CORES["card"], fg=CORES["green"],
                  relief="flat", cursor="hand2",
                  highlightbackground=CORES["green"], highlightthickness=1,
                  activebackground=CORES["card"], activeforeground=CORES["green"],
                  command=self._add_hora).pack(side="left", padx=(0, 6))
        tk.Button(hora_btns, text="Editar", font=("Consolas", 9),
                  bg=CORES["card"], fg=CORES["amber"],
                  relief="flat", cursor="hand2",
                  highlightbackground=CORES["amber"], highlightthickness=1,
                  activebackground=CORES["card"], activeforeground=CORES["amber"],
                  command=self._editar_hora).pack(side="left", padx=(0, 6))
        tk.Button(hora_btns, text="Remover", font=("Consolas", 9),
                  bg=CORES["card"], fg=CORES["red"],
                  relief="flat", cursor="hand2",
                  highlightbackground=CORES["red"], highlightthickness=1,
                  activebackground=CORES["card"], activeforeground=CORES["red"],
                  command=self._del_hora).pack(side="left")
        self._hora_listbox.bind("<Double-Button-1>", lambda e: self._editar_hora())

        # Dias da semana (grid 4 + 3)
        dias_lbl_row = tk.Frame(body, bg=CORES["bg"])
        dias_lbl_row.pack(fill="x", pady=(8, 2))
        tk.Label(dias_lbl_row, text="Repetir em:", font=("Consolas", 10),
                 bg=CORES["bg"], fg=CORES["text"], width=14, anchor="w").pack(side="left")

        dias_sel = s.get("dias", [0, 1, 2, 3, 4])
        self.dias_vars = []
        dias_grid = tk.Frame(body, bg=CORES["bg"])
        dias_grid.pack(anchor="w", padx=(112, 0))

        for i, lbl in enumerate(self.DIAS_LABELS):
            col = i % 4
            row_n = i // 4
            v = tk.BooleanVar(value=i in dias_sel)
            self.dias_vars.append(v)
            tk.Checkbutton(dias_grid, text=lbl, variable=v,
                           font=("Consolas", 10),
                           bg=CORES["bg"], fg=CORES["text"],
                           selectcolor=CORES["input_bg"],
                           activebackground=CORES["bg"],
                           activeforeground=CORES["green"],
                           width=10, anchor="w").grid(row=row_n, column=col,
                                                      sticky="w", padx=2, pady=1)

        # ── Seção: Configurações avançadas ────────────
        self._section(body, "Configurações avançadas")

        # Expira em
        fim_row = tk.Frame(body, bg=CORES["bg"])
        fim_row.pack(fill="x", pady=4)
        self.tem_fim_var  = tk.BooleanVar(value=bool(s.get("data_fim")))
        self.data_fim_var = tk.StringVar(value=self._iso_to_br(s.get("data_fim") or ""))
        tk.Checkbutton(fim_row, text="Expira em:", variable=self.tem_fim_var,
                       font=("Consolas", 10),
                       bg=CORES["bg"], fg=CORES["text"],
                       selectcolor=CORES["input_bg"],
                       activebackground=CORES["bg"],
                       activeforeground=CORES["green"],
                       width=13, anchor="w",
                       command=self._toggle_fim).pack(side="left")
        self.fim_entry = tk.Entry(fim_row, textvariable=self.data_fim_var,
                                  font=("Consolas", 11), width=14,
                                  state="normal" if self.tem_fim_var.get() else "disabled",
                                  bg=CORES["input_bg"], fg=CORES["green"],
                                  insertbackground=CORES["green"],
                                  disabledbackground=CORES["input_bg"],
                                  disabledforeground=CORES["muted"],
                                  relief="flat", bd=4)
        self.fim_entry.pack(side="left")
        tk.Label(fim_row, text="  DD/MM/AAAA", font=("Consolas", 8),
                 bg=CORES["bg"], fg=CORES["muted"]).pack(side="left")

        # ── Habilitado ────────────────────────────────
        hab_frame = tk.Frame(body, bg=CORES["card"],
                             highlightbackground=CORES["border"],
                             highlightthickness=1)
        hab_frame.pack(fill="x", pady=(14, 0), ipady=4, ipadx=6)
        self.ativo_var = tk.BooleanVar(value=s.get("ativo", True))
        tk.Checkbutton(hab_frame, text="  Habilitado", variable=self.ativo_var,
                       font=("Consolas", 11, "bold"),
                       bg=CORES["card"], fg=CORES["green"],
                       selectcolor=CORES["input_bg"],
                       activebackground=CORES["card"],
                       activeforeground=CORES["green"]).pack(anchor="w")

        # ── Botões OK / Cancelar ──────────────────────
        btn_bar = tk.Frame(self, bg=CORES["bg"])
        btn_bar.pack(fill="x", padx=20, pady=(8, 14))
        tk.Button(btn_bar, text="Cancelar",
                  font=("Consolas", 10), bg=CORES["card"], fg=CORES["muted"],
                  relief="flat", cursor="hand2",
                  highlightbackground=CORES["border"], highlightthickness=1,
                  activebackground=CORES["card"], activeforeground=CORES["text"],
                  command=self.destroy, width=10).pack(side="right", padx=(6, 0))
        tk.Button(btn_bar, text="OK",
                  font=("Consolas", 10, "bold"), bg=CORES["green"], fg=CORES["bg"],
                  relief="flat", cursor="hand2",
                  activebackground=CORES["green_d"], activeforeground=CORES["bg"],
                  command=self._salvar, width=10).pack(side="right")

    def _section(self, parent, titulo):
        row = tk.Frame(parent, bg=CORES["bg"])
        row.pack(fill="x", pady=(12, 6))
        tk.Label(row, text=titulo, font=("Consolas", 9, "bold"),
                 bg=CORES["bg"], fg=CORES["green"]).pack(side="left")
        tk.Frame(row, bg=CORES["muted"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=5)

    @staticmethod
    def _iso_to_br(iso: str) -> str:
        if not iso:
            return ""
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return iso

    def _add_hora(self):
        val = simpledialog.askstring("Adicionar horário", "Digite o horário (HH:MM):", parent=self)
        if not val:
            return
        val = val.strip()
        try:
            datetime.strptime(val, "%H:%M")
        except ValueError:
            messagebox.showerror("Formato inválido", "Use HH:MM (ex: 09:00)", parent=self)
            return
        if val not in self._hora_listbox.get(0, tk.END):
            self._hora_listbox.insert(tk.END, val)
            todos = sorted(self._hora_listbox.get(0, tk.END))
            self._hora_listbox.delete(0, tk.END)
            for h in todos:
                self._hora_listbox.insert(tk.END, h)

    def _del_hora(self):
        sel = self._hora_listbox.curselection()
        if sel:
            self._hora_listbox.delete(sel[0])

    def _editar_hora(self):
        sel = self._hora_listbox.curselection()
        if not sel:
            messagebox.showinfo("Selecione um horário",
                                "Clique em um horário da lista antes de editar.", parent=self)
            return
        idx    = sel[0]
        atual  = self._hora_listbox.get(idx)
        novo   = simpledialog.askstring("Editar horário", "Novo horário (HH:MM):",
                                        initialvalue=atual, parent=self)
        if not novo:
            return
        novo = novo.strip()
        try:
            datetime.strptime(novo, "%H:%M")
        except ValueError:
            messagebox.showerror("Formato inválido", "Use HH:MM (ex: 09:00)", parent=self)
            return
        outros = list(self._hora_listbox.get(0, tk.END))
        outros.pop(idx)
        if novo in outros:
            messagebox.showerror("Duplicado", f"{novo} já está na lista.", parent=self)
            return
        self._hora_listbox.delete(idx)
        todos = sorted(outros + [novo])
        self._hora_listbox.delete(0, tk.END)
        for h in todos:
            self._hora_listbox.insert(tk.END, h)
        novo_idx = todos.index(novo)
        self._hora_listbox.selection_set(novo_idx)

    def _toggle_fim(self):
        if self.tem_fim_var.get():
            self.fim_entry.config(state="normal")
        else:
            self.fim_entry.config(state="disabled")
            self.data_fim_var.set("")

    def _salvar(self):
        nome        = self.nome_var.get().strip()
        horarios    = list(self._hora_listbox.get(0, tk.END))
        dias        = [i for i, v in enumerate(self.dias_vars) if v.get()]
        data_inicio_br = self.data_ini_var.get().strip() or None
        data_fim_br    = self.data_fim_var.get().strip() if self.tem_fim_var.get() else None
        ativo          = self.ativo_var.get()

        if not nome:
            messagebox.showerror("Campo obrigatório", "Informe um nome.", parent=self)
            return
        if not horarios:
            messagebox.showerror("Horários obrigatórios", "Adicione ao menos um horário.", parent=self)
            return
        if not dias:
            messagebox.showerror("Dias obrigatórios", "Selecione ao menos um dia.", parent=self)
            return

        data_inicio = data_fim = None
        for campo, val_br, attr in [("Iniciar em", data_inicio_br, "data_inicio"),
                                     ("Expira em",  data_fim_br,    "data_fim")]:
            if val_br:
                try:
                    data_iso = datetime.strptime(val_br, "%d/%m/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Data inválida",
                                         f"{campo}: use DD/MM/AAAA (ex: 31/12/2025)", parent=self)
                    return
                if attr == "data_inicio":
                    data_inicio = data_iso
                else:
                    data_fim = data_iso

        NMS = self.DIAS_LABELS
        if ativo:
            for i, s in enumerate(self.existing):
                if i == self.edit_index:
                    continue
                if not s.get("ativo", True):
                    continue
                horas_existentes = s.get("horarios", [s.get("horario", "")])
                for hora in horarios:
                    if hora in horas_existentes:
                        conflito = set(s.get("dias", [])) & set(dias)
                        if conflito:
                            dias_str = ", ".join(NMS[d] for d in sorted(conflito))
                            messagebox.showerror(
                                "Conflito de horário",
                                f"Já existe uma batida às {hora} em: {dias_str}\n('{s.get('nome', '')}')",
                                parent=self)
                            return

        self.callback(
            {"nome": nome, "horarios": sorted(horarios), "dias": sorted(dias),
             "data_inicio": data_inicio, "data_fim": data_fim, "ativo": ativo},
            self.edit_index
        )
        self.destroy()


# ──────────────────────────────────────────────────────
#  TOOLTIP
# ──────────────────────────────────────────────────────
class _Tooltip:
    def __init__(self, widget, text):
        self._widget = widget
        self._text   = text
        self._tip    = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        x = self._widget.winfo_rootx() + 24
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.wm_attributes("-topmost", True)
        frame = tk.Frame(self._tip, bg=CORES["card"],
                         highlightbackground=CORES["border"],
                         highlightthickness=1)
        frame.pack()
        tk.Label(frame, text=self._text,
                 font=("Consolas", 9),
                 bg=CORES["card"], fg=CORES["text"],
                 wraplength=300, justify="left",
                 padx=12, pady=8).pack()

    def _hide(self, event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ──────────────────────────────────────────────────────
#  APP PRINCIPAL
# ──────────────────────────────────────────────────────
class BrunoPontoApp:
    def __init__(self):
        self.cfg = load_config()
        self.cfg["modo_teste"] = False
        self.root = tk.Tk()
        self.root.title(f"bruno.ponto <dev/> v{APP_VERSION}")
        self.root.configure(bg=CORES["bg"])
        self.root.resizable(False, False)

        try:
            self.root.iconbitmap(default='')
        except Exception:
            pass

        w, h = 580, 800
        self.root.geometry(f"{w}x{h}")
        self._center()

        self._tray_icon   = None
        self._tray_estado = "normal"  # "normal" | "alerta" | "executando"
        _icones_tray()
        self._build_ui()
        self._setup_tray()
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.protocol("WM_DELETE_WINDOW", self._minimizar_para_tray)
        if "--minimized" in sys.argv:
            self.root.after(300, self._minimizar_para_tray)
        self._scheduler_thread = threading.Thread(
            target=self._run_scheduler, daemon=True
        )
        self._scheduler_thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._watchdog_thread.start()
        self._telegram_poll_thread = threading.Thread(
            target=self._telegram_polling_loop, daemon=True
        )
        self._telegram_poll_thread.start()
        self._update_heartbeat()
        self._atualizar_prox()
        self._tg_registrar_comandos()
        threading.Thread(target=self._checar_atualizacao_bg, daemon=True).start()

    # ── ATUALIZAÇÃO ───────────────────────────────────────

    def _checar_atualizacao_bg(self):
        versao, url = self._verificar_atualizacao()
        if versao:
            self.root.after(0, lambda: self._mostrar_notif_atualizacao(versao, url))

    def _verificar_atualizacao(self):
        try:
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(api_url, headers={"User-Agent": "BrunoPonto"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            tag = data.get("tag_name", "").lstrip("v")
            if not tag:
                return None, None
            latest  = tuple(int(x) for x in tag.split("."))
            current = tuple(int(x) for x in APP_VERSION.split("."))
            if latest > current:
                for asset in data.get("assets", []):
                    if asset["name"] == "BrunoPonto.exe":
                        return tag, asset["browser_download_url"]
        except Exception as e:
            log.error(f"Verificação de atualização: {e}")
        return None, None

    def _mostrar_notif_atualizacao(self, versao, url):
        self._btn_update.config(
            text=f"⬆ v{versao}",
            bg=CORES["amber"], fg=CORES["bg"],
            highlightbackground=CORES["amber"],
            activebackground=CORES["amber"],
            activeforeground=CORES["bg"],
            font=("Consolas", 9, "bold"),
            command=lambda: self._confirmar_atualizacao(versao, url)
        )

    def _verificar_atualizacao_manual(self):
        C = CORES
        self._btn_update.config(
            text="verificando...", state="disabled",
            fg=C["muted"], bg=C["section_bg"],
            highlightbackground=C["muted"],
            font=("Consolas", 9)
        )
        def _checar():
            versao, url = self._verificar_atualizacao()
            def _resultado():
                if versao:
                    self._mostrar_notif_atualizacao(versao, url)
                    self._confirmar_atualizacao(versao, url)
                else:
                    self._btn_update.config(
                        text="⬆ atualizar", state="normal",
                        fg=C["green"], bg=C["section_bg"],
                        highlightbackground=C["green"],
                        activebackground=C["card"],
                        font=("Consolas", 9, "bold")
                    )
                    messagebox.showinfo(
                        "Sem atualizações",
                        f"Você já está na versão mais recente (v{APP_VERSION}).",
                        parent=self.root
                    )
            self.root.after(0, _resultado)
        threading.Thread(target=_checar, daemon=True).start()

    def _confirmar_atualizacao(self, versao, url):
        resp = messagebox.askyesno(
            "Atualização disponível",
            f"Nova versão v{versao} disponível.\n\nDeseja baixar e instalar agora?\nO app será reiniciado automaticamente.",
            parent=self.root
        )
        if resp:
            self._baixar_e_aplicar_atualizacao(versao, url)

    def _baixar_e_aplicar_atualizacao(self, versao, url):
        C = CORES

        # janela de progresso
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Atualizando...")
        prog_win.configure(bg=C["bg"])
        prog_win.resizable(False, False)
        prog_win.geometry("360x110")
        prog_win.transient(self.root)
        prog_win.grab_set()
        x = self.root.winfo_rootx() + (580 - 360) // 2
        y = self.root.winfo_rooty() + (800 - 110) // 2
        prog_win.geometry(f"360x110+{x}+{y}")

        tk.Label(prog_win, text=f"Baixando v{versao}...", font=("Consolas", 10),
                 bg=C["bg"], fg=C["text"]).pack(pady=(18, 6))

        bar = ttk.Progressbar(prog_win, length=300, mode="determinate", maximum=100)
        bar.pack()

        pct_lbl = tk.Label(prog_win, text="0%", font=("Consolas", 9),
                           bg=C["bg"], fg=C["muted"])
        pct_lbl.pack(pady=4)

        def _reporthook(count, block_size, total_size):
            if total_size > 0:
                pct = min(int(count * block_size * 100 / total_size), 100)
                self.root.after(0, lambda p=pct: (bar.configure(value=p),
                                                   pct_lbl.configure(text=f"{p}%")))

        def _baixar():
            try:
                tmp_exe = os.path.join(tempfile.gettempdir(), "BrunoPonto_new.exe")
                urllib.request.urlretrieve(url, tmp_exe, reporthook=_reporthook)

                self.root.after(0, lambda: pct_lbl.configure(text="Instalando..."))
                self.root.after(0, lambda: bar.configure(value=100))

                current_exe = sys.executable
                tmp_dir     = tempfile.gettempdir()
                bat = (
                    "@echo off\n"
                    "timeout /t 3 /nobreak > NUL\n"
                    f'for /d %%i in ("{tmp_dir}\\_MEI*") do rd /s /q "%%i" 2>NUL\n'
                    f'move /y "{tmp_exe}" "{current_exe}"\n'
                    f'start "" "{current_exe}"\n'
                    "del \"%~f0\"\n"
                )
                bat_path = os.path.join(tmp_dir, "bruno_update.bat")
                with open(bat_path, "w") as f:
                    f.write(bat)
                subprocess.Popen(["cmd", "/c", bat_path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                self.root.after(500, lambda: os._exit(0))
            except Exception as e:
                self.root.after(0, lambda: (
                    prog_win.destroy(),
                    messagebox.showerror("Erro na atualização", str(e), parent=self.root)
                ))

        threading.Thread(target=_baixar, daemon=True).start()

    def _center(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x  = (sw - 580) // 2
        y  = (sh - 800) // 2
        self.root.geometry(f"580x800+{x}+{y}")

    # ── UI ────────────────────────────────────────────

    def _build_ui(self):
        C = CORES

        # Topbar
        topbar = tk.Frame(self.root, bg=C["section_bg"], height=56)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")

        tleft = tk.Frame(topbar, bg=C["section_bg"])
        tleft.pack(side="left", padx=20, fill="y")
        tk.Label(tleft, text=">_", font=("Consolas", 16, "bold"),
                 bg=C["section_bg"], fg=C["green"]).pack(side="left")
        tk.Label(tleft, text=" bruno.ponto", font=("Consolas", 14, "bold"),
                 bg=C["section_bg"], fg=C["text"]).pack(side="left")

        tright = tk.Frame(topbar, bg=C["section_bg"])
        tright.pack(side="right", padx=20, fill="y")
        tk.Label(tright, text="<dev/>", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["green"]).pack(side="right")
        tk.Label(tright, text=f"v{APP_VERSION}  ", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="right")
        self._btn_update = tk.Button(
            tright, text="⬆ atualizar",
            bg=C["section_bg"], fg=C["green"],
            font=("Consolas", 9, "bold"),
            relief="flat", cursor="hand2",
            highlightbackground=C["green"],
            highlightthickness=1,
            activebackground=C["card"],
            activeforeground=C["green"],
            padx=10, pady=3,
            command=self._verificar_atualizacao_manual
        )
        self._btn_update.pack(side="right", padx=(0, 10))

        # Banner teste
        self.teste_banner = tk.Frame(self.root, height=28)
        self.teste_banner.pack(fill="x")
        self.teste_banner.pack_propagate(False)
        self.teste_lbl = tk.Label(self.teste_banner, text="",
                                  font=("Consolas", 9, "bold"))
        self.teste_lbl.pack(side="left", padx=20)
        self._banner_side_lbl = tk.Label(self.teste_banner, text="Modo Simulação",
                                         font=("Consolas", 8), fg=C["muted"])
        self._banner_side_lbl.pack(side="right", padx=20)

        # Notebook estilizado
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TNotebook",
            background=C["bg"], borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure("Dark.TNotebook.Tab",
            background=C["section_bg"], foreground=C["muted"],
            font=("Consolas", 9), padding=[14, 6], borderwidth=0)
        style.map("Dark.TNotebook.Tab",
            background=[("selected", C["bg"]), ("active", C["card"])],
            foreground=[("selected", C["green"]), ("active", C["text"])])

        nb = ttk.Notebook(self.root, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True, pady=(4, 0))

        tab1 = tk.Frame(nb, bg=C["bg"])
        nb.add(tab1, text="  Principal  ")
        self._build_tab_principal(tab1)

        tab2 = tk.Frame(nb, bg=C["bg"])
        nb.add(tab2, text="  Configurações  ")
        self._build_tab_config(tab2)

        self._atualizar_banner()

    def _build_tab_principal(self, parent):
        C = CORES

        wrap = tk.Frame(parent, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=C["bg"])
        body.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        bwin = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(bwin, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Credenciais
        self._section(body, "// credenciais", padx=20)
        cred_card = self._card(body)
        g = tk.Frame(cred_card, bg=C["section_bg"])
        g.pack(fill="x", padx=10, pady=(8, 8))
        g.columnconfigure(0, weight=1)
        g.columnconfigure(1, weight=1)
        self._grid_input(g, "Cod. Empregador", "codigo_var",
                         self.cfg["codigo_empregador"], r=0, c=0)
        self._grid_input(g, "PIN", "pin_var",
                         self.cfg["pin"], r=0, c=1, show="●", toggle=True)
        save_row = tk.Frame(cred_card, bg=C["section_bg"])
        save_row.pack(fill="x", padx=10, pady=(0, 10))
        self._mk_btn(save_row, "Salvar", self._salvar_credenciais,
                     solid=True).pack(side="right")

        # Schedules
        self._section(body, "// schedules", padx=20)
        sched_card = self._card(body)
        sh = tk.Frame(sched_card, bg=C["section_bg"])
        sh.pack(fill="x", padx=10, pady=(4, 6))
        self._mk_btn(sh, "+ Adicionar",
                     self._abrir_adicionar_schedule).pack(side="right")
        list_wrap = tk.Frame(sched_card, bg=C["section_bg"])
        list_wrap.pack(fill="x", padx=10, pady=(0, 8))
        self._sched_canvas = tk.Canvas(list_wrap, bg=C["section_bg"],
                                       highlightthickness=0, height=150)
        self._sched_scroll = tk.Scrollbar(list_wrap, orient="vertical",
                                          command=self._sched_canvas.yview)
        self._sched_inner = tk.Frame(self._sched_canvas, bg=C["section_bg"])
        self._sched_inner.bind("<Configure>", lambda e: self._sched_canvas.configure(
            scrollregion=self._sched_canvas.bbox("all")))
        self._sched_win = self._sched_canvas.create_window(
            (0, 0), window=self._sched_inner, anchor="nw")
        self._sched_canvas.configure(yscrollcommand=self._sched_scroll.set)
        self._sched_canvas.bind("<Configure>",
            lambda e: self._sched_canvas.itemconfig(self._sched_win, width=e.width))
        self._sched_canvas.pack(side="left", fill="both", expand=True)
        self._sched_scroll.pack(side="right", fill="y")
        self._render_schedules()

        # Modo + Status
        ms = tk.Frame(body, bg=C["bg"])
        ms.pack(fill="x", padx=20, pady=(10, 0))
        ms.columnconfigure(0, weight=1)
        ms.columnconfigure(1, weight=2)

        modo_card = tk.Frame(ms, bg=C["section_bg"],
                             highlightbackground=C["border"], highlightthickness=1)
        modo_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), ipady=8, ipadx=10)
        tk.Label(modo_card, text="// modo", font=("Consolas", 8),
                 bg=C["section_bg"], fg=C["green"]).pack(anchor="w", pady=(4, 4))
        self.modo_var = tk.BooleanVar(value=self.cfg["modo_teste"])
        tk.Checkbutton(modo_card, variable=self.modo_var,
                       text=" TEST_MODE",
                       font=("Consolas", 10, "bold"),
                       bg=C["section_bg"], fg=C["green"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_modo).pack(anchor="w")

        stat_card = tk.Frame(ms, bg=C["section_bg"],
                             highlightbackground=C["green"], highlightthickness=1)
        stat_card.grid(row=0, column=1, sticky="nsew", ipady=8, ipadx=10)
        stat_top = tk.Frame(stat_card, bg=C["section_bg"])
        stat_top.pack(fill="x", pady=(4, 2))
        tk.Label(stat_top, text="// status", font=("Consolas", 8),
                 bg=C["section_bg"], fg=C["green"]).pack(side="left")
        self._mk_btn(stat_top, "RUN", self._testar_agora,
                     solid=True).pack(side="right")
        self.prox_lbl = tk.Label(stat_card, text="calculando...",
                                 font=("Consolas", 11, "bold"),
                                 bg=C["section_bg"], fg=C["green"])
        self.prox_lbl.pack(anchor="w")
        self.relogio_lbl = tk.Label(stat_card, text="",
                                    font=("Consolas", 9),
                                    bg=C["section_bg"], fg=C["muted"])
        self.relogio_lbl.pack(anchor="w")

        self._prog_canvas = tk.Canvas(stat_card, bg=C["section_bg"],
                                      height=6, bd=0, highlightthickness=0)
        self._prog_canvas.pack(fill="x", pady=(6, 2))
        self._prog_lbl = tk.Label(stat_card, text="",
                                   font=("Consolas", 8),
                                   bg=C["section_bg"], fg=C["muted"])
        self._prog_lbl.pack(anchor="w")
        self._tick_relogio()

        # Output
        self._section(body, "// output", padx=20)
        log_outer = tk.Frame(body, bg=C["section_bg"],
                             highlightbackground=C["border"], highlightthickness=1)
        log_outer.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.log_text = tk.Text(log_outer,
                                bg=C["input_bg"], fg=C["text"],
                                font=("Consolas", 11),
                                relief="flat", bd=6,
                                state="disabled", height=5)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("ok",    foreground=C["green"])
        self.log_text.tag_config("erro",  foreground=C["red"])
        self.log_text.tag_config("teste", foreground=C["amber"])
        self.log_text.tag_config("info",  foreground=C["muted"])

    def _build_tab_config(self, parent):
        C = CORES

        wrap = tk.Frame(parent, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=C["bg"])
        body.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        bwin = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(bwin, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Sistema
        self._section_info(body, "// sistema", padx=20,
            tooltip="Configurações de inicialização do sistema.")
        sys_card = self._card(body)
        sys_inner = tk.Frame(sys_card, bg=C["section_bg"])
        sys_inner.pack(fill="x", padx=10, pady=(8, 10))
        self.autostart_var = tk.BooleanVar(value=_get_autostart())
        tk.Checkbutton(sys_inner, variable=self.autostart_var,
                       text=" Iniciar com o Windows (minimizado na bandeja)",
                       font=("Consolas", 10),
                       bg=C["section_bg"], fg=C["text"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_autostart).pack(anchor="w")
        self._field_info(sys_inner,
            "Quando ativo, o BrunoPonto inicia automaticamente com o Windows "
            "já minimizado na bandeja do sistema.\n\n"
            "Requer que o executável esteja em sua localização definitiva.").pack(anchor="w", pady=(2, 0))

        # Telegram
        self._section_info(body, "// telegram", padx=20,
            tooltip="Configurações do bot do Telegram para receber notificações automáticas após cada batida de ponto.")
        tg_card = self._card(body)
        tg_g = tk.Frame(tg_card, bg=C["section_bg"])
        tg_g.pack(fill="x", padx=10, pady=(8, 4))
        tg_g.columnconfigure(0, weight=1)

        # Token com ⓘ
        tk.Label(tg_g, text="Token", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).grid(
                     row=0, column=0, sticky="w", pady=(0, 0))
        self._field_info(tg_g, "Token gerado pelo @BotFather no Telegram.\nFormato: 123456789:AABBccDDee...").grid(
            row=0, column=0, sticky="w", padx=(44, 0))
        self.telegram_token_var = tk.StringVar(value=self.cfg.get("telegram_token", ""))
        _te = tk.Frame(tg_g, bg=C["section_bg"])
        _te.grid(row=0, column=0, sticky="ew", pady=(16, 3))
        _e = tk.Entry(_te, textvariable=self.telegram_token_var, show="●",
                      font=("Consolas", 11), bg=C["input_bg"], fg=C["green"],
                      insertbackground=C["green"], relief="flat", bd=4)
        _e.pack(side="left", fill="x", expand=True)
        def _tog_tok(e=_e):
            e.config(show="" if e.cget("show") else "●")
        tk.Button(_te, text="mostrar", font=("Consolas", 8),
                  bg=C["section_bg"], fg=C["muted"], relief="flat",
                  cursor="hand2", command=_tog_tok, padx=4).pack(side="right")

        # Chat ID com ⓘ
        tk.Label(tg_g, text="Chat ID", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).grid(
                     row=1, column=0, sticky="w", pady=(4, 0))
        self._field_info(tg_g, "ID do chat ou grupo que receberá as notificações.\nUse o @userinfobot no Telegram para descobrir o seu ID.").grid(
            row=1, column=0, sticky="w", padx=(58, 0), pady=(4, 0))
        self.telegram_chat_id_var = tk.StringVar(value=self.cfg.get("telegram_chat_id", ""))
        _ci = tk.Entry(tg_g, textvariable=self.telegram_chat_id_var,
                       font=("Consolas", 11), bg=C["input_bg"], fg=C["green"],
                       insertbackground=C["green"], relief="flat", bd=4)
        _ci.grid(row=2, column=0, sticky="ew", pady=(2, 4))

        # Mensagem com ⓘ
        msg_lbl_row = tk.Frame(tg_card, bg=C["section_bg"])
        msg_lbl_row.pack(anchor="w", padx=10, pady=(4, 0))
        tk.Label(msg_lbl_row, text="Mensagem", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        self._field_info(msg_lbl_row,
            "Texto enviado ao Telegram após cada batida.\n\n"
            "Variáveis disponíveis:\n"
            "  {dia_semana}      → ex: Quarta-feira\n"
            "  {data}            → ex: 24/06/2026\n"
            "  {hora}            → ex: 08:00\n"
            "  {versao}          → versão do app\n"
            "  {dia_semana_prox} → ex: Quinta-feira\n"
            "  {data_prox}       → ex: 25/06/2026\n"
            "  {hora_prox}       → ex: 12:00").pack(side="left", padx=(4, 0))
        self.telegram_msg_text = tk.Text(tg_card,
                                         font=("Consolas", 10),
                                         bg=C["input_bg"], fg=C["green"],
                                         insertbackground=C["green"],
                                         relief="flat", bd=4,
                                         height=5, wrap="word")
        self.telegram_msg_text.insert("1.0",
            self.cfg.get("telegram_mensagem", _MSG_PADRAO))
        self.telegram_msg_text.pack(fill="x", padx=10, pady=(2, 4))
        tk.Label(tg_card,
                 text="variáveis: {dia_semana}  {data}  {hora}  {versao}  {dia_semana_prox}  {data_prox}  {hora_prox}",
                 font=("Consolas", 8), bg=C["section_bg"],
                 fg=C["muted"]).pack(anchor="w", padx=10, pady=(0, 4))
        tg_btns = tk.Frame(tg_card, bg=C["section_bg"])
        tg_btns.pack(fill="x", padx=10, pady=(0, 10))
        self._mk_btn(tg_btns, "Testar Telegram",
                     self._testar_telegram).pack(side="left")
        self._mk_btn(tg_btns, "Salvar", self._salvar_config_tab,
                     solid=True).pack(side="right")

        # Watchdog
        self._section_info(body, "// watchdog", padx=20,
            tooltip="Monitora se o programa está ativo.\nEnvia alerta no Telegram se ficar sem atividade por mais tempo que a tolerância configurada.")
        wd_card = self._card(body)
        wd_inner = tk.Frame(wd_card, bg=C["section_bg"])
        wd_inner.pack(fill="x", padx=10, pady=(8, 10))
        self.watchdog_var = tk.BooleanVar(value=self.cfg.get("watchdog_ativo", True))
        tk.Checkbutton(wd_inner, variable=self.watchdog_var,
                       text=" Ativo",
                       font=("Consolas", 10),
                       bg=C["section_bg"], fg=C["text"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_watchdog).pack(anchor="w")
        wd_row = tk.Frame(wd_inner, bg=C["section_bg"])
        wd_row.pack(anchor="w", pady=(6, 0))
        tk.Label(wd_row, text="Tolerância:", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        self.watchdog_horas_var = tk.StringVar(
            value=str(self.cfg.get("watchdog_horas", 2)))
        tk.Entry(wd_row, textvariable=self.watchdog_horas_var,
                 font=("Consolas", 10), width=4,
                 bg=C["input_bg"], fg=C["green"],
                 insertbackground=C["green"],
                 relief="flat", bd=3, justify="center").pack(side="left", padx=(6, 4))
        tk.Label(wd_row, text="horas sem atividade para alertar",
                 font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        self._field_info(wd_row,
            "Quantidade de horas sem nenhuma atividade do scheduler antes de enviar o alerta.\n\nPadrão: 2h. Aumente se a máquina costuma ficar sem ponto para bater por períodos longos.").pack(side="left", padx=(6, 0))
        self.watchdog_horas_var.trace_add("write", self._salvar_watchdog)
        last = self.cfg.get("last_heartbeat")
        last_txt = datetime.fromisoformat(last).strftime("%d/%m/%Y %H:%M") if last else "—"
        self._wd_last_lbl = tk.Label(wd_inner, text=f"último heartbeat: {last_txt}",
                                      font=("Consolas", 8),
                                      bg=C["section_bg"], fg=C["muted"])
        self._wd_last_lbl.pack(anchor="w", pady=(6, 0))

        # Demora
        self._section_info(body, "// demora no selenium", padx=20,
            tooltip="Alerta quando o navegador demora mais que o esperado para registrar o ponto.\nPode indicar lentidão na rede, no servidor ou no computador.")
        dm_card = self._card(body)
        dm_inner = tk.Frame(dm_card, bg=C["section_bg"])
        dm_inner.pack(fill="x", padx=10, pady=(8, 10))
        self.demora_var = tk.BooleanVar(value=self.cfg.get("alerta_demora_ativo", True))
        tk.Checkbutton(dm_inner, variable=self.demora_var,
                       text=" Ativo",
                       font=("Consolas", 10),
                       bg=C["section_bg"], fg=C["text"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_demora).pack(anchor="w")
        dm_row = tk.Frame(dm_inner, bg=C["section_bg"])
        dm_row.pack(anchor="w", pady=(6, 0))
        tk.Label(dm_row, text="Limite:", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        self.demora_seg_var = tk.StringVar(
            value=str(self.cfg.get("alerta_demora_seg", 60)))
        tk.Entry(dm_row, textvariable=self.demora_seg_var,
                 font=("Consolas", 10), width=4,
                 bg=C["input_bg"], fg=C["green"],
                 insertbackground=C["green"],
                 relief="flat", bd=3, justify="center").pack(side="left", padx=(6, 4))
        tk.Label(dm_row, text="segundos para alertar por lentidão",
                 font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        self._field_info(dm_row,
            "Tempo máximo em segundos que o selenium pode demorar para abrir o navegador e registrar o ponto.\n\nSe ultrapassar, você recebe alerta no Telegram.\nPadrão: 60s.").pack(side="left", padx=(6, 0))
        self.demora_seg_var.trace_add("write", self._salvar_demora)

        # ── Férias ────────────────────────────────────────────
        self._section_info(body, "// férias", padx=20,
            tooltip="Suspende todas as batidas durante o período de férias.\nNenhum schedule será executado entre as datas configuradas.")
        vac_card = self._card(body)
        vac_inner = tk.Frame(vac_card, bg=C["section_bg"])
        vac_inner.pack(fill="x", padx=10, pady=(8, 10))

        vac_top = tk.Frame(vac_inner, bg=C["section_bg"])
        vac_top.pack(fill="x")
        self.ferias_var = tk.BooleanVar(value=self.cfg.get("ferias_ativo", False))
        tk.Checkbutton(vac_top, variable=self.ferias_var,
                       text=" Modo férias ativo",
                       font=("Consolas", 10),
                       bg=C["section_bg"], fg=C["text"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_ferias).pack(side="left")

        datas_row = tk.Frame(vac_inner, bg=C["section_bg"])
        datas_row.pack(anchor="w", pady=(8, 0))

        tk.Label(datas_row, text="Início:", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        ini_raw = self.cfg.get("ferias_inicio") or ""
        ini_br  = EditarScheduleWindow._iso_to_br(ini_raw) if ini_raw else ""
        self.ferias_inicio_var = tk.StringVar(value=ini_br)
        tk.Entry(datas_row, textvariable=self.ferias_inicio_var,
                 font=("Consolas", 10), width=10,
                 bg=C["input_bg"], fg=C["green"],
                 insertbackground=C["green"],
                 relief="flat", bd=3,
                 justify="center").pack(side="left", padx=(6, 12))
        self._field_info(datas_row, "Data de início das férias no formato DD/MM/AAAA.").pack(side="left", padx=(0, 12))

        tk.Label(datas_row, text="Fim:", font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        fim_raw = self.cfg.get("ferias_fim") or ""
        fim_br  = EditarScheduleWindow._iso_to_br(fim_raw) if fim_raw else ""
        self.ferias_fim_var = tk.StringVar(value=fim_br)
        tk.Entry(datas_row, textvariable=self.ferias_fim_var,
                 font=("Consolas", 10), width=10,
                 bg=C["input_bg"], fg=C["green"],
                 insertbackground=C["green"],
                 relief="flat", bd=3,
                 justify="center").pack(side="left", padx=(6, 6))
        self._field_info(datas_row, "Data de fim das férias no formato DD/MM/AAAA.").pack(side="left", padx=(0, 0))

        self._mk_btn(vac_inner, "Salvar férias", self._salvar_ferias,
                     solid=True).pack(anchor="e", pady=(10, 0))

        # ── Notificações ──────────────────────────────────────
        self._section_info(body, "// notificações", padx=20,
            tooltip="Ativa ou desativa todas as notificações nativas do Windows.\nInclui: aviso 5 min antes, confirmação de batida e resultado de teste.")
        notif_card = self._card(body)
        notif_inner = tk.Frame(notif_card, bg=C["section_bg"])
        notif_inner.pack(fill="x", padx=10, pady=(8, 10))

        self.notif_var = tk.BooleanVar(value=self.cfg.get("notificacoes_ativas", True))
        tk.Checkbutton(notif_inner, variable=self.notif_var,
                       text=" Notificações ativas",
                       font=("Consolas", 10),
                       bg=C["section_bg"], fg=C["text"],
                       selectcolor=C["input_bg"],
                       activebackground=C["section_bg"],
                       activeforeground=C["green"],
                       command=self._toggle_notificacoes).pack(anchor="w")
        tk.Label(notif_inner,
                 text="Usa notificações nativas do Windows para todos os avisos.",
                 font=("Consolas", 8), bg=C["section_bg"], fg=C["muted"]).pack(anchor="w", pady=(4, 0))

        # ── Healthchecks.io ───────────────────────────────────
        self._section_info(body, "// healthchecks.io", padx=20,
            tooltip="Dead man's switch: o app envia um ping a cada 5 minutos.\nSe os pings pararem, o healthchecks.io avisa você no Telegram.\nCrie um check em healthchecks.io e cole a URL de ping aqui.")
        hc_card = self._card(body)
        hc_inner = tk.Frame(hc_card, bg=C["section_bg"])
        hc_inner.pack(fill="x", padx=10, pady=(8, 10))

        tk.Label(hc_inner, text="URL de ping:",
                 font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(anchor="w")
        hc_url_row = tk.Frame(hc_inner, bg=C["section_bg"])
        hc_url_row.pack(fill="x", pady=(4, 0))
        self.hc_url_var = tk.StringVar(
            value=self.cfg.get("hc_ping_url", ""))
        tk.Entry(hc_url_row, textvariable=self.hc_url_var,
                 font=("Consolas", 10), width=48,
                 bg=C["input_bg"], fg=C["green"],
                 insertbackground=C["green"],
                 relief="flat", bd=3).pack(side="left", padx=(0, 6))
        self._field_info(hc_url_row,
            "URL fornecida pelo healthchecks.io para este check.\nExemplo: https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx").pack(side="left")

        hc_btn_row = tk.Frame(hc_inner, bg=C["section_bg"])
        hc_btn_row.pack(anchor="e", pady=(10, 0))
        self._mk_btn(hc_btn_row, "Testar ping", self._testar_hc).pack(side="left", padx=(0, 8))
        self._mk_btn(hc_btn_row, "Salvar", self._salvar_hc, solid=True).pack(side="left")

    def _section(self, parent, titulo, padx=0):
        f = tk.Frame(parent, bg=CORES["bg"])
        f.pack(fill="x", padx=padx, pady=(12, 4))
        tk.Label(f, text=titulo, font=("Consolas", 8, "bold"),
                 bg=CORES["bg"], fg=CORES["green"]).pack(side="left")
        tk.Frame(f, bg=CORES["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=5)

    def _section_info(self, parent, titulo, tooltip, padx=0):
        f = tk.Frame(parent, bg=CORES["bg"])
        f.pack(fill="x", padx=padx, pady=(12, 4))
        tk.Label(f, text=titulo, font=("Consolas", 8, "bold"),
                 bg=CORES["bg"], fg=CORES["green"]).pack(side="left")
        ib = tk.Label(f, text=" ⓘ", font=("Consolas", 10),
                      bg=CORES["bg"], fg=CORES["muted"], cursor="hand2")
        ib.pack(side="left")
        _Tooltip(ib, tooltip)
        tk.Frame(f, bg=CORES["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(4, 0), pady=5)

    def _field_info(self, parent, texto):
        ib = tk.Label(parent, text=" ⓘ", font=("Consolas", 10),
                      bg=CORES["section_bg"], fg=CORES["muted"], cursor="hand2")
        _Tooltip(ib, texto)
        return ib

    def _card(self, parent):
        f = tk.Frame(parent, bg=CORES["section_bg"],
                     highlightbackground=CORES["border"],
                     highlightthickness=1)
        f.pack(fill="x", padx=20, pady=(0, 4))
        return f

    def _mk_btn(self, parent, txt, cmd, solid=False):
        C = CORES
        if solid:
            return tk.Button(parent, text=txt,
                             bg=C["green"], fg=C["bg"],
                             font=("Consolas", 10, "bold"),
                             relief="flat", cursor="hand2",
                             activebackground=C["green_d"],
                             activeforeground=C["bg"],
                             command=cmd, padx=10)
        return tk.Button(parent, text=txt,
                         bg=C["section_bg"], fg=C["muted"],
                         font=("Consolas", 9),
                         relief="flat", cursor="hand2",
                         highlightbackground=C["border"],
                         highlightthickness=1,
                         activebackground=C["section_bg"],
                         activeforeground=C["green"],
                         command=cmd, padx=8)

    def _neon_btn(self, parent, txt, cmd, dim=False, solid=False):
        return self._mk_btn(parent, txt, cmd, solid=solid)

    def _grid_input(self, parent, label, attr, value, r, c,
                    show="", toggle=False, span=1):
        C = CORES
        frame = tk.Frame(parent, bg=C["section_bg"])
        frame.grid(row=r, column=c, columnspan=span,
                   sticky="ew", padx=(0 if c == 0 else 8, 0), pady=3)
        tk.Label(frame, text=label, font=("Consolas", 9),
                 bg=C["section_bg"], fg=C["muted"]).pack(anchor="w")
        entry_row = tk.Frame(frame, bg=C["section_bg"])
        entry_row.pack(fill="x")
        var = tk.StringVar(value=value)
        setattr(self, attr, var)
        e = tk.Entry(entry_row, textvariable=var, show=show,
                     font=("Consolas", 11),
                     bg=C["input_bg"], fg=C["green"],
                     insertbackground=C["green"],
                     relief="flat", bd=4)
        e.pack(side="left", fill="x", expand=True)
        if toggle and show:
            def _tog(entry=e, char=show):
                if entry.cget("show") == "":
                    entry.config(show=char)
                    btn.config(text="mostrar")
                else:
                    entry.config(show="")
                    btn.config(text="ocultar")
            btn = tk.Button(entry_row, text="mostrar",
                            font=("Consolas", 8), bg=C["section_bg"],
                            fg=C["muted"], relief="flat", cursor="hand2",
                            activebackground=C["section_bg"],
                            activeforeground=C["green"],
                            command=_tog, padx=4)
            btn.pack(side="right")

    def _row_input(self, parent, label, attr, value, show="", toggle=False):
        C = CORES
        row = tk.Frame(parent, bg=C["section_bg"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=f"{label:<20}", anchor="w",
                 font=("Consolas", 10),
                 bg=C["section_bg"], fg=C["muted"]).pack(side="left")
        var = tk.StringVar(value=value)
        setattr(self, attr, var)
        e = tk.Entry(row, textvariable=var, show=show,
                     font=("Consolas", 11),
                     bg=C["input_bg"], fg=C["green"],
                     insertbackground=C["green"],
                     relief="flat", bd=4, width=20)
        e.pack(side="left")
        if toggle and show:
            def _toggle(entry=e, char=show):
                if entry.cget("show") == "":
                    entry.config(show=char)
                    btn.config(text="mostrar")
                else:
                    entry.config(show="")
                    btn.config(text="ocultar")
            btn = tk.Button(row, text="mostrar",
                            bg=C["section_bg"], fg=C["muted"],
                            font=("Consolas", 9),
                            relief="flat", cursor="hand2",
                            highlightbackground=C["border"],
                            highlightthickness=1,
                            activebackground=C["section_bg"],
                            activeforeground=C["green"],
                            command=_toggle)
            btn.pack(side="left", padx=(6, 0))

    def _render_schedules(self):
        for w in self._sched_inner.winfo_children():
            w.destroy()

        C = CORES
        DIAS_ABR = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        schedules = self.cfg.get("schedules", [])

        if not schedules:
            tk.Label(self._sched_inner, text="nenhum schedule cadastrado.",
                     font=("Consolas", 9), bg=C["section_bg"],
                     fg=C["muted"]).pack(anchor="w", padx=8, pady=8)
            return

        for i, s in enumerate(schedules):
            ativo = s.get("ativo", True)
            cor   = C["green"] if ativo else C["muted"]

            row = tk.Frame(self._sched_inner, bg=C["section_bg"],
                           highlightbackground=C["border"], highlightthickness=1)
            row.pack(fill="x", pady=1)
            row.columnconfigure(1, weight=1)

            # coluna 0: botão ●
            tk.Button(row, text="●",
                      font=("Consolas", 13), relief="flat", cursor="hand2",
                      bg=C["section_bg"], fg=cor,
                      activebackground=C["section_bg"],
                      activeforeground=C["green"] if ativo else C["muted"],
                      command=lambda idx=i: self._toggle_schedule_ativo(idx),
                      padx=6, pady=0).grid(row=0, column=0, rowspan=2, sticky="ns")

            # coluna 1: nome (linha 0) + badges (linha 1)
            tk.Label(row, text=s["nome"], font=("Consolas", 10, "bold"),
                     bg=C["section_bg"], fg=cor,
                     anchor="w").grid(row=0, column=1, sticky="w", pady=(4, 0))

            badges = tk.Frame(row, bg=C["section_bg"])
            badges.grid(row=1, column=1, sticky="w", pady=(0, 4))

            horas_str = " | ".join(s.get("horarios", []))
            tk.Label(badges, text=f" {horas_str} ",
                     font=("Consolas", 9),
                     bg=C["input_bg"], fg=C["text"],
                     padx=4, pady=1).pack(side="left", padx=(0, 4))

            dias_str = " ".join(DIAS_ABR[d] for d in sorted(s.get("dias", [])))
            tk.Label(badges, text=f" {dias_str} ",
                     font=("Consolas", 8),
                     bg=C["input_bg"],
                     fg=C["amber"] if s.get("data_fim") else C["muted"],
                     padx=2, pady=1).pack(side="left", padx=(0, 4))

            if s.get("data_fim"):
                fim_br = EditarScheduleWindow._iso_to_br(s["data_fim"])
                tk.Label(badges, text=f"até {fim_br}",
                         font=("Consolas", 8), bg=C["section_bg"],
                         fg=C["amber"]).pack(side="left")

            # coluna 2: botões ação
            btns = tk.Frame(row, bg=C["section_bg"])
            btns.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(4, 2))
            tk.Button(btns, text="✏",
                      bg=C["section_bg"], fg=C["muted"],
                      font=("Segoe UI Emoji", 11), relief="flat", cursor="hand2",
                      activebackground=C["section_bg"], activeforeground=C["green"],
                      command=lambda idx=i: self._abrir_editar_schedule(idx),
                      padx=4).pack(side="left")
            tk.Button(btns, text="🗑",
                      bg=C["section_bg"], fg=C["red"],
                      font=("Segoe UI Emoji", 11), relief="flat", cursor="hand2",
                      activebackground=C["section_bg"], activeforeground=C["red"],
                      command=lambda idx=i: self._remover_schedule(idx),
                      padx=4).pack(side="left")

    # ── Lógica de UI ─────────────────────────────────

    def _salvar_credenciais(self):
        self.cfg["codigo_empregador"] = self.codigo_var.get().strip()
        self.cfg["pin"]               = self.pin_var.get().strip()
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self.add_log("Credenciais salvas.", "ok")
        messagebox.showinfo(APP_NAME, "Credenciais salvas com sucesso!", parent=self.root)

    def _testar_telegram(self):
        self.cfg["telegram_token"]    = self.telegram_token_var.get().strip()
        self.cfg["telegram_chat_id"]  = self.telegram_chat_id_var.get().strip()
        self.cfg["telegram_mensagem"] = self.telegram_msg_text.get("1.0", "end-1c").strip()
        save_config(self.cfg)
        self._enviar_telegram(_msg_telegram("HH:MM", modo_teste=False,
            template=self.cfg.get("telegram_mensagem", ""), cfg=self.cfg))

    def _salvar_config_tab(self):
        self.cfg["telegram_token"]      = self.telegram_token_var.get().strip()
        self.cfg["telegram_chat_id"]    = self.telegram_chat_id_var.get().strip()
        self.cfg["telegram_mensagem"]   = self.telegram_msg_text.get("1.0", "end-1c").strip()
        self.cfg["watchdog_ativo"]      = self.watchdog_var.get()
        try:
            self.cfg["watchdog_horas"]  = int(self.watchdog_horas_var.get() or 2)
        except ValueError:
            pass
        self.cfg["alerta_demora_ativo"] = self.demora_var.get()
        try:
            self.cfg["alerta_demora_seg"] = int(self.demora_seg_var.get() or 60)
        except ValueError:
            pass
        save_config(self.cfg)
        self._tg_registrar_comandos()
        self.add_log("Configurações salvas.", "ok")
        messagebox.showinfo(APP_NAME, "Configurações salvas com sucesso!", parent=self.root)

    def _toggle_modo(self):
        self.cfg["modo_teste"] = self.modo_var.get()
        save_config(self.cfg)
        self._atualizar_banner()
        modo = "TESTE" if self.cfg["modo_teste"] else "REAL"
        self.add_log(f"Modo alterado para: {modo}", "info")

    def _toggle_autostart(self):
        enable = self.autostart_var.get()
        _set_autostart(enable)
        estado = "ativado" if enable else "desativado"
        self.add_log(f"Iniciar com Windows {estado}.", "ok" if enable else "info")

    def _toggle_watchdog(self):
        self.cfg["watchdog_ativo"] = self.watchdog_var.get()
        save_config(self.cfg)
        estado = "ativado" if self.cfg["watchdog_ativo"] else "desativado"
        self.add_log(f"Watchdog {estado}.", "ok" if self.cfg["watchdog_ativo"] else "info")

    def _salvar_watchdog(self, *_):
        try:
            horas = int(self.watchdog_horas_var.get())
            if horas > 0:
                self.cfg["watchdog_horas"] = horas
                save_config(self.cfg)
        except ValueError:
            pass

    def _toggle_demora(self):
        self.cfg["alerta_demora_ativo"] = self.demora_var.get()
        save_config(self.cfg)
        estado = "ativado" if self.cfg["alerta_demora_ativo"] else "desativado"
        self.add_log(f"Alerta de demora {estado}.", "ok" if self.cfg["alerta_demora_ativo"] else "info")

    def _salvar_demora(self, *_):
        try:
            seg = int(self.demora_seg_var.get())
            if seg > 0:
                self.cfg["alerta_demora_seg"] = seg
                save_config(self.cfg)
        except ValueError:
            pass

    # ── Férias ────────────────────────────────────────────────

    def _toggle_ferias(self):
        self.cfg["ferias_ativo"] = self.ferias_var.get()
        save_config(self.cfg)
        estado = "ativado" if self.cfg["ferias_ativo"] else "desativado"
        self.add_log(f"Modo férias {estado}.", "ok" if self.cfg["ferias_ativo"] else "info")

    def _salvar_ferias(self):
        def _br_to_iso(br: str) -> str:
            try:
                d, m, y = br.strip().split("/")
                return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
            except Exception:
                return ""
        ini = _br_to_iso(self.ferias_inicio_var.get())
        fim = _br_to_iso(self.ferias_fim_var.get())
        self.cfg["ferias_ativo"]  = self.ferias_var.get()
        self.cfg["ferias_inicio"] = ini or None
        self.cfg["ferias_fim"]    = fim or None
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self.add_log("Configurações de férias salvas.", "ok")

    def _toggle_notificacoes(self):
        self.cfg["notificacoes_ativas"] = self.notif_var.get()
        save_config(self.cfg)
        estado = "ativadas" if self.cfg["notificacoes_ativas"] else "desativadas"
        self.add_log(f"Notificações {estado}.", "ok" if self.cfg["notificacoes_ativas"] else "info")

    # ── Healthchecks.io ───────────────────────────────────────

    def _salvar_hc(self):
        url = self.hc_url_var.get().strip()
        self.cfg["hc_ping_url"] = url
        save_config(self.cfg)
        self.add_log("URL do healthchecks.io salva.", "ok")

    def _testar_hc(self):
        url = self.hc_url_var.get().strip()
        if not url:
            self.add_log("Informe a URL do healthchecks.io antes de testar.", "warn")
            return
        def _ping():
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "BrunoPonto"}),
                    timeout=10)
                self.after(0, lambda: self.add_log("Ping enviado com sucesso.", "ok"))
            except Exception as e:
                self.after(0, lambda: self.add_log(f"Erro ao enviar ping: {e}", "erro"))
        threading.Thread(target=_ping, daemon=True).start()

    def _atualizar_banner(self):
        C = CORES
        if self.cfg["modo_teste"]:
            self.teste_banner.config(bg="#451a03")
            self.teste_lbl.config(
                text="● [ TEST_MODE = true ]  hover no botão — sem click",
                bg="#451a03", fg=C["amber"])
            self._banner_side_lbl.config(bg="#451a03", fg=C["muted"])
        else:
            self.teste_banner.config(bg=C["bg"])
            self.teste_lbl.config(text="", bg=C["bg"], fg=C["bg"])
            self._banner_side_lbl.config(bg=C["bg"], fg=C["bg"])

    def _abrir_adicionar_schedule(self):
        EditarScheduleWindow(
            self.root,
            existing_schedules=self.cfg.get("schedules", []),
            callback=self._on_schedule_salvo,
        )

    def _abrir_editar_schedule(self, idx):
        EditarScheduleWindow(
            self.root,
            schedule_entry=self.cfg["schedules"][idx],
            existing_schedules=self.cfg.get("schedules", []),
            callback=self._on_schedule_salvo,
            edit_index=idx,
        )

    def _on_schedule_salvo(self, entry, edit_index=None):
        schedules = self.cfg.setdefault("schedules", [])
        if edit_index is None:
            schedules.append(entry)
            acao = "adicionado"
        else:
            schedules[edit_index] = entry
            acao = "atualizado"
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self._render_schedules()
        self._atualizar_prox()
        self.add_log(f"Schedule '{entry['nome']}' {acao}.", "ok")
        icone    = "➕" if edit_index is None else "✏️"
        _dias    = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
        dias_str  = ", ".join(_dias[d] for d in sorted(entry.get("dias", [])))
        horas_str = " | ".join(sorted(entry.get("horarios", [])))
        self._tg_send(f"📅 {icone} Schedule {acao}: {entry['nome']}\n🕐 {horas_str}\n📆 {dias_str}")

    def _toggle_schedule_ativo(self, idx):
        schedules = self.cfg.get("schedules", [])
        s = schedules[idx]
        ativando = not s.get("ativo", True)
        if ativando:
            horas_s = set(s.get("horarios", [s.get("horario", "")]))
            dias_s  = set(s.get("dias", []))
            for i, outro in enumerate(schedules):
                if i == idx or not outro.get("ativo", True):
                    continue
                horas_o = set(outro.get("horarios", [outro.get("horario", "")]))
                conflito = horas_s & horas_o
                if conflito and dias_s & set(outro.get("dias", [])):
                    horas_str = ", ".join(sorted(conflito))
                    messagebox.showerror(
                        "Conflito de horário",
                        f"O schedule '{outro.get('nome', '')}' já está ativo com o mesmo horário ({horas_str}).",
                        parent=self.root)
                    return
        s["ativo"] = ativando
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self._render_schedules()
        self._atualizar_prox()
        estado = "habilitado" if s["ativo"] else "desabilitado"
        self.add_log(f"Schedule '{s['nome']}' {estado}.", "ok" if s["ativo"] else "info")
        icone = "✅" if s["ativo"] else "🔕"
        self._tg_send(f"{icone} Schedule {estado}: {s['nome']}")

    def _remover_schedule(self, idx):
        schedules = self.cfg.get("schedules", [])
        nome = schedules[idx].get("nome", "")
        schedules.pop(idx)
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self._render_schedules()
        self._atualizar_prox()
        self.add_log(f"Schedule '{nome}' removido.", "info")
        self._tg_send(f"🗑 Schedule removido: {nome}")

    def _testar_agora(self):
        hora_agora = datetime.now().strftime("%H:%M")
        executar_acao(self.cfg, self, hora_agora)
        if self.cfg.get("modo_teste"):
            self.modo_var.set(False)
            self._toggle_modo()

    def show_alert(self, hora, agora, modo_teste):
        if not self.cfg.get("notificacoes_ativas", True):
            return
        if modo_teste:
            _win_toast("Bruno Ponto  [ TESTE ]", f"Hover executado — {hora}  ({agora})")
        else:
            _win_toast("Bruno Ponto  ✓", f"Ponto registrado às {hora}  ({agora})")

    def _enviar_telegram(self, mensagem: str):
        token   = self.cfg.get("telegram_token",   "").strip()
        chat_id = self.cfg.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return
        def _enviar():
            try:
                url  = f"https://api.telegram.org/bot{token}/sendMessage"
                data = urllib.parse.urlencode({"chat_id": chat_id, "text": mensagem}).encode()
                urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
                self.root.after(0, lambda: self.add_log("Telegram: mensagem enviada.", "ok"))
            except Exception as e:
                log.error(f"Telegram erro: {e}")
                self.root.after(0, lambda: self.add_log(f"Telegram erro: {e}", "erro"))
        threading.Thread(target=_enviar, daemon=True).start()

    def add_log(self, msg: str, tag: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        linha = f"[{ts}]  {msg}\n"
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, linha, tag)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _atualizar_prox(self):
        prox = proximo_ponto(self.cfg)
        if prox:
            delta = prox - datetime.now()
            total = int(delta.total_seconds())
            if total < 0:
                txt = "Aguardando próximo dia..."
            else:
                h, rem = divmod(total, 3600)
                m, s   = divmod(rem, 60)
                txt = f"Próxima batida: {prox.strftime('%H:%M')}"
        else:
            txt = "Nenhum horário agendado."
        self.prox_lbl.config(text=txt)
        self.root.after(30_000, self._atualizar_prox)

    def _tick_relogio(self):
        now   = datetime.now()
        dia   = DIAS_PT[now.weekday()]
        agora = f"{dia}, {now.strftime('%d/%m/%Y  %H:%M:%S')}"
        self.relogio_lbl.config(text=agora)
        prox = proximo_ponto(self.cfg)
        if self._tray_estado != "executando":
            if prox:
                seg = (prox - now).total_seconds()
                novo = "alerta" if 0 <= seg <= 300 else "normal"
            else:
                novo = "normal"
            if novo != self._tray_estado:
                self._set_tray_estado(novo)
        if HAS_TRAY and self._tray_icon and self._tray_estado != "executando":
            if prox:
                rest = int((prox - now).total_seconds())
                h_r, rem = divmod(max(rest, 0), 3600)
                m_r, s_r = divmod(rem, 60)
                if h_r > 0:
                    tempo = f"{h_r}h {m_r:02d}m"
                elif m_r > 0:
                    tempo = f"{m_r}m {s_r:02d}s"
                else:
                    tempo = f"{s_r}s"
                self._tray_icon.title = f"Bruno Ponto  —  próximo às {prox.strftime('%H:%M')} (em {tempo})"
            else:
                self._tray_icon.title = "Bruno Ponto"
        self._atualizar_barra(prox, now)
        self.root.after(1000, self._tick_relogio)

    def _atualizar_barra(self, prox, now):
        C   = CORES
        w   = self._prog_canvas.winfo_width()
        if w < 4:
            self.root.after(100, lambda: self._atualizar_barra(prox, now))
            return
        self._prog_canvas.delete("all")
        if not prox:
            self._prog_canvas.create_rectangle(0, 0, w, 6, fill=C["input_bg"], outline="")
            self._prog_lbl.config(text="")
            return
        rest = (prox - now).total_seconds()
        if rest < 0:
            rest = 0
        prev = _horario_anterior(self.cfg)
        if prev:
            total = (prox - prev).total_seconds()
        else:
            total = max(rest, 3600)
        pct = rest / total if total > 0 else 0
        pct = max(0.0, min(1.0, pct))
        if rest <= 600:
            cor = C["amber"]
        elif rest <= 1800:
            cor = "#A3E635"
        else:
            cor = C["green"]
        self._prog_canvas.create_rectangle(0, 0, w, 6, fill=C["input_bg"], outline="")
        filled = int(w * pct)
        if filled > 0:
            self._prog_canvas.create_rectangle(0, 0, filled, 6, fill=cor, outline="")
        h_r = int(rest // 3600)
        m_r = int((rest % 3600) // 60)
        s_r = int(rest % 60)
        if h_r > 0:
            tempo = f"em {h_r}h {m_r:02d}m"
        elif m_r > 0:
            tempo = f"em {m_r}m {s_r:02d}s"
        else:
            tempo = f"em {s_r}s"
        self._prog_lbl.config(text=tempo, fg=cor)

    def _set_tray_estado(self, estado: str):
        self._tray_estado = estado
        if not HAS_TRAY or self._tray_icon is None:
            return
        if estado == "executando":
            icone = _ICONE_VERMELHO
            titulo = "Bruno Ponto — registrando ponto..."
        elif estado == "alerta":
            icone = _ICONE_AMARELO
            titulo = "Bruno Ponto — ponto em breve!"
        else:
            icone = _ICONE_VERDE
            titulo = "Bruno Ponto"
        self._tray_icon.icon  = icone
        self._tray_icon.title = titulo

    # ── Scheduler ────────────────────────────────────

    def _run_scheduler(self):
        rebuild_schedule(self.cfg, self)
        _hb_counter = 0
        while True:
            schedule.run_pending()
            _hb_counter += 1
            if _hb_counter >= 6:    # 6 × 10s = 1 min
                _hb_counter = 0
                self._update_heartbeat()
                url = self.cfg.get("hc_ping_url", "").strip()
                if url:
                    def _ping(u=url):
                        try:
                            urllib.request.urlopen(
                                urllib.request.Request(u, headers={"User-Agent": "BrunoPonto"}),
                                timeout=10)
                        except Exception as e:
                            log.warning(f"healthchecks.io ping erro: {e}")
                    threading.Thread(target=_ping, daemon=True).start()
            time.sleep(10)

    # ── Watchdog ─────────────────────────────────────

    def _update_heartbeat(self):
        self.cfg["last_heartbeat"] = datetime.now().isoformat()
        save_config(self.cfg)
        if hasattr(self, "_wd_last_lbl"):
            ts = datetime.now().strftime("%d/%m/%Y %H:%M")
            self.root.after(0, lambda: self._wd_last_lbl.config(text=f"último heartbeat: {ts}"))

    def _watchdog_loop(self):
        for _ in range(360):          # aguarda 1h antes do primeiro check
            time.sleep(10)
        while True:
            self._check_watchdog()
            for _ in range(180):      # checa a cada 30min (180 × 10s)
                time.sleep(10)

    def _check_watchdog(self):
        if not self.cfg.get("watchdog_ativo", True):
            return
        last = self.cfg.get("last_heartbeat")
        if not last:
            return
        try:
            dt    = datetime.fromisoformat(last)
            horas = int(self.cfg.get("watchdog_horas", 2))
            diff  = (datetime.now() - dt).total_seconds()
            if diff > horas * 3600:
                ts  = dt.strftime("%d/%m/%Y %H:%M")
                msg = (
                    f"⚠️ bruno.ponto pode ter parado!\n"
                    f"Último heartbeat: {ts}\n"
                    f"Sem atividade há mais de {horas}h.\n\n"
                    f"Verifique se o programa está rodando 🔴"
                )
                self._enviar_telegram(msg)
                log.warning(f"Watchdog: sem heartbeat há +{horas}h (último: {ts})")
        except Exception as e:
            log.error(f"Watchdog erro: {e}")

    # ── Telegram bot — receber comandos ─────────────────

    def _telegram_polling_loop(self):
        offset = 0
        while True:
            token   = self.cfg.get("telegram_token",   "").strip()
            chat_id = self.cfg.get("telegram_chat_id", "").strip()
            if not token or not chat_id:
                time.sleep(30)
                continue
            try:
                url = (f"https://api.telegram.org/bot{token}"
                       f"/getUpdates?offset={offset}&timeout=5")
                req = urllib.request.Request(url, headers={"User-Agent": "BrunoPonto"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode())
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1

                    cb = upd.get("callback_query")
                    if cb:
                        sender = str(cb.get("from", {}).get("id", ""))
                        if sender == chat_id:
                            self._processar_callback_telegram(
                                cb.get("data", ""), cb.get("id", "")
                            )
                        continue

                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    sender = str(msg.get("chat", {}).get("id", ""))
                    if sender != chat_id:
                        continue
                    texto = (msg.get("text") or "").strip().lower()
                    if texto:
                        self._processar_cmd_telegram(texto)
            except Exception as e:
                log.error(f"Telegram polling: {e}")
                time.sleep(15)
                continue
            time.sleep(8)

    def _tg_registrar_comandos(self):
        token = self.cfg.get("telegram_token", "").strip()
        if not token:
            return
        comandos = [
            {"command": "menu",        "description": "Abre o menu interativo com botões"},
            {"command": "bater",       "description": "Registra o ponto agora (com confirmação)"},
            {"command": "teste_bater", "description": "Registra em modo teste (sem clicar)"},
            {"command": "ping",        "description": "Confirma que o app está ativo"},
            {"command": "status",      "description": "Modo, próxima batida e heartbeat"},
            {"command": "schedules",   "description": "Lista todos os agendamentos"},
            {"command": "ferias",      "description": "Status do modo férias"},
            {"command": "log",         "description": "Últimas 5 entradas do log"},
            {"command": "dia",         "description": "Batidas reais de hoje"},
            {"command": "semana",      "description": "Batidas reais dos últimos 7 dias"},
            {"command": "mes",         "description": "Batidas reais dos últimos 30 dias"},
            {"command": "teste_d",     "description": "Testes de hoje"},
            {"command": "teste_s",     "description": "Testes dos últimos 7 dias"},
            {"command": "teste_m",     "description": "Testes dos últimos 30 dias"},
        ]
        def _registrar():
            try:
                url     = f"https://api.telegram.org/bot{token}/setMyCommands"
                payload = json.dumps({"commands": comandos}).encode("utf-8")
                req     = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    resp = json.loads(r.read().decode())
                if resp.get("ok"):
                    log.info("Telegram: comandos registrados com sucesso.")
                else:
                    log.error(f"Telegram setMyCommands falhou: {resp}")
            except Exception as e:
                log.error(f"Telegram setMyCommands erro: {e}")
        threading.Thread(target=_registrar, daemon=True).start()

    def _tg_send_inline(self, texto: str, linhas: list):
        """linhas: lista de listas de (texto_botao, callback_data)."""
        token   = self.cfg.get("telegram_token",   "").strip()
        chat_id = self.cfg.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return
        def _enviar():
            try:
                url      = f"https://api.telegram.org/bot{token}/sendMessage"
                keyboard = {"inline_keyboard": [
                    [{"text": t, "callback_data": d} for t, d in linha]
                    for linha in linhas
                ]}
                data     = urllib.parse.urlencode({
                    "chat_id":      chat_id,
                    "text":         texto,
                    "reply_markup": json.dumps(keyboard),
                }).encode()
                urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
            except Exception as e:
                log.error(f"Telegram inline erro: {e}")
        threading.Thread(target=_enviar, daemon=True).start()

    def _tg_answer_callback(self, callback_id: str, texto: str = ""):
        token = self.cfg.get("telegram_token", "").strip()
        if not token:
            return
        def _answer():
            try:
                url  = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
                data = urllib.parse.urlencode({"callback_query_id": callback_id, "text": texto}).encode()
                urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
            except Exception as e:
                log.error(f"Telegram answer callback erro: {e}")
        threading.Thread(target=_answer, daemon=True).start()

    def _processar_callback_telegram(self, data: str, callback_id: str):
        if data == "bater_sim":
            hora_agora = datetime.now().strftime("%H:%M")
            modo = "TESTE 🧪" if self.cfg.get("modo_teste") else "REAL ✅"
            self._tg_answer_callback(callback_id, "Registrando...")
            self._tg_send(f"⏳ Registrando ponto às {hora_agora}...\nModo: {modo}")
            executar_acao(self.cfg, self, hora_agora)
        elif data == "bater_nao":
            self._tg_answer_callback(callback_id, "Cancelado.")
            self._tg_send("❌ Registro cancelado.")
        elif data.startswith("/"):
            self._tg_answer_callback(callback_id)
            self._processar_cmd_telegram(data)

    def _tg_send(self, texto: str):
        token   = self.cfg.get("telegram_token",   "").strip()
        chat_id = self.cfg.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return
        def _enviar():
            try:
                url  = f"https://api.telegram.org/bot{token}/sendMessage"
                data = urllib.parse.urlencode(
                    {"chat_id": chat_id, "text": texto}
                ).encode()
                urllib.request.urlopen(
                    urllib.request.Request(url, data=data), timeout=10
                )
            except Exception as e:
                log.error(f"Telegram resposta erro: {e}")
        threading.Thread(target=_enviar, daemon=True).start()

    def _processar_cmd_telegram(self, cmd: str):
        _ABR = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

        if cmd in ("/?", "/menu"):
            self._tg_send_inline(
                "📋 Bruno Ponto — selecione um comando:",
                [
                    [("🟢 Bater ponto", "/bater"), ("🧪 Teste bater", "/teste_bater")],
                    [("📡 Ping", "/ping"), ("📊 Status", "/status")],
                    [("📋 Schedules", "/schedules"), ("🏖 Férias", "/ferias")],
                    [("📄 Log", "/log")],
                    [("📅 Hoje", "/dia"), ("📅 7 dias", "/semana"), ("📅 30 dias", "/mes")],
                    [("🧪 Testes hoje", "/teste_d"), ("🧪 7 dias", "/teste_s"), ("🧪 30 dias", "/teste_m")],
                ]
            )

        elif cmd == "/bater":
            hora_agora = datetime.now().strftime("%H:%M")
            modo = "TESTE 🧪" if self.cfg.get("modo_teste") else "REAL ✅"
            self._tg_send_inline(
                f"🕐 Registrar ponto agora às {hora_agora}?\nModo: {modo}",
                [[("Sim ✅", "bater_sim"), ("Não ❌", "bater_nao")]]
            )

        elif cmd == "/teste_bater":
            hora_agora = datetime.now().strftime("%H:%M")
            self._tg_send(f"🧪 Iniciando registro em modo TESTE às {hora_agora}...")
            cfg_teste = {**self.cfg, "modo_teste": True}
            executar_acao(cfg_teste, self, hora_agora)

        elif cmd == "/ping":
            agora = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
            self._tg_send(f"🟢 Bruno Ponto está ativo.\n⏱ {agora}")

        elif cmd == "/status":
            modo = "TESTE 🧪" if self.cfg.get("modo_teste") else "REAL ✅"
            prox = proximo_ponto(self.cfg)
            if prox:
                delta = prox - datetime.now()
                total = int(delta.total_seconds())
                h, rem = divmod(max(total, 0), 3600)
                m, _   = divmod(rem, 60)
                prox_txt = f"{prox.strftime('%H:%M')} (em {h:02d}h {m:02d}m)"
            else:
                prox_txt = "Nenhum horário agendado"
            last     = self.cfg.get("last_heartbeat")
            last_txt = (datetime.fromisoformat(last).strftime("%d/%m/%Y %H:%M")
                        if last else "—")
            ferias   = "ATIVO 🏖" if self.cfg.get("ferias_ativo") else "inativo"
            self._tg_send(
                f"📊 Status — Bruno Ponto\n\n"
                f"🔄 Modo: {modo}\n"
                f"📅 Próxima batida: {prox_txt}\n"
                f"💓 Heartbeat: {last_txt}\n"
                f"🏖 Férias: {ferias}"
            )

        elif cmd == "/schedules":
            sched = self.cfg.get("schedules", [])
            if not sched:
                self._tg_send("📋 Nenhum schedule configurado.")
                return
            linhas = ["📋 Schedules\n"]
            for s in sched:
                ativo  = "✅" if s.get("ativo", True) else "⏸"
                horas  = " | ".join(s.get("horarios", []))
                dias   = " ".join(_ABR[d] for d in sorted(s.get("dias", [])))
                ini_br = EditarScheduleWindow._iso_to_br(s.get("data_inicio") or "")
                fim_br = EditarScheduleWindow._iso_to_br(s.get("data_fim") or "")
                if ini_br and fim_br:
                    vig = f"   {ini_br} → {fim_br}"
                elif ini_br:
                    vig = f"   desde {ini_br}"
                else:
                    vig = ""
                bloco = f"{ativo} {s['nome']}\n   {horas}\n   {dias}"
                if vig:
                    bloco += f"\n{vig}"
                linhas.append(bloco)
            self._tg_send("\n\n".join(linhas))

        elif cmd in ("/ferias", "/férias"):
            if not self.cfg.get("ferias_ativo", False):
                self._tg_send("🏖 Modo férias: INATIVO")
            else:
                ini = EditarScheduleWindow._iso_to_br(self.cfg.get("ferias_inicio") or "")
                fim = EditarScheduleWindow._iso_to_br(self.cfg.get("ferias_fim") or "")
                datas = f"\n📅 {ini} → {fim}" if (ini or fim) else ""
                self._tg_send(f"🏖 Modo férias: ATIVO{datas}")

        elif cmd == "/log":
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    linhas = [l.strip() for l in f if l.strip()]
                ultimas = linhas[-5:]
                limpas = []
                for l in ultimas:
                    partes = l.split("  ", 2)
                    hora   = partes[0][11:16] if len(partes[0]) >= 16 else ""
                    msg_l  = partes[2]        if len(partes) >= 3    else l
                    limpas.append(f"[{hora}] {msg_l}")
                self._tg_send("📄 Últimas 5 entradas:\n\n" + "\n".join(limpas))
            except Exception as e:
                self._tg_send(f"❌ Erro ao ler log: {e}")

        elif cmd == "/dia":
            hoje = datetime.now().date()
            abr  = _ABR[hoje.weekday()]
            self._cmd_batidas_periodo(
                f"📋 Batidas de hoje — {abr}, {hoje.strftime('%d/%m/%Y')}",
                hoje, filtro="real")

        elif cmd == "/semana":
            from_d = (datetime.now() - timedelta(days=6)).date()
            self._cmd_batidas_periodo("📋 Últimos 7 dias", from_d, filtro="real")

        elif cmd in ("/mes", "/mês"):
            from_d = (datetime.now() - timedelta(days=29)).date()
            self._cmd_batidas_periodo("📋 Últimos 30 dias", from_d, filtro="real")

        elif cmd == "/teste_d":
            hoje = datetime.now().date()
            abr  = _ABR[hoje.weekday()]
            self._cmd_batidas_periodo(
                f"🧪 Testes de hoje — {abr}, {hoje.strftime('%d/%m/%Y')}",
                hoje, filtro="teste")

        elif cmd == "/teste_s":
            from_d = (datetime.now() - timedelta(days=6)).date()
            self._cmd_batidas_periodo("🧪 Testes — últimos 7 dias", from_d, filtro="teste")

        elif cmd == "/teste_m":
            from_d = (datetime.now() - timedelta(days=29)).date()
            self._cmd_batidas_periodo("🧪 Testes — últimos 30 dias", from_d, filtro="teste")

    def _cmd_batidas_periodo(self, titulo: str, from_date, filtro: str = None):
        _ABR    = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        batidas = _ler_batidas_log(from_date)
        linhas  = [f"{titulo}\n"]
        tem     = False
        for data, regs in batidas:
            filtradas = [(h, t) for h, t in regs if filtro is None or t == filtro]
            if not filtradas:
                continue
            tem = True
            abr = _ABR[data.weekday()]
            linhas.append(f"\n📅 {abr} {data.strftime('%d/%m/%Y')}")
            for hora, tipo in sorted(filtradas):
                icone = "✅" if tipo == "real" else "🧪"
                linhas.append(f"  {icone} {hora}")
        if not tem:
            self._tg_send(f"{titulo}\n\nNenhum registro encontrado.")
            return
        msg = "\n".join(linhas)
        if len(msg) > 4000:
            msg = msg[:3997] + "..."
        self._tg_send(msg)

    # ── Bandeja do sistema ────────────────────────────

    def _setup_tray(self):
        if not HAS_TRAY:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", self._restaurar_janela, default=True),
            pystray.MenuItem("Fechar", self._fechar_app),
        )
        self._tray_icon = pystray.Icon(
            "BrunoPonto", _criar_icone_tray(), "Bruno Ponto", menu
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _on_unmap(self, event):
        if event.widget is self.root:
            self.root.after(0, self._check_minimize)

    def _check_minimize(self):
        if self.root.state() == "iconic":
            self._minimizar_para_tray()

    def _minimizar_para_tray(self):
        self.root.withdraw()

    def _restaurar_janela(self, icon=None, item=None):
        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))

    def _fechar_app(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def run(self):
        self.add_log("Bruno Ponto iniciado. Aguardando horários...", "info")
        if not HAS_SELENIUM:
            self.add_log(
                "Aviso: selenium não instalado — modo real desabilitado.  "
                "(pip install selenium)", "erro")
        self.root.mainloop()


# ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app = BrunoPontoApp()
    app.run()
