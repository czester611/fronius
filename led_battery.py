#!/usr/bin/env python3
"""
solar_soc_leds.py
=================
Wizualizacja stanu magazynu energii (SOC) na pasku WS2812B (8 diód).

Działanie:
  - Diody 0..(n-2) świecą na NIEBIESKO  (stały stan naładowania)
  - Dioda (n-1) miga:
      ZIELONO  – magazyn się ładuje
      CZERWONO – magazyn się rozładowuje
  - Częstotliwość mrugania = moc_W / 100 Hz  (np. 300W → 3 Hz)
  - Gdy SOC = 0% lub brak mocy – wszystkie diody wygaszone (lub jedno mignięcie co 2s)
  - Gdy SOC = 100% – wszystkie 8 diód niebieskich, bez mrugania

Wymagania:
  pip install rpi_ws281x requests

Uruchomienie (wymaga root dla DMA/PWM):
  sudo python3 solar_soc_leds.py

Konfiguracja poniżej (sekcja CONFIG).
"""

import time
import math
import threading
import requests
from rpi_ws281x import PixelStrip, Color

# ──────────────────────────────────────────────────
#  CONFIG – dostosuj do własnego okablowania
# ──────────────────────────────────────────────────
LED_COUNT      = 8          # liczba diód na pasku
LED_PIN        = 18         # GPIO PWM (BCM); można też użyć 12, 13, 19
LED_FREQ_HZ    = 800_000    # częstotliwość sygnału WS2812B
LED_DMA        = 10         # kanał DMA (nie używaj 5 – system)
LED_BRIGHTNESS = 80         # jasność 0–255
LED_INVERT     = False      # True jeśli używasz konwertera logiki z inwerterem
LED_CHANNEL    = 0          # 0 = GPIO18/12, 1 = GPIO13/19

API_URL        = "http://192.168.5.244/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
POLL_INTERVAL  = 2.0        # interwał odpytywania API [sekundy]

# Minimalna moc [W] żeby uznać ruch energii za aktywny (eliminuje szum)
MIN_POWER_W    = 20

# Kolory
COLOR_BLUE     = Color(0,   0,   255)   # stały stan naładowania
COLOR_GREEN    = Color(0,   200, 0)     # ładowanie
COLOR_RED      = Color(220, 0,   0)     # rozładowanie
COLOR_OFF      = Color(0,   0,   0)
# ──────────────────────────────────────────────────


