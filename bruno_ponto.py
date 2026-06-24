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

# ──────────────────────────────────────────────────────
#  CONSTANTES
# ──────────────────────────────────────────────────────
APP_NAME    = "Bruno Ponto"
APP_VERSION = "2.0"
URL_PONTO   = "https://app.tangerino.com.br/Tangerino/"

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

def _criar_icone_tray():
    size = 64
    img  = Image.new("RGBA", (size, size), (9, 9, 9, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, size - 8, size - 8], fill="#00FF41")
    draw.polygon([(24, 20), (24, 44), (44, 32)], fill="#090909")
    return img


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
#  SELENIUM — DRIVER E AUTOMAÇÃO
# ──────────────────────────────────────────────────────
_driver      = None
_driver_lock = threading.Lock()


def _criar_driver():
    """Tenta criar WebDriver na ordem: Chrome → Edge → Firefox."""
    erros = []

    # Permissões concedidas automaticamente — evita popups de câmera e localização
    _PREFS = {
        "profile.default_content_setting_values.geolocation":       1,
        "profile.default_content_setting_values.media_stream_camera": 1,
        "profile.default_content_setting_values.media_stream_mic":  1,
        "profile.default_content_setting_values.notifications":     2,
    }

    try:
        opts = ChromeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--use-fake-ui-for-media-stream")   # suprime popup de câmera/mic
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_experimental_option("prefs", _PREFS)
        return webdriver.Chrome(options=opts)
    except Exception as e:
        erros.append(f"Chrome: {e}")

    try:
        opts = EdgeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--use-fake-ui-for-media-stream")
        opts.add_experimental_option("prefs", _PREFS)
        return webdriver.Edge(options=opts)
    except Exception as e:
        erros.append(f"Edge: {e}")

    try:
        opts = FirefoxOptions()
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
# ──────────────────────────────────────────────────────
def _mover_mouse_z():
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

def _msg_telegram(hora_label: str, modo_teste: bool, template: str = "") -> str:
    now        = datetime.now()
    dia_semana = DIAS_PT[now.weekday()]
    data_fmt   = now.strftime("%d/%m/%Y")
    prefixo    = "[TESTE] " if modo_teste else ""
    tmpl       = template.strip() or _MSG_PADRAO
    try:
        corpo = tmpl.format(dia_semana=dia_semana, data=data_fmt,
                            hora=hora_label, versao=APP_VERSION)
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

    prefixo = "[TESTE] " if modo_teste else ""
    msg = f"{prefixo}Abrindo navegador para registrar ponto às {hora_label} ({agora})..."
    log.info(msg)
    app_ref.root.after(0, lambda: app_ref.add_log(msg, "teste" if modo_teste else "info"))

    def _registrar():
        global _driver
        try:
            with _driver_lock:
                if _driver is None or not _driver_ativo(_driver):
                    _driver = _criar_driver()

                _driver.get(URL_PONTO)
                _preencher_formulario(_driver, cfg, modo_teste=modo_teste)

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
                    template=cfg.get("telegram_mensagem", "")))
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
                    template=cfg.get("telegram_mensagem", "")))

        except Exception as e:
            err = f"Erro ao registrar: {e}"
            log.error(err)
            app_ref.root.after(0, lambda: app_ref.add_log(err, "erro"))
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
    def _abrir():
        app_ref.root.deiconify()
        app_ref.root.lift()
        AvisoWindow(app_ref.root, hora_label)
    app_ref.root.after(0, _abrir)

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


