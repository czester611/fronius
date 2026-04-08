#!/usr/bin/env python3
"""
Prognoza słoneczna + produkcja PV — Ruszcza, gm. Połaniec
Wyświetlacz: SSD1306 128x64 OLED (I2C)
Falownik: Fronius Symo GEN24 10.0 Plus | Instalacja: 3.6 kWp | Południe 30-35°

Model sezonowy skalibrowany na 61 dniach (2026-02-01 do 2026-04-04):
  produkcja [kWh] = clip( 2.18355 × rad
                        - 0.009121 × rad × doy
                        - 1.44181,   0, 26.0 )
  gdzie:
    rad = shortwave_radiation_sum [MJ/m²]  — CAŁKOWITA radiacja (bez sunshine_duration!)
    doy = dzień roku (1=1sty, 94=4kwi, 172=21cze)
  R² = 0.79,  MAE = 2.75 kWh/dzień

Dlaczego bez sunshine_duration:
  Open-Meteo zaniża sunshine_duration dla dni "jasno pochmurnych" (cirrusy, cienkie
  chmury) — shortwave_radiation jest dużo dokładniejszy bo mierzy faktyczną energię.

WAŻNE — przed uruchomieniem ustaw strefę czasową:
  sudo timedatectl set-timezone Europe/Warsaw

Instalacja:
  pip install luma.oled pillow requests
  sudo raspi-config → Interface Options → I2C → Enable

Podłączenie SSD1306:
  VCC → Pin 1 (3.3V) | GND → Pin 6 | SDA → Pin 3 | SCL → Pin 5
  Sprawdź adres: sudo i2cdetect -y 1  (typowo 0x3C lub 0x3D)
"""

import time
import requests
from datetime import datetime

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont

# ── Konfiguracja ─────────────────────────────────────────────────────────────
LAT           = 50.409
LON           = 21.231
KWP           = 3.6
I2C_PORT      = 1
I2C_ADDRESS   = 0x3C    # zmień na 0x3D jeśli i2cdetect pokazuje 3d
INTERVAL_SEC  = 3600    # odświeżanie API co 1h
VIEW_SWITCH   = 6       # zmiana widoku co N sekund
FORECAST_DAYS = 5

# ── Model PV sezonowy ────────────────────────────────────────────────────────
# prod = clip(PV_A*rad + PV_B*rad*doy + PV_C, 0, PV_MAX)
# Efekt sezonowy: w czerwcu (doy≈172) PV_B*doy kompensuje PV_A
# → ta sama radiacja daje ~20% więcej energii w czerwcu niż w lutym
PV_A   =  2.18355   # kWh per MJ/m²
PV_B   = -0.009121  # korekcja sezonowa: kWh per (MJ/m² × dzień_roku)
PV_C   = -1.44181   # offset
PV_MAX = 26.0       # hard cap — empiryczny max (czerwiec pełne słońce)

def doy_today() -> int:
    """Zwraca dzień roku dla dzisiaj."""
    return datetime.now().timetuple().tm_yday

def doy_for(date: datetime) -> int:
    return date.timetuple().tm_yday

def estimate_day(shortwave_mj: float, date: datetime) -> float:
    """Dzienna estymacja produkcji [kWh]."""
    doy = doy_for(date)
    raw = PV_A * shortwave_mj + PV_B * shortwave_mj * doy + PV_C
    return round(max(0.0, min(raw, PV_MAX)), 2)

def estimate_hour(shortwave_wm2: float, date: datetime) -> float:
    """Godzinowa estymacja [kWh] z chwilowej radiacji W/m²."""
    rad_mj = shortwave_wm2 * 3600 / 1_000_000   # W/m² → MJ/m² za 1h
    doy    = doy_for(date)
    raw    = PV_A * rad_mj + PV_B * rad_mj * doy + PV_C / 12.0
    return round(max(0.0, min(raw, PV_MAX / 12.0)), 3)

# ── Fonty ─────────────────────────────────────────────────────────────────────
try:
    FONT_LARGE = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    FONT_MED   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    FONT_SMALL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
    FONT_TINY  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 8)
except Exception:
    FONT_LARGE = FONT_MED = FONT_SMALL = FONT_TINY = ImageFont.load_default()

# ── API ───────────────────────────────────────────────────────────────────────
def fetch_data():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&daily=shortwave_radiation_sum,sunshine_duration,daylight_duration,"
        f"weathercode,temperature_2m_max,temperature_2m_min"
        f"&hourly=shortwave_radiation,sunshine_duration,cloudcover"
        f"&timezone=Europe%2FWarsaw"
        f"&forecast_days={FORECAST_DAYS}"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[API błąd] {e}")
        return None

