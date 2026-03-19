import requests
import time
import logging
import os
import json
import threading
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('fronius_monitor.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# --- Konfiguracja ---
FRONIUS_IP    = os.getenv("FRONIUS_IP", "192.168.5.244")
FRONIUS_PORT  = os.getenv("FRONIUS_PORT", "80")
NTFY_TOPIC    = os.getenv("NTFY_TOPIC")        # powiadomienia
NTFY_CMD      = os.getenv("NTFY_CMD")          # komendy przychodzące

# --- Stan dynamiczny (zmienia się w locie) ---
state = {
    "thresholds": [20, 80],      # progi SOC w %
    "poll_interval": 60,          # sekundy
    "triggered": set(),           # progi które już odpaliły alert
    "last_soc": None
}
state_lock = threading.Lock()

# --- Fronius API ---
def get_battery_soc():
    url = f"http://{FRONIUS_IP}:{FRONIUS_PORT}/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        i = data["Body"]["Data"]["Inverters"]["1"]
        s = data["Body"]["Data"]["Site"]
        soc    = float(i["SOC"])
        p_pv   = s.get("P_PV") or 0
        p_akku = s.get("P_Akku") or 0
        p_grid = s.get("P_Grid") or 0
        p_load = abs(s.get("P_Load") or 0)
        log.info(f"SOC: {soc:.1f}% | PV: {p_pv:.0f}W | Bateria: {p_akku:.0f}W | Sieć: {p_grid:.0f}W | Dom: {p_load:.0f}W")
        return soc, p_pv, p_akku, p_grid, p_load
    except Exception as e:
        log.error(f"Błąd pobierania danych: {e}")
        return None, None, None, None, None

# --- Wysyłanie powiadomień ---
def send_ntfy(title, message, priority="default", tags="battery"):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
            data=message.encode("utf-8"),
            timeout=10
        )
    except Exception as e:
        log.error(f"ntfy błąd: {e}")

# --- Obsługa komend ---
def handle_command(cmd):
    cmd = cmd.strip().lower()
    parts = cmd.split()
    log.info(f"Komenda: {cmd}")

    with state_lock:
        if parts[0] == "add" and len(parts) == 2:
            try:
                val = float(parts[1])
                if val not in state["thresholds"]:
                    state["thresholds"].append(val)
                    state["thresholds"].sort()
                send_ntfy(
                    "✅ Alert dodany",
                    f"Dodano próg: {val}%\nAktywne progi: {state['thresholds']}",
                    tags="white_check_mark"
                )
            except ValueError:
                send_ntfy("❌ Błąd", "Użycie: add 80", tags="x")

        elif parts[0] == "remove" and len(parts) == 2:
            try:
                val = float(parts[1])
                if val in state["thresholds"]:
                    state["thresholds"].remove(val)
                    state["triggered"].discard(val)
                send_ntfy(
                    "🗑️ Alert usunięty",
                    f"Usunięto próg: {val}%\nAktywne progi: {state['thresholds']}",
                    tags="wastebasket"
                )
            except ValueError:
                send_ntfy("❌ Błąd", "Użycie: remove 80", tags="x")

        elif parts[0] == "interval" and len(parts) == 2:
            try:
                val = int(parts[1])
                state["poll_interval"] = max(10, val)  # minimum 10s
                send_ntfy(
                    "⏱️ Interwał zmieniony",
                    f"Polling co {state['poll_interval']} sekund",
                    tags="timer_clock"
                )
            except ValueError:
                send_ntfy("❌ Błąd", "Użycie: interval 30", tags="x")

        elif parts[0] == "list":
            progi = state["thresholds"]
            send_ntfy(
                "📋 Aktywne alerty",
                f"Progi SOC: {progi}%\nPolling: co {state['poll_interval']}s\nSOC teraz: {state['last_soc']}%",
                tags="clipboard"
            )

        elif parts[0] == "status":
            soc, pv, akku, grid, load = get_battery_soc()
            if soc:
                kierunek = "ładowanie ↑" if akku and akku < 0 else "rozładowanie ↓"
                send_ntfy(
                    f"🔋 Status: {soc:.1f}%",
                    f"PV: {pv:.0f}W\nBateria: {abs(akku):.0f}W ({kierunek})\nSieć: {grid:.0f}W\nDom: {load:.0f}W\nAlerty: {state['thresholds']}%",
                    tags="bar_chart"
                )

        elif parts[0] == "clear":
            state["thresholds"] = []
            state["triggered"] = set()
            send_ntfy("🗑️ Wyczyszczono", "Wszystkie alerty usunięte", tags="wastebasket")

        else:
            send_ntfy(
                "❓ Nieznana komenda",
                "Dostępne komendy:\nadd 80\nremove 80\nlist\ninterval 30\nstatus\nclear",
                tags="question"
            )

# --- Wątek nasłuchujący komend ---
def command_listener():
    if not NTFY_CMD:
        log.warning("NTFY_CMD nie ustawiony — komendy wyłączone")
        return

    log.info(f"Nasłuchuję komend na: ntfy.sh/{NTFY_CMD}")
    while True:
        try:
            # ntfy stream — blokujące połączenie SSE
            with requests.get(
                f"https://ntfy.sh/{NTFY_CMD}/sse",
                stream=True,
                timeout=None
            ) as r:
                for line in r.iter_lines():
                    if line:
                        line = line.decode("utf-8")
                        if line.startswith("data:"):
                            try:
                                payload = json.loads(line[5:])
                                msg = payload.get("message", "")
                                if msg:
                                    handle_command(msg)
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            log.error(f"Błąd listenera komend: {e} — restartuję za 10s")
            time.sleep(10)

# --- Główna pętla monitorowania ---
def monitor_loop():
    log.info("=== Fronius Monitor start ===")
    send_ntfy(
        "🚀 Monitor uruchomiony",
        f"Aktywne progi: {state['thresholds']}%\nWyślij 'help' po komendy",
        tags="rocket"
    )

    while True:
        soc, *_ = get_battery_soc()

        if soc is not None:
            with state_lock:
                state["last_soc"] = round(soc, 1)
                thresholds = sorted(state["thresholds"])

                for t in thresholds:
                    if soc >= t and t not in state["triggered"]:
                        emoji = "🔋" if t >= 50 else "⚠️"
                        priority = "high" if t >= 80 else "default"
                        send_ntfy(
                            f"{emoji} Bateria {soc:.1f}% (próg {t}%)",
                            f"SOC osiągnął {soc:.1f}%\nPróg alertu: {t}%\nCzas: {datetime.now().strftime('%H:%M')}",
                            priority=priority,
                            tags="battery"
                        )
                        state["triggered"].add(t)

                    elif soc < t - 5:
                        # reset progu gdy SOC spadnie 5% poniżej
                        state["triggered"].discard(t)

                interval = state["poll_interval"]

        time.sleep(interval)

# --- Start ---
if __name__ == "__main__":
    # Wątek komend w tle
    t = threading.Thread(target=command_listener, daemon=True)
    t.start()

    # Główna pętla
    monitor_loop()