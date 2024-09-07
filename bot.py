import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import random
import time
import sys
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import tempfile
import argparse
from selenium.common.exceptions import TimeoutException, WebDriverException
from threading import Lock

# Konfigurasi logging
logging.basicConfig(level=logging.INFO)

# Fungsi untuk membaca data dari file
def load_data_from_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = [line.strip() for line in file if line.strip()]
        return data
    except FileNotFoundError:
        print(f"File {file_path} tidak ditemukan.")
        sys.exit(1)

# Memuat daftar User-Agent dan Proxy dari file
user_agents = load_data_from_file('user_agents.txt')
proxies = load_data_from_file('proxies.txt')  # Format proxies.txt: username:password@ip:port

def signal_handler(sig, frame):
    print('Menghentikan bot...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Counter untuk jumlah berhasil dan gagal
success_count = 0
failure_count = 0
total_attempts = 0
max_visits = 0
lock = Lock()

def create_proxy_extension(proxy):
    """Membuat ekstensi Chrome untuk menangani proxy dengan autentikasi."""
    try:
        credentials, host_port = proxy.split('@', 1)
        username, password = credentials.split(':', 1)
        host, port = host_port.split(':', 1)
    except ValueError as e:
        print(f"Error parsing proxy: {proxy}. Format proxy yang tidak sesuai: {proxy}. Expected format: username:password@ip:port")
        return None

    # Manifest file untuk ekstensi Chrome
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"]
        },
        "minimum_chrome_version":"22.0.0"
    }
    """

    # Background script untuk ekstensi
    background_js = f"""
    var config = {{
            mode: "fixed_servers",
            rules: {{
            singleProxy: {{
                scheme: "http",
                host: "{host}",
                port: parseInt({port})
            }},
            bypassList: ["localhost"]
            }}
        }};

    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

    function callbackFn(details) {{
        return {{
            authCredentials: {{
                username: "{username}",
                password: "{password}"
            }}
        }};
    }}

    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        {{urls: ["<all_urls>"]}},
        ['blocking']
    );
    """

    # Membuat direktori sementara untuk ekstensi
    extension_dir = tempfile.mkdtemp()

    # Menyimpan manifest.json dan background.js ke direktori sementara
    with open(os.path.join(extension_dir, 'manifest.json'), 'w') as manifest_file:
        manifest_file.write(manifest_json)

    with open(os.path.join(extension_dir, 'background.js'), 'w') as background_file:
        background_file.write(background_js)

    return extension_dir

def run_bot_with_proxy(url, proxy, retries=3):
    global success_count, failure_count, total_attempts, max_visits

    with lock:
        # Cek apakah total percobaan sudah mencapai maksimum
        if total_attempts >= max_visits:
            return

    user_agent = random.choice(user_agents)
    proxy_extension = create_proxy_extension(proxy)

    # Jika pembuatan ekstensi proxy gagal, lewati proxy ini
    if not proxy_extension:
        with lock:
            failure_count += 1
        return

    for attempt in range(retries):
        with lock:
            # Cek apakah total percobaan sudah mencapai maksimum sebelum setiap percobaan ulang
            if total_attempts >= max_visits:
                return
            total_attempts += 1  # Tambahkan ke total percobaan

        chrome_options = Options()
        chrome_options.add_argument(f'user-agent={user_agent}')
        chrome_options.add_argument('--ignore-certificate-errors')
        chrome_options.add_argument('--allow-insecure-localhost')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument(f'--load-extension={proxy_extension}')
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')

        try:
            # Menginisialisasi driver dengan opsi yang sudah diatur
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
            driver.set_page_load_timeout(30)  # Mengatur timeout untuk loading halaman
            
            # Mengunjungi situs target
            driver.get(url)

            # Tunggu sampai halaman selesai dimuat
            if driver.execute_script("return document.readyState") == "complete":
                print(f"Berhasil mengunjungi {url} dengan proxy {proxy}")
                with lock:
                    success_count += 1
                break
            else:
                raise TimeoutException("Halaman tidak selesai dimuat.")

            # Simulasi perilaku manusia dengan scroll dan tunggu acak
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            wait_time = random.uniform(2, 5)  # Waktu tunggu acak antara 2 hingga 5 detik
            print(f"Menunggu di situs selama {wait_time:.2f} detik...")
            time.sleep(wait_time)

        except (TimeoutException, WebDriverException) as e:
            print(f"Attempt {attempt + 1} gagal mengunjungi {url} dengan proxy {proxy}: {e}")
            if attempt == retries - 1:
                with lock:
                    failure_count += 1

        finally:
            driver.quit()

# Menjalankan bot secara paralel tanpa delay antar proxy
def run_bots_in_parallel(target_url, number_of_visits):
    global max_visits
    max_visits = number_of_visits  # Set maksimum kunjungan yang diizinkan

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(run_bot_with_proxy, target_url, proxies[i % len(proxies)]) for i in range(number_of_visits)]

        for future in as_completed(futures):
            future.result()

def main():
    parser = argparse.ArgumentParser(description="Bot dengan input URL dan jumlah kunjungan dinamis")
    parser.add_argument('--url', type=str, required=True, help='Masukkan URL yang ingin dikunjungi')
    parser.add_argument('--visits', type=int, required=True, help='Jumlah kunjungan yang diinginkan')
    args = parser.parse_args()

    # Menjalankan bot dengan input URL dan jumlah kunjungan
    run_bots_in_parallel(args.url, args.visits)

    # Menampilkan hasil setelah script selesai
    print("\n--- Hasil Eksekusi ---")
    print(f"Total kunjungan berhasil: {success_count}")
    print(f"Total kunjungan gagal: {failure_count}")

if __name__ == '__main__':
    main()
