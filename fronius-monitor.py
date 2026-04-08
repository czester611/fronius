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

    monitor_loop()