def parse_data(raw):
    if not raw:
        return None, None

    d = raw["daily"]
    daily = []
    for i, date_str in enumerate(d["time"]):
        date   = datetime.strptime(date_str, "%Y-%m-%d")
        rad    = d["shortwave_radiation_sum"][i]
        sun_h  = d["sunshine_duration"][i] / 3600
        dl_h   = d["daylight_duration"][i] / 3600
        sun_pct = int((sun_h / dl_h * 100)) if dl_h > 0 else 0
        prod   = estimate_day(rad, date)
        daily.append({
            "date":    date,
            "label":   date.strftime("%d.%m"),
            "dow":     ["Pn","Wt","Sr","Cz","Pt","Sb","Nd"][date.weekday()],
            "rad":     rad,
            "sun_h":   round(sun_h, 1),
            "sun_pct": sun_pct,
            "tmax":    round(d["temperature_2m_max"][i]),
            "tmin":    round(d["temperature_2m_min"][i]),
            "wmo":     d["weathercode"][i],
            "prod":    prod,
        })

    # Dane godzinowe — tylko dziś (używa lokalnego czasu Pi = Warsaw)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_dt  = datetime.now().replace(hour=0)
    h = raw["hourly"]
    hourly = []
    for i, t in enumerate(h["time"]):
        if t.startswith(today_str):
            hour   = int(t[11:13])
            rad_wm = h["shortwave_radiation"][i]
            sun_s  = h["sunshine_duration"][i]
            hourly.append({
                "hour":    hour,
                "rad_wm2": rad_wm,
                "sun_pct": int((sun_s / 3600) * 100),
                "cloud":   h["cloudcover"][i],
                "prod":    estimate_hour(rad_wm, today_dt),
            })

    return daily, hourly

# ── Widoki ────────────────────────────────────────────────────────────────────

def draw_today_big(draw, daily, hourly):
    if not daily:
        return
    today   = daily[0]
    now_h   = datetime.now().hour
    current = next((h for h in hourly if h["hour"] == now_h), None)

    draw.text((0, 0),  "DZIS",                   font=FONT_TINY,  fill="white")
    draw.text((0, 9),  f"{today['prod']:.1f}",   font=FONT_LARGE, fill="white")
    draw.text((0, 30), "kWh est.",                font=FONT_SMALL, fill="white")
    draw.text((0, 41), f"rad:{today['rad']:.1f}MJ", font=FONT_TINY, fill="white")
    draw.text((0, 51), f"{today['tmin']}/{today['tmax']}C", font=FONT_TINY, fill="white")

    draw.line([(74, 0), (74, 63)], fill="white")

    draw.text((77, 0), "TERAZ", font=FONT_TINY, fill="white")
    if current:
        draw.text((77, 9),  f"{current['prod']:.2f}", font=FONT_MED,  fill="white")
        draw.text((77, 22), "kWh/h",                  font=FONT_TINY, fill="white")
        draw.text((77, 32), f"{current['rad_wm2']:.0f}W/m2", font=FONT_TINY, fill="white")
    else:
        draw.text((77, 9), "--", font=FONT_MED, fill="white")

    if len(daily) > 1:
        jutro = daily[1]
        draw.line([(77, 43), (127, 43)], fill="white")
        draw.text((77, 45), "JUTRO",                    font=FONT_TINY, fill="white")
        draw.text((77, 54), f"{jutro['prod']:.1f}kWh",  font=FONT_TINY, fill="white")

def draw_daily_pv(draw, daily):
    if not daily:
        return
    draw.text((0, 0), "PRODUKCJA 5 DNI [kWh]", font=FONT_TINY, fill="white")
    draw.line([(0, 10), (127, 10)], fill="white")

    max_p = max(d["prod"] for d in daily) or 1
    bar_x, bar_w, val_x = 22, 68, 93

    for i, day in enumerate(daily[:5]):
        y      = 12 + i * 10
        lbl    = "Dzis" if i == 0 else day["dow"]
        filled = int((day["prod"] / max_p) * bar_w)
        draw.text((0, y), lbl, font=FONT_TINY, fill="white")
        draw.rectangle([bar_x, y+1, bar_x+bar_w, y+8], outline="white")
        if filled > 0:
            draw.rectangle([bar_x+1, y+2, bar_x+filled, y+7], fill="white")
        draw.text((val_x, y), f"{day['prod']:.1f}", font=FONT_TINY, fill="white")

def draw_hourly_pv(draw, hourly, daily):
    if not hourly:
        return
    today_prod = daily[0]["prod"] if daily else 0
    draw.text((0, 0), f"GODZ | dz:{today_prod:.1f}kWh", font=FONT_TINY, fill="white")
    draw.line([(0, 10), (127, 10)], fill="white")

    chart_top, chart_bottom = 12, 54
    chart_h = chart_bottom - chart_top
    col_w   = 128 / 24
    max_p   = max((h["prod"] for h in hourly), default=0.1) or 0.1

    for h in hourly:
        x = int(h["hour"] * col_w)
        w = max(int(col_w) - 1, 1)
        if h["prod"] > 0:
            bar_h = max(int((h["prod"] / max_p) * chart_h), 2)
            draw.rectangle([x, chart_bottom - bar_h, x+w, chart_bottom], fill="white")

    draw.line([(0, chart_bottom+1), (127, chart_bottom+1)], fill="white")
    for tick in [0, 6, 12, 18, 23]:
        draw.text((int(tick * col_w), chart_bottom+2), str(tick), font=FONT_TINY, fill="white")
    # Linia teraz
    now_x = int(datetime.now().hour * col_w) + int(col_w / 2)
    draw.line([(now_x, chart_top), (now_x, chart_bottom)], fill="white")