# ──────────────────────────────────────────────────────
#  JANELA DE AVISO (5 min antes)
# ──────────────────────────────────────────────────────
class AvisoWindow(tk.Toplevel):
    def __init__(self, parent, hora):
        super().__init__(parent)
        self.title("bruno.ponto :: aviso")
        self.configure(bg=CORES["bg"])
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.update_idletasks()
        w, h = 420, 210
        x = parent.winfo_rootx() + (parent.winfo_width()  - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        tk.Frame(self, bg=CORES["amber"], height=2).pack(fill="x")

        tk.Label(self, text="[ AVISO ]",
                 font=("Consolas", 20, "bold"),
                 bg=CORES["bg"], fg=CORES["amber"]).pack(pady=(18, 4))

        tk.Label(self, text=f"ponto automático em 5 minutos  →  {hora}",
                 font=("Consolas", 11),
                 bg=CORES["bg"], fg=CORES["text"]).pack()

        tk.Label(self, text="// o registro será feito automaticamente",
                 font=("Consolas", 9),
                 bg=CORES["bg"], fg=CORES["muted"]).pack(pady=4)

        tk.Button(self, text="[ fechar ]",
                  bg=CORES["bg"], fg=CORES["amber"],
                  font=("Consolas", 10, "bold"),
                  relief="flat", cursor="hand2",
                  highlightbackground=CORES["amber"],
                  highlightthickness=1,
                  activebackground=CORES["bg"],
                  activeforeground=CORES["amber"],
                  command=self.destroy).pack(pady=14)

        # fecha sozinho 1 min antes de bater o ponto
        self.after(240_000, self.destroy)


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
        for i, s in enumerate(self.existing):
            if i == self.edit_index:
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

        self._build_ui()
        self._tray_icon = None
        self._setup_tray()
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.protocol("WM_DELETE_WINDOW", self._minimizar_para_tray)
        if "--minimized" in sys.argv:
            self.root.after(300, self._minimizar_para_tray)
        self._scheduler_thread = threading.Thread(
            target=self._run_scheduler, daemon=True
        )
        self._scheduler_thread.start()
        self._atualizar_prox()

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

        # Scrollable body
        wrap = tk.Frame(self.root, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        self._body_canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        _sb = tk.Scrollbar(wrap, orient="vertical", command=self._body_canvas.yview)
        body = tk.Frame(self._body_canvas, bg=C["bg"])
        body.bind("<Configure>", lambda e: self._body_canvas.configure(
            scrollregion=self._body_canvas.bbox("all")))
        _bwin = self._body_canvas.create_window((0, 0), window=body, anchor="nw")
        self._body_canvas.configure(yscrollcommand=_sb.set)
        self._body_canvas.bind("<Configure>",
            lambda e: self._body_canvas.itemconfig(_bwin, width=e.width))
        self._body_canvas.pack(side="left", fill="both", expand=True)
        _sb.pack(side="right", fill="y")
        self._body_canvas.bind_all("<MouseWheel>",
            lambda e: self._body_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # ── Credenciais ──
        self._section(body, "// credenciais", padx=20)
        cred_card = self._card(body)

        g = tk.Frame(cred_card, bg=C["section_bg"])
        g.pack(fill="x", padx=10, pady=(8, 4))
        g.columnconfigure(0, weight=1)
        g.columnconfigure(1, weight=1)
        self._grid_input(g, "Cod. Empregador", "codigo_var",
                         self.cfg["codigo_empregador"], r=0, c=0)
        self._grid_input(g, "PIN", "pin_var",
                         self.cfg["pin"], r=0, c=1, show="●", toggle=True)

        self._tg_header = tk.Frame(cred_card, bg=C["section_bg"])
        self._tg_header.pack(fill="x", padx=10, pady=(6, 0))
        self._telegram_visible = False
        self._tg_toggle_btn = tk.Button(
            self._tg_header, text="▶  Telegram",
            font=("Consolas", 9), bg=C["section_bg"], fg=C["muted"],
            relief="flat", cursor="hand2", anchor="w",
            activebackground=C["section_bg"], activeforeground=C["green"],
            command=self._toggle_telegram)
        self._tg_toggle_btn.pack(side="left")
        tk.Frame(self._tg_header, bg=C["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

        self._telegram_frame = tk.Frame(cred_card, bg=C["section_bg"])
        tg_g = tk.Frame(self._telegram_frame, bg=C["section_bg"])
        tg_g.pack(fill="x", pady=4)
        tg_g.columnconfigure(0, weight=1)
        self._grid_input(tg_g, "Token", "telegram_token_var",
                         self.cfg.get("telegram_token", ""), r=0, c=0,
                         show="●", toggle=True)
        self._grid_input(tg_g, "Chat ID", "telegram_chat_id_var",
                         self.cfg.get("telegram_chat_id", ""), r=1, c=0)

        # Mensagem personalizada
        tk.Label(self._telegram_frame, text="Mensagem",
                 font=("Consolas", 9), bg=C["section_bg"],
                 fg=C["muted"]).pack(anchor="w")
        self.telegram_msg_text = tk.Text(self._telegram_frame,
                                         font=("Consolas", 10),
                                         bg=C["input_bg"], fg=C["green"],
                                         insertbackground=C["green"],
                                         relief="flat", bd=4,
                                         height=5, wrap="word")
        self.telegram_msg_text.insert("1.0",
            self.cfg.get("telegram_mensagem", _MSG_PADRAO))
        self.telegram_msg_text.pack(fill="x", pady=(2, 4))
        tk.Label(self._telegram_frame,
                 text="variáveis: {dia_semana}  {data}  {hora}  {versao}",
                 font=("Consolas", 8), bg=C["section_bg"],
                 fg=C["muted"]).pack(anchor="w", pady=(0, 4))

        self._mk_btn(self._telegram_frame, "Testar Telegram",
                     self._testar_telegram).pack(anchor="e", pady=(0, 6))

        self._save_row = tk.Frame(cred_card, bg=C["section_bg"])
        self._save_row.pack(fill="x", padx=10, pady=(6, 10))
        self._mk_btn(self._save_row, "Salvar", self._salvar_credenciais,
                     solid=True).pack(side="right")

        # ── Schedules ──
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

        # ── Modo + Status (side by side) ──
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
        self._tick_relogio()

        # ── Output ──
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

        self._atualizar_banner()

    def _section(self, parent, titulo, padx=0):
        f = tk.Frame(parent, bg=CORES["bg"])
        f.pack(fill="x", padx=padx, pady=(12, 4))
        tk.Label(f, text=titulo, font=("Consolas", 8, "bold"),
                 bg=CORES["bg"], fg=CORES["green"]).pack(side="left")
        tk.Frame(f, bg=CORES["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=5)

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
        self.cfg["telegram_token"]    = self.telegram_token_var.get().strip()
        self.cfg["telegram_chat_id"]  = self.telegram_chat_id_var.get().strip()
        self.cfg["telegram_mensagem"] = self.telegram_msg_text.get("1.0", "end-1c").strip()
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
            template=self.cfg.get("telegram_mensagem", "")))

    def _toggle_modo(self):
        self.cfg["modo_teste"] = self.modo_var.get()
        save_config(self.cfg)
        self._atualizar_banner()
        modo = "TESTE" if self.cfg["modo_teste"] else "REAL"
        self.add_log(f"Modo alterado para: {modo}", "info")

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

    def _toggle_telegram(self):
        if self._telegram_visible:
            self._telegram_frame.pack_forget()
            self._telegram_visible = False
            self._tg_toggle_btn.config(text="▶  Telegram", fg=CORES["muted"])
        else:
            self._telegram_frame.pack(fill="x", padx=10, after=self._tg_header)
            self._telegram_visible = True
            self._tg_toggle_btn.config(text="▼  Telegram", fg=CORES["green"])

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

    def _toggle_schedule_ativo(self, idx):
        s = self.cfg.get("schedules", [])[idx]
        s["ativo"] = not s.get("ativo", True)
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self._render_schedules()
        self._atualizar_prox()
        estado = "habilitado" if s["ativo"] else "desabilitado"
        self.add_log(f"Schedule '{s['nome']}' {estado}.", "ok" if s["ativo"] else "info")

    def _remover_schedule(self, idx):
        schedules = self.cfg.get("schedules", [])
        nome = schedules[idx].get("nome", "")
        schedules.pop(idx)
        save_config(self.cfg)
        rebuild_schedule(self.cfg, self)
        self._render_schedules()
        self._atualizar_prox()
        self.add_log(f"Schedule '{nome}' removido.", "info")

    def _testar_agora(self):
        hora_agora = datetime.now().strftime("%H:%M")
        executar_acao(self.cfg, self, hora_agora)
        if self.cfg.get("modo_teste"):
            self.modo_var.set(False)
            self._toggle_modo()

    def show_alert(self, hora, agora, modo_teste):
        self.root.deiconify()
        self.root.lift()
        AlertaWindow(self.root, hora, agora, modo_teste)

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
                txt = f"Próxima batida: {prox.strftime('%H:%M')}  (em {h:02d}h {m:02d}m)"
        else:
            txt = "Nenhum horário agendado."
        self.prox_lbl.config(text=txt)
        self.root.after(30_000, self._atualizar_prox)

    def _tick_relogio(self):
        now   = datetime.now()
        dia   = DIAS_PT[now.weekday()]
        agora = f"{dia}, {now.strftime('%d/%m/%Y  %H:%M:%S')}"
        self.relogio_lbl.config(text=agora)
        self.root.after(1000, self._tick_relogio)

    # ── Scheduler ────────────────────────────────────

    def _run_scheduler(self):
        rebuild_schedule(self.cfg, self)
        while True:
            schedule.run_pending()
            time.sleep(10)

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
