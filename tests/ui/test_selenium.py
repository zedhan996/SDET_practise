import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = os.getenv("SELENIUM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
WAIT_SECONDS = 10
SCREENSHOT_DIR = Path("reports/selenium")


def test_selenium_login_shows_catalog():
    driver = None

    try:
        driver = webdriver.Edge()
        wait = WebDriverWait(driver, WAIT_SECONDS)

        driver.get(f"{BASE_URL}/app/")

        wait.until(
            EC.visibility_of_element_located((By.ID, "login-view"))
        )

        username_input = driver.find_element(By.ID, "login-username")
        password_input = driver.find_element(By.ID, "login-password")
        username_input.clear()
        password_input.clear()
        username_input.send_keys("admin")
        password_input.send_keys("Admin@123")
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "#login-form button[type='submit']")
            )
        ).click()

        workspace = wait.until(
            EC.visibility_of_element_located((By.ID, "workspace-view"))
        )

        assert driver.title == "商品目录工作台"
        assert "商品目录" in workspace.text

    except Exception:
        if driver is not None:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(
                str(SCREENSHOT_DIR / "login-failure.png")
            )
        raise
    finally:
        if driver is not None:
            driver.quit()