def draw_sun_bars(draw, daily):
    if not daily:
        return
    draw.text((0, 0), "RADIACJA 5 DNI [MJ/m2]", font=FONT_TINY, fill="white")
    draw.line([(0, 10), (127, 10)], fill="white")

    max_r = max(d["rad"] for d in daily) or 1
    bar_x, bar_w, val_x = 22, 72, 97

    for i, day in enumerate(daily[:5]):
        y      = 12 + i * 10
        lbl    = "Dzis" if i == 0 else day["dow"]
        filled = int((day["rad"] / max_r) * bar_w)
        draw.text((0, y), lbl, font=FONT_TINY, fill="white")
        draw.rectangle([bar_x, y+1, bar_x+bar_w, y+8], outline="white")
        if filled > 0:
            draw.rectangle([bar_x+1, y+2, bar_x+filled, y+7], fill="white")
        draw.text((val_x, y), f"{day['rad']:.1f}", font=FONT_TINY, fill="white")

def draw_loading(draw):
    doy = datetime.now().timetuple().tm_yday
    draw.text((4, 4),  "Pobieranie...",          font=FONT_TINY,  fill="white")
    draw.text((4, 18), "Open-Meteo API",          font=FONT_SMALL, fill="white")
    draw.text((4, 32), "Ruszcza/Polaniec",        font=FONT_TINY,  fill="white")
    draw.text((4, 46), f"PV {KWP}kWp  doy={doy}", font=FONT_TINY, fill="white")

def draw_error(draw, msg):
    draw.text((0, 0), "BLAD API", font=FONT_SMALL, fill="white")
    draw.line([(0, 12), (127, 12)], fill="white")
    words, line, y = msg.split(), "", 16
    for w in words:
        test = (line + " " + w).strip()
        if len(test) > 22:
            draw.text((0, y), line, font=FONT_TINY, fill="white")
            y += 10; line = w
        else:
            line = test
    if line:
        draw.text((0, y), line, font=FONT_TINY, fill="white")

# ── Główna pętla ──────────────────────────────────────────────────────────────
def main():
    # Sprawdź strefę czasową
    import subprocess
    tz = subprocess.run(["timedatectl", "show", "--property=Timezone", "--value"],
                        capture_output=True, text=True).stdout.strip()
    if tz != "Europe/Warsaw":
        print(f"[UWAGA] Strefa czasowa to '{tz}' zamiast 'Europe/Warsaw'!")
        print(f"        Uruchom: sudo timedatectl set-timezone Europe/Warsaw")

    serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
    device = ssd1306(serial, width=128, height=64)
    print(f"[OK] SSD1306 128x64 @ I2C {hex(I2C_ADDRESS)}")
    print(f"[OK] Model: a={PV_A} b={PV_B} c={PV_C} max={PV_MAX}kWh")
    print(f"[OK] Strefa: {tz} | doy={doy_today()}")

    with canvas(device) as draw:
        draw_loading(draw)

    daily, hourly = None, None
    last_fetch    = 0
    view_index    = 0
    views         = ["today_big", "hourly_pv", "daily_pv"]
    last_switch   = time.time()

    while True:
        now = time.time()

        if now - last_fetch > INTERVAL_SEC or daily is None:
            print(f"[API] {datetime.now().strftime('%H:%M:%S')} doy={doy_today()} — pobieram...")
            raw = fetch_data()
            if raw:
                daily, hourly = parse_data(raw)
                last_fetch = now
                if daily:
                    d0 = daily[0]
                    print(f"[OK]  Dziś: {d0['prod']:.1f}kWh | rad={d0['rad']:.1f}MJ/m² | doy={doy_for(d0['date'])}")
            else:
                with canvas(device) as draw:
                    draw_error(draw, "Brak sieci Open-Meteo")
                time.sleep(30)
                continue

        if now - last_switch >= VIEW_SWITCH:
            view_index  = (view_index + 1) % len(views)
            last_switch = now

        with canvas(device) as draw:
            v = views[view_index]
            if   v == "today_big":  draw_today_big(draw, daily, hourly)
            elif v == "hourly_pv":  draw_hourly_pv(draw, hourly, daily)
            elif v == "daily_pv":   draw_daily_pv(draw, daily)
            elif v == "sun_bars":   draw_sun_bars(draw, daily)

        time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Zatrzymano.")
