import time
import os
import sys
import threading
import tkinter as tk

# Fix DPI scaling issue on Windows 10/11
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
import logging

# ================================
# LOGGING SETUP
# ================================
log_dir = os.path.join(os.path.expanduser("~"), "AttendanceScraper_Logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
    ]
)
logger = logging.getLogger(__name__)


# ================================
# SCRAPER CORE
# ================================
class AttendanceScraper:
    def __init__(self, config, log_callback=None, progress_callback=None):
        self.config = config
        self.log = log_callback or print
        self.update_progress = progress_callback or (lambda *a: None)
        self.driver = None
        self.wait = None
        self.all_data = []
        self.is_running = False
        self.is_paused = False
        self.stop_requested = False

    def setup_browser(self):
        self.log("🌐 Setting up Chrome browser...")
        logger.info("Setting up browser")
        options = ChromeOptions()
        options.add_argument('--start-maximized')
        options.add_argument('--disable-blink-features=AutomationControlled')
        if self.config.get('headless'):
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')

        self.log("📦 Auto-downloading ChromeDriver (first time may take a moment)...")
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.implicitly_wait(3)
        self.wait = WebDriverWait(self.driver, 10)
        self.log("✅ Browser ready!\n")
        logger.info("Browser setup complete")

    def login(self):
        try:
            self.log("🔐 Logging in...")
            base_url = self.config['base_url'].rstrip('/')
            self.driver.get(f"{base_url}/authenticate")

            username = self.wait.until(EC.presence_of_element_located((By.NAME, "empcode")))
            password = self.driver.find_element(By.NAME, "password")
            login_btn = self.driver.find_element(By.XPATH, "//button[@type='submit']")

            username.send_keys(self.config['username'])
            password.send_keys(self.config['password'])
            login_btn.click()

            time.sleep(2)
            self.log("✅ Login successful!\n")
            logger.info("Login successful")
            return True
        except Exception as e:
            self.log(f"❌ Login failed: {e}")
            logger.error(f"Login failed: {e}")
            return False

    def setup_attendance_page(self):
        try:
            self.log("📊 Setting up attendance report page...")
            base_url = self.config['base_url'].rstrip('/')
            self.driver.get(f"{base_url}/attendance/attendancereport")

            date_box = self.wait.until(EC.element_to_be_clickable((By.ID, "monthYearPicker")))
            date_box.click()
            done_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='Done']")))
            done_button.click()
            time.sleep(0.5)

            reportlevel = Select(self.wait.until(EC.presence_of_element_located((By.NAME, "reportlevel"))))
            reportlevel.select_by_value("Level1")
            time.sleep(0.5)

            subbusiness = Select(self.wait.until(EC.presence_of_element_located((By.NAME, "subbusiness"))))
            subbusiness.select_by_value("15")
            time.sleep(1)

            self.log("✅ Page setup complete!\n")
            logger.info("Attendance page setup complete")
            return True
        except Exception as e:
            self.log(f"❌ Page setup failed: {e}")
            logger.error(f"Page setup failed: {e}")
            return False

    def get_dropdown_options(self, dropdown_name):
        try:
            select_element = Select(self.driver.find_element(By.NAME, dropdown_name))
            options = []
            for opt in select_element.options:
                value = opt.get_attribute("value")
                text = opt.text.strip()
                if value and value.strip():
                    options.append({'value': value.strip(), 'text': text})
            return options
        except Exception:
            return []

    def select_dropdown(self, dropdown_name, value):
        try:
            select_element = Select(self.driver.find_element(By.NAME, dropdown_name))
            select_element.select_by_value(value)
            time.sleep(0.3)
            return True
        except Exception:
            return False

    def extract_attendance_from_page(self):
        try:
            html = self.driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            table = soup.find('table', class_='table') or soup.find('table')
            if not table:
                return []

            data = []
            tbody = table.find('tbody')
            if not tbody:
                return []

            for row in tbody.find_all('tr'):
                if row.find('td', {'colspan': '10'}):
                    continue
                cols = row.find_all('td')
                if len(cols) >= 10:
                    date = cols[1].text.strip()
                    if date:
                        data.append({
                            'date': date,
                            'check_in_time': cols[2].text.strip(),
                            'check_out_time': cols[3].text.strip(),
                            'check_in_location': cols[4].text.strip(),
                            'check_out_location': cols[5].text.strip()
                        })
            return data
        except Exception as e:
            logger.warning(f"Extraction error: {e}")
            return []

    def scrape_employee_with_retry(self, fme_code, retries=3):
        for attempt in range(retries):
            try:
                if not self.select_dropdown('fmecode', fme_code):
                    return []
                submit_btn = self.driver.find_element(By.NAME, "submit")
                submit_btn.click()
                time.sleep(1.5)
                return self.extract_attendance_from_page()
            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed for {fme_code}: {e}")
                time.sleep(2)
        return []

    def save_data(self, temp=False):
        try:
            if not self.all_data:
                return
            df = pd.DataFrame(self.all_data)
            output_path = self.config['output_file']
            filename = f"temp_{output_path}" if temp else output_path
            df.to_excel(filename, index=False, sheet_name="Attendance")
            if temp:
                self.log(f"💾 Checkpoint saved: {len(self.all_data)} records")
            else:
                self.log(f"\n✅ Final file saved: {filename}")
            logger.info(f"Saved {len(self.all_data)} records to {filename}")
        except Exception as e:
            self.log(f"❌ Save error: {e}")
            logger.error(f"Save error: {e}")

    def run(self):
        self.is_running = True
        self.stop_requested = False
        start_time = time.time()
        total_employees = 0
        current_period = datetime.now().strftime("%B %Y")

        self.log("=" * 60)
        self.log("🚀 ATTENDANCE SCRAPER STARTED")
        self.log(f"📅 Period: {current_period}")
        self.log("=" * 60 + "\n")

        try:
            self.setup_browser()
            if not self.login():
                self.log("❌ Cannot proceed without login.")
                return
            if not self.setup_attendance_page():
                self.log("❌ Cannot setup page.")
                return

            regions = self.get_dropdown_options('regioncode')
            self.log(f"🌍 Found {len(regions)} regions\n")

            total_regions = len(regions)
            for region_idx, region in enumerate(regions, 1):
                if self.stop_requested:
                    break

                region_code = region['value']
                region_name = region['text']
                self.log(f"━━━ Region {region_idx}/{total_regions}: {region_name} ━━━")

                if not self.select_dropdown('regioncode', region_code):
                    continue
                time.sleep(0.5)

                areas = self.get_dropdown_options('areacode')

                for area_idx, area in enumerate(areas, 1):
                    if self.stop_requested:
                        break

                    # Pause support
                    while self.is_paused and not self.stop_requested:
                        time.sleep(0.5)

                    area_code = area['value']
                    area_name = area['text']

                    if not self.select_dropdown('areacode', area_code):
                        continue
                    time.sleep(0.5)

                    employees = self.get_dropdown_options('fmecode')
                    self.log(f"  📍 {area_name}: {len(employees)} employees")

                    for emp_idx, employee in enumerate(employees, 1):
                        if self.stop_requested:
                            break

                        fme_code = employee['value']
                        emp_name = employee['text']

                        records = self.scrape_employee_with_retry(fme_code)
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        for record in records:
                            self.all_data.append({
                                'Region_Code': region_code,
                                'Region_Name': region_name,
                                'Area_Code': area_code,
                                'Area_Name': area_name,
                                'FME_Code': fme_code,
                                'Employee_Name': emp_name,
                                'Date': record['date'],
                                'Check_In_Time': record['check_in_time'],
                                'Check_Out_Time': record['check_out_time'],
                                'Check_In_Location': record['check_in_location'],
                                'Check_Out_Location': record['check_out_location'],
                                'Period': current_period,
                                'Timestamp': timestamp
                            })

                        total_employees += 1

                        # Progress update
                        elapsed = time.time() - start_time
                        rate = total_employees / elapsed if elapsed > 0 else 0
                        eta = (1338 - total_employees) / rate if rate > 0 else 0
                        self.update_progress(
                            total_employees, len(self.all_data),
                            rate, eta, emp_name
                        )

                        if total_employees % 50 == 0:
                            self.save_data(temp=True)

            self.save_data(temp=False)

            elapsed = time.time() - start_time
            self.log("\n" + "=" * 60)
            self.log("🎉 SCRAPING COMPLETE!")
            self.log(f"   Employees  : {total_employees}")
            self.log(f"   Records    : {len(self.all_data)}")
            self.log(f"   Time       : {elapsed/60:.1f} minutes")
            self.log(f"   Output     : {self.config['output_file']}")
            self.log("=" * 60)

        except Exception as e:
            self.log(f"\n❌ Unexpected error: {e}")
            logger.error(f"Unexpected error: {e}", exc_info=True)
            self.save_data(temp=False)
        finally:
            self.is_running = False
            if self.driver:
                self.driver.quit()
                logger.info("Browser closed")


