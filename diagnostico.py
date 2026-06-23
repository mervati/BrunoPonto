# coding: utf-8
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time

_PREFS = {
    "profile.default_content_setting_values.geolocation":         1,
    "profile.default_content_setting_values.media_stream_camera": 1,
    "profile.default_content_setting_values.media_stream_mic":    1,
    "profile.default_content_setting_values.notifications":       2,
}
opts = Options()
opts.add_argument("--start-maximized")
opts.add_argument("--use-fake-ui-for-media-stream")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
opts.add_experimental_option("useAutomationExtension", False)
opts.add_experimental_option("prefs", _PREFS)

driver = webdriver.Chrome(options=opts)
driver.get("https://app.tangerino.com.br/Tangerino/")

wait = WebDriverWait(driver, 15)
wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(@href,'baterPonto')]")))
print("URL antes do clique:", driver.current_url)

aba = driver.find_element(By.XPATH, "//a[contains(@href,'baterPonto')]")
href = aba.get_attribute("href")
print("href da aba:", href)
aba.click()
time.sleep(2)

print("URL depois do clique:", driver.current_url)

# Tenta encontrar os campos
for campo_id in ["codigoEmpregador", "codigoPin"]:
    try:
        el = wait.until(EC.presence_of_element_located((By.ID, campo_id)))
        print(f"Campo '{campo_id}' encontrado — visível: {el.is_displayed()}, habilitado: {el.is_enabled()}")
    except Exception as e:
        print(f"Campo '{campo_id}' NAO encontrado: {e}")

# Tenta preencher
try:
    cod = driver.find_element(By.ID, "codigoEmpregador")
    cod.click()
    cod.clear()
    cod.send_keys("ZV159")
    print("Código preenchido OK")
except Exception as e:
    print(f"Erro ao preencher código: {e}")

try:
    pin = driver.find_element(By.ID, "codigoPin")
    pin.click()
    pin.clear()
    pin.send_keys("9241")
    print("PIN preenchido OK")
except Exception as e:
    print(f"Erro ao preencher PIN: {e}")

time.sleep(3)
driver.quit()