def fetch_soc_and_power() -> tuple[float, float]:
    """
    Pobiera SOC [%] i moc baterii [W] z lokalnego API Fronius.
    Zwraca (soc, power_w).
      soc     – 0..100
      power_w – dodatnia = ładowanie, ujemna = rozładowanie, 0 = standby
    """
    resp = requests.get(API_URL, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    # Struktura odpowiedzi Fronius Solar API v1
    site = data["Body"]["Data"]["Site"]
    inverter = data["Body"]["Data"]["Inverters"]["1"]

    # SOC jest w Inverters.1.SOC
    soc = float(inverter.get("SOC", 0) or 0)

    # P_Akku w Site: ujemne = rozładowanie, dodatnie = ładowanie
    power_w = float(site.get("P_Akku", 0) or 0)
    return soc, power_w


class LedController:
    """Steruje paskiem WS2812B w osobnym wątku mrugającym."""

    def __init__(self, strip: PixelStrip):
        self.strip = strip
        self._lock = threading.Lock()

        # Stan przekazywany wątkowi mrugającemu
        self._n_lit     = 0      # liczba lit diód (0–8)
        self._blink_color = None # Color lub None (brak mrugania)
        self._blink_hz  = 0.5    # częstotliwość mrugania

        self._running = True
        self._thread = threading.Thread(target=self._blink_loop, daemon=True)
        self._thread.start()

    def update(self, soc: float, power_w: float):
        """Przelicz SOC i moc na parametry wyświetlania."""
        # Liczba lit diód – proporcjonalnie do SOC
        n = round(soc / 100.0 * LED_COUNT)
        n = max(0, min(LED_COUNT, n))

        # Kierunek i prędkość mrugania
        if n == 0 or abs(power_w) < MIN_POWER_W:
            blink_color = None
            blink_hz = 0.0
        elif power_w < 0:
            # Ładowanie → zielony
            blink_color = COLOR_GREEN
            blink_hz = abs(power_w) / 200.0
        else:
            # Rozładowanie → czerwony
            blink_color = COLOR_RED
            blink_hz = abs(power_w) / 200.0

        # Ogranicz HZ do rozsądnego zakresu
        blink_hz = max(0.1, min(blink_hz, 20.0))

        with self._lock:
            self._n_lit       = n
            self._blink_color = blink_color
            self._blink_hz    = blink_hz

        print(f"  SOC={soc:.1f}%  P_akku={power_w:+.0f}W  "
              f"lit={n}  blink={'off' if blink_color is None else ('green' if blink_color == COLOR_GREEN else 'red')}  "
              f"hz={blink_hz:.2f}")

    def _blink_loop(self):
        """Główna pętla mrugania – działa w tle."""
        blink_state = True   # True = dioda miga do koloru, False = wygaszona
        last_toggle = time.time()

        while self._running:
            with self._lock:
                n          = self._n_lit
                blink_color = self._blink_color
                blink_hz   = self._blink_hz

            now = time.time()

            # Przełącz stan mrugania
            if blink_color is not None and blink_hz > 0:
                period = 1.0 / blink_hz
                half   = period / 2.0
                if now - last_toggle >= half:
                    blink_state  = not blink_state
                    last_toggle  = now
            else:
                blink_state = True  # bez mrugania – traktuj jako włączone

            self._render(n, blink_color, blink_state)

            # Śpij krótko – ale nie dłużej niż pół okresu mrugania
            if blink_color is not None and blink_hz > 0:
                sleep_time = min(0.02, 1.0 / blink_hz / 2.0)
            else:
                sleep_time = 0.05
            time.sleep(sleep_time)

    def _render(self, n: int, blink_color, blink_on: bool):
        """Zapisz kolory na pasek."""
        strip = self.strip
        for i in range(LED_COUNT):
            if i < n - 1:
                # Stałe niebieskie diody (poza ostatnią aktywną)
                strip.setPixelColor(i, COLOR_BLUE)
            elif i == n - 1 and n > 0:
                # Ostatnia aktywna dioda – mruga lub stała
                if blink_color is None:
                    # Brak przepływu mocy – świeć niebiesko (stały SOC)
                    strip.setPixelColor(i, COLOR_BLUE)
                else:
                    strip.setPixelColor(i, blink_color if blink_on else COLOR_OFF)
            else:
                strip.setPixelColor(i, COLOR_OFF)
        strip.show()

    def stop(self):
        self._running = False
        self._thread.join(timeout=2)
        # Wygaś wszystkie diody
        for i in range(LED_COUNT):
            self.strip.setPixelColor(i, COLOR_OFF)
        self.strip.show()


def main():
    print("=== Solar SOC LED display ===")
    print(f"Pasek: {LED_COUNT} diód, GPIO{LED_PIN}, jasność={LED_BRIGHTNESS}")
    print(f"API:   {API_URL}")
    print(f"Odśwież co {POLL_INTERVAL}s\n")

    # Inicjalizacja paska
    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL
    )
    strip.begin()

    controller = LedController(strip)

    try:
        while True:
            try:
                soc, power_w = fetch_soc_and_power()
                controller.update(soc, power_w)
            except requests.RequestException as e:
                print(f"[WARN] Błąd API: {e}")
            except (KeyError, ValueError, TypeError) as e:
                print(f"[WARN] Błąd parsowania odpowiedzi: {e}")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nZatrzymywanie...")
    finally:
        controller.stop()
        print("Diody wygaszone. Do widzenia.")


if __name__ == "__main__":
    main()