# ================================
# GUI APPLICATION
# ================================
class AttendanceScraperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Attendance Scraper")
        self.root.resizable(True, True)
        self.root.configure(bg="#0f1117")

        self.scraper = None
        self.scraper_thread = None

        self._build_ui()

    def _build_ui(self):
        # ── Fonts & Colors ──
        BG = "#0f1117"
        PANEL = "#1a1d27"
        ACCENT = "#00d4aa"
        ACCENT2 = "#0099ff"
        TEXT = "#e8eaf0"
        MUTED = "#6b7280"
        DANGER = "#ff4757"
        SUCCESS = "#2ed573"

        self.root.configure(bg=BG)

        # ── Header ──
        header = tk.Frame(self.root, bg=ACCENT, height=4)
        header.pack(fill="x")

        title_frame = tk.Frame(self.root, bg=BG, pady=16)
        title_frame.pack(fill="x", padx=24)

        tk.Label(title_frame, text="ATTENDANCE SCRAPER",
                 font=("Courier New", 18, "bold"), bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(title_frame, text="  v2.0 — Automated Data Collector",
                 font=("Courier New", 10), bg=BG, fg=MUTED).pack(side="left", pady=4)

        # ── Main Content ──
        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        # Left column - Config
        left = tk.Frame(content, bg=BG)
        left.pack(side="left", fill="y", padx=(0, 16))

        # Config panel
        config_panel = tk.Frame(left, bg=PANEL, padx=16, pady=16,
                                 highlightbackground=ACCENT, highlightthickness=1)
        config_panel.pack(fill="x", pady=(0, 12))

        tk.Label(config_panel, text="⚙  CONFIGURATION",
                 font=("Courier New", 10, "bold"), bg=PANEL, fg=ACCENT).pack(anchor="w", pady=(0, 12))

        fields = [
            ("Base URL", "base_url", "http://103.209.40.112/gpharma", False),
            ("Username", "username", "su", False),
            ("Password", "password", "tech", True),
        ]

        self.entries = {}
        for label, key, default, secret in fields:
            row = tk.Frame(config_panel, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, width=10, anchor="w",
                     font=("Courier New", 9), bg=PANEL, fg=MUTED).pack(side="left")
            show = "*" if secret else ""
            entry = tk.Entry(row, show=show, width=28,
                             font=("Courier New", 9), bg="#252836",
                             fg=TEXT, insertbackground=ACCENT,
                             relief="flat", bd=4)
            entry.insert(0, default)
            entry.pack(side="left", padx=(8, 0))
            self.entries[key] = entry

        # Output file row
        out_row = tk.Frame(config_panel, bg=PANEL)
        out_row.pack(fill="x", pady=3)
        tk.Label(out_row, text="Output", width=10, anchor="w",
                 font=("Courier New", 9), bg=PANEL, fg=MUTED).pack(side="left")
        self.output_entry = tk.Entry(out_row, width=20,
                                      font=("Courier New", 9), bg="#252836",
                                      fg=TEXT, insertbackground=ACCENT,
                                      relief="flat", bd=4)
        self.output_entry.insert(0, "Attendance_Data.xlsx")
        self.output_entry.pack(side="left", padx=(8, 4))
        tk.Button(out_row, text="📁", font=("Courier New", 9),
                  bg=PANEL, fg=ACCENT, bd=0, cursor="hand2",
                  command=self._browse_output).pack(side="left")

        # Headless checkbox
        self.headless_var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(config_panel, text="Run in background (headless)",
                              variable=self.headless_var,
                              font=("Courier New", 9), bg=PANEL,
                              fg=TEXT, selectcolor=PANEL,
                              activebackground=PANEL, activeforeground=ACCENT)
        chk.pack(anchor="w", pady=(8, 0))

        # ── Control Buttons ──
        btn_frame = tk.Frame(left, bg=BG)
        btn_frame.pack(fill="x", pady=(0, 12))

        self.start_btn = tk.Button(
            btn_frame, text="▶  START SCRAPING",
            font=("Courier New", 10, "bold"),
            bg=ACCENT, fg="#0f1117", relief="flat",
            cursor="hand2", pady=8,
            command=self._start_scraping
        )
        self.start_btn.pack(fill="x", pady=(0, 6))

        btn_row = tk.Frame(btn_frame, bg=BG)
        btn_row.pack(fill="x")

        self.pause_btn = tk.Button(
            btn_row, text="⏸  PAUSE",
            font=("Courier New", 9, "bold"),
            bg="#f39c12", fg="#0f1117", relief="flat",
            cursor="hand2", pady=6, state="disabled",
            command=self._toggle_pause
        )
        self.pause_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.stop_btn = tk.Button(
            btn_row, text="⏹  STOP",
            font=("Courier New", 9, "bold"),
            bg=DANGER, fg="white", relief="flat",
            cursor="hand2", pady=6, state="disabled",
            command=self._stop_scraping
        )
        self.stop_btn.pack(side="left", fill="x", expand=True)

        # ── Stats Panel ──
        stats_panel = tk.Frame(left, bg=PANEL, padx=16, pady=16,
                                highlightbackground="#2a2d3a", highlightthickness=1)
        stats_panel.pack(fill="x")

        tk.Label(stats_panel, text="📊  LIVE STATS",
                 font=("Courier New", 10, "bold"), bg=PANEL, fg=ACCENT2).pack(anchor="w", pady=(0, 10))

        self.stat_vars = {}
        stats = [("Employees", "employees", "0"),
                 ("Records", "records", "0"),
                 ("Speed", "speed", "0.00 /sec"),
                 ("ETA", "eta", "—"),
                 ("Status", "status", "Idle")]

        for label, key, default in stats:
            row = tk.Frame(stats_panel, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=11, anchor="w",
                     font=("Courier New", 9), bg=PANEL, fg=MUTED).pack(side="left")
            var = tk.StringVar(value=default)
            self.stat_vars[key] = var
            tk.Label(row, textvariable=var,
                     font=("Courier New", 9, "bold"), bg=PANEL, fg=TEXT).pack(side="left")

        # ── Progress bar ──
        pb_frame = tk.Frame(stats_panel, bg=PANEL)
        pb_frame.pack(fill="x")
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("green.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor="#252836",
                        borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)
        self.progress_bar = ttk.Progressbar(pb_frame, style="green.Horizontal.TProgressbar",
                                             orient="horizontal", length=240, mode="determinate",
                                             maximum=1338)
        self.progress_bar.pack(fill="x")
        self.progress_label = tk.Label(pb_frame, text="0 / ~1338",
                                        font=("Courier New", 8), bg=PANEL, fg=MUTED)
        self.progress_label.pack(anchor="e")

        # ── Right column - Log ──
        right = tk.Frame(content, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        log_header = tk.Frame(right, bg=BG)
        log_header.pack(fill="x", pady=(0, 6))
        tk.Label(log_header, text="📋  LIVE LOG",
                 font=("Courier New", 10, "bold"), bg=BG, fg=ACCENT2).pack(side="left")
        tk.Button(log_header, text="Clear", font=("Courier New", 8),
                  bg=PANEL, fg=MUTED, bd=0, cursor="hand2",
                  command=lambda: self.log_box.delete(1.0, tk.END)).pack(side="right")

        self.log_box = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, state="disabled",
            font=("Courier New", 9),
            bg="#0a0c12", fg=TEXT,
            insertbackground=ACCENT,
            relief="flat", bd=0,
            padx=10, pady=10
        )
        self.log_box.pack(fill="both", expand=True)

        # Tag colors for log
        self.log_box.tag_config("success", foreground=SUCCESS)
        self.log_box.tag_config("error", foreground=DANGER)
        self.log_box.tag_config("accent", foreground=ACCENT)
        self.log_box.tag_config("info", foreground=ACCENT2)

        # ── Status Bar ──
        status_bar = tk.Frame(self.root, bg=PANEL, height=28)
        status_bar.pack(fill="x", side="bottom")
        self.status_label = tk.Label(status_bar, text="  Ready — Configure and press START",
                                      font=("Courier New", 9), bg=PANEL, fg=MUTED, anchor="w")
        self.status_label.pack(side="left", padx=8, pady=4)
        tk.Label(status_bar, text=f"  Log: {log_file}  ",
                 font=("Courier New", 8), bg=PANEL, fg=MUTED).pack(side="right")

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            title="Choose output file location"
        )
        if path:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, path)

    def _log(self, message):
        def _do():
            self.log_box.configure(state="normal")
            timestamp = datetime.now().strftime("%H:%M:%S")

            tag = None
            if "✅" in message or "complete" in message.lower() or "🎉" in message:
                tag = "success"
            elif "❌" in message or "error" in message.lower() or "failed" in message.lower():
                tag = "error"
            elif "🚀" in message or "━━━" in message or "===" in message:
                tag = "accent"
            elif "📊" in message or "📍" in message or "🌍" in message:
                tag = "info"

            line = f"[{timestamp}] {message}\n"
            if tag:
                self.log_box.insert(tk.END, line, tag)
            else:
                self.log_box.insert(tk.END, line)

            self.log_box.see(tk.END)
            self.log_box.configure(state="disabled")
        self.root.after(0, _do)

    def _update_progress(self, employees, records, rate, eta, current_emp):
        def _do():
            self.stat_vars['employees'].set(str(employees))
            self.stat_vars['records'].set(str(records))
            self.stat_vars['speed'].set(f"{rate:.2f} /sec")
            eta_str = f"{eta/60:.1f} min" if eta > 0 else "—"
            self.stat_vars['eta'].set(eta_str)
            self.stat_vars['status'].set("Running")
            self.progress_bar['value'] = min(employees, 1338)
            self.progress_label.config(text=f"{employees} / ~1338")
            self.status_label.config(text=f"  Processing: {current_emp}")
        self.root.after(0, _do)

    def _start_scraping(self):
        config = {
            'base_url': self.entries['base_url'].get().strip(),
            'username': self.entries['username'].get().strip(),
            'password': self.entries['password'].get().strip(),
            'output_file': self.output_entry.get().strip() or "Attendance_Data.xlsx",
            'headless': self.headless_var.get(),
        }

        if not config['base_url'] or not config['username']:
            messagebox.showerror("Error", "Please fill in all required fields.")
            return

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self.stat_vars['status'].set("Starting...")

        self.scraper = AttendanceScraper(config, self._log, self._update_progress)

        def run_thread():
            self.scraper.run()
            self.root.after(0, self._on_scraping_done)

        self.scraper_thread = threading.Thread(target=run_thread, daemon=True)
        self.scraper_thread.start()

    def _toggle_pause(self):
        if not self.scraper:
            return
        if self.scraper.is_paused:
            self.scraper.is_paused = False
            self.pause_btn.config(text="⏸  PAUSE")
            self.stat_vars['status'].set("Running")
            self._log("▶ Resumed")
        else:
            self.scraper.is_paused = True
            self.pause_btn.config(text="▶  RESUME")
            self.stat_vars['status'].set("Paused")
            self._log("⏸ Paused — click Resume to continue")

    def _stop_scraping(self):
        if self.scraper:
            if messagebox.askyesno("Stop?", "Stop scraping and save current data?"):
                self.scraper.stop_requested = True
                self.scraper.is_paused = False
                self._log("⏹ Stop requested — finishing current employee...")
                self.stat_vars['status'].set("Stopping...")

    def _on_scraping_done(self):
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="⏸  PAUSE")
        self.stop_btn.config(state="disabled")
        self.stat_vars['status'].set("Done ✓")
        self.status_label.config(text="  ✅ Scraping complete! Check your output file.")
        messagebox.showinfo("Done!", f"Scraping complete!\n\nFile saved to:\n{self.scraper.config['output_file']}")


# ================================
# MAIN ENTRY POINT
# ================================
def main():
    root = tk.Tk()

    # Fix font/geometry scaling on high-DPI Windows screens
    try:
        root.tk.call('tk', 'scaling', 1.0)
    except Exception:
        pass

    app = AttendanceScraperApp(root)

    # Center window safely
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    w, h = 820, 680
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
