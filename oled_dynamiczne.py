#!/usr/bin/env python3
"""
Prognoza słoneczna + produkcja PV + ceny energii — Ruszcza, gm. Połaniec
Wyświetlacz: SSD1306 128x64 OLED (I2C)
Falownik: Fronius Symo GEN24 10.0 Plus | 3.6 kWp | Południe 30-35°

Model PV (68 dni, 2026-02-01 do 2026-04-12):
  prod = clip(2.32864×rad - 0.010965×rad×doy - 1.70793,  0, 26.0)
  R²=0.79,  MAE=2.78 kWh

Ceny energii: PSE RCE API (https://api.raporty.pse.pl/api/rce-pln)
  Dane: zł/MWh → zł/kWh (Rynkowa Cena Energii, bez klucza, bez rejestracji)

Przed uruchomieniem:
  sudo timedatectl set-timezone Europe/Warsaw
  pip install luma.oled pillow requests

Podłączenie SSD1306:
  VCC → Pin 1 (3.3V) | GND → Pin 6 | SDA → Pin 3 | SCL → Pin 5
"""

import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont

# ── Konfiguracja ─────────────────────────────────────────────────────────────
LAT           = 50.409
LON           = 21.231
KWP           = 3.6
I2C_PORT      = 1
I2C_ADDRESS   = 0x3C       # zmień na 0x3D jeśli i2cdetect pokazuje 3d
INTERVAL_PV   = 3600       # odświeżanie pogody co 1h
INTERVAL_PRICE= 3600       # odświeżanie cen co 1h (nowe ceny dnia jutrzejszego ~14:00)
VIEW_SWITCH   = 6          # zmiana widoku co N sekund
DRAW_INTERVAL = 1.0        # odswiezanie wyswietlacza co N sekund (nie szybciej — I2C)
FORECAST_DAYS = 5

# ── PSE RCE API — bez klucza, bez rejestracji ────────────────────────────────
PSE_API_URL   = "https://api.raporty.pse.pl/api/rce-pln"  # zł/MWh, czas lokalny PL

# ── Model PV ─────────────────────────────────────────────────────────────────
PV_A   =  2.32864
PV_B   = -0.010965
PV_C   = -1.70793
PV_MAX = 26.0

def doy_for(date: datetime) -> int:
    return date.timetuple().tm_yday

def estimate_day(shortwave_mj: float, date: datetime) -> float:
    doy = doy_for(date)
    raw = PV_A * shortwave_mj + PV_B * shortwave_mj * doy + PV_C
    return round(max(0.0, min(raw, PV_MAX)), 2)

def estimate_hour(shortwave_wm2: float, date: datetime) -> float:
    rad_mj = shortwave_wm2 * 3600 / 1_000_000
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

# ── Pobieranie danych pogodowych ──────────────────────────────────────────────
def fetch_weather():
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
        print(f"[Pogoda błąd] {e}")
        return None

def parse_weather(raw):
    if not raw:
        return None, None
    d = raw["daily"]
    daily = []
    for i, date_str in enumerate(d["time"]):
        date    = datetime.strptime(date_str, "%Y-%m-%d")
        rad     = d["shortwave_radiation_sum"][i]
        sun_h   = d["sunshine_duration"][i] / 3600
        dl_h    = d["daylight_duration"][i] / 3600
        sun_pct = int((sun_h / dl_h * 100)) if dl_h > 0 else 0
        daily.append({
            "date":    date,
            "label":   date.strftime("%d.%m"),
            "dow":     ["Pn","Wt","Sr","Cz","Pt","Sb","Nd"][date.weekday()],
            "rad":     rad,
            "sun_h":   round(sun_h, 1),
            "sun_pct": sun_pct,
            "tmax":    round(d["temperature_2m_max"][i]),
            "tmin":    round(d["temperature_2m_min"][i]),
            "prod":    estimate_day(rad, date),
        })
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

# ── Pobieranie cen energii PSE RCE ───────────────────────────────────────────
def fetch_prices(day: datetime) -> dict:
    """
    Pobiera ceny RCE z API PSE dla podanego dnia.
    Zwraca {hour: cena_zł_kWh} — cena hurtowa zł/kWh.
    API publiczne PSE: https://api.raporty.pse.pl/api/rce-pln
    Ceny jutrzejsze publikowane ok. 14:00.
    """
    date_str = day.strftime("%Y-%m-%d")
    # URL jako surowy string — tak działają wszystkie znane implementacje PSE API.
    # requests.get() z surowym URL nie enkoduje $filter ani apostrofów.
    url = f"{PSE_API_URL}?$filter=business_date eq '{date_str}'"
    try:
        r = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        items = data.get("value", [])
        print(f"[PSE] {date_str}: {len(items)} rekordów")
        return _parse_pse(items)
    except Exception as e:
        print(f"[PSE błąd] {date_str}: {e}")
        return {}

def _parse_pse(items: list) -> dict:
    """
    Parsuje listę rekordów PSE RCE i zwraca {hour: cena_zł_kWh}.
    Pole udtczas_oreb: "00:00 - 00:15" — czas początku okresu, czas lokalny PL.
    Pole rce_pln: cena w zł/MWh → dzielimy przez 1000 → zł/kWh.
    Punkty 15-minutowe są uśredniane do godzin.
    """
    hour_buckets: dict = {}
    for item in items:
        try:
            price = float(item["rce_pln"]) / 1000  # zł/MWh → zł/kWh
            # udtczas_oreb = "HH:MM - HH:MM" — bierzemy godzinę startu
            time_str = item.get("udtczas_oreb", "")
            if time_str:
                start_str = time_str.split(" - ")[0].strip()  # "00:00"
                hour = int(start_str.split(":")[0])
            else:
                # fallback na dtime
                dt   = datetime.strptime(item["dtime"], "%Y-%m-%d %H:%M:%S")
                hour = dt.hour if dt.minute > 0 else (dt.hour - 1) % 24
            hour_buckets.setdefault(hour, []).append(price)
        except Exception:
            continue
    return {h: round(sum(v) / len(v), 4) for h, v in sorted(hour_buckets.items())}

# ── Widok cen energii ─────────────────────────────────────────────────────────
def draw_prices(draw, prices_today: dict, prices_tomorrow: dict):
    """
    Widok 5: ceny energii dziś i jutro.
    Lewa strona: dziś — aktualna godzina + min/max
    Prawa: jutro — min/max + godzina najtańszej
    Dół: wykres słupkowy 24h dla dzisiaj
    """
    now_h = datetime.now().hour

    # Nagłówek
    draw.text((0, 0), "CENA ENERGII [zl/kWh]", font=FONT_TINY, fill="white")
    draw.line([(0, 10), (127, 10)], fill="white")

    if not prices_today and not prices_tomorrow:
        # Brak klucza lub danych
        draw.text((0, 14), "Brak danych.", font=FONT_SMALL, fill="white")
        draw.text((0, 26), "Brak danych z PSE.", font=FONT_TINY, fill="white")
        draw.text((0, 36), "Sprawdz polaczenie", font=FONT_TINY, fill="white")
        draw.text((0, 50), "api.raporty.pse.pl", font=FONT_TINY, fill="white")
        return

    def fmt(p): return f"{p:.3f}" if p else "---"

    # Lewa — dziś
    draw.text((0, 12), "DZIS", font=FONT_TINY, fill="white")
    if prices_today:
        current = prices_today.get(now_h)
        min_p   = min(prices_today.values())
        max_p   = max(prices_today.values())
        min_h   = min(prices_today, key=prices_today.get)
        max_h   = max(prices_today, key=prices_today.get)

        if current:
            draw.text((0, 21), fmt(current), font=FONT_MED, fill="white")
            draw.text((0, 33), f"{now_h}:00 teraz", font=FONT_TINY, fill="white")
        draw.text((0, 43), f"min:{fmt(min_p)}", font=FONT_TINY, fill="white")
        draw.text((0, 52), f"max:{fmt(max_p)}", font=FONT_TINY, fill="white")
    else:
        draw.text((0, 21), "brak", font=FONT_SMALL, fill="white")

    # Separator
    draw.line([(64, 12), (64, 63)], fill="white")

    # Prawa — jutro
    draw.text((67, 12), "JUTRO", font=FONT_TINY, fill="white")
    if prices_tomorrow:
        min_p2 = min(prices_tomorrow.values())
        max_p2 = max(prices_tomorrow.values())
        min_h2 = min(prices_tomorrow, key=prices_tomorrow.get)
        max_h2 = max(prices_tomorrow, key=prices_tomorrow.get)

        draw.text((67, 21), fmt(min_p2), font=FONT_MED, fill="white")
        draw.text((67, 33), f"min@{min_h2}:00", font=FONT_TINY, fill="white")
        draw.text((67, 43), f"max:{fmt(max_p2)}", font=FONT_TINY, fill="white")
        draw.text((67, 52), f"@{max_h2}:00", font=FONT_TINY, fill="white")
    else:
        draw.text((67, 21), "brak", font=FONT_SMALL, fill="white")
        draw.text((67, 33), "(po 14:00)", font=FONT_TINY, fill="white")

def draw_prices_chart(draw, prices_today: dict, prices_tomorrow: dict):
    """
    Widok 6: wykres słupkowy cen godzinowych dziś i jutro (jeśli dostępne).
    """
    draw.text((0, 0), "WYKRES CEN 24H", font=FONT_TINY, fill="white")
    draw.line([(0, 10), (127, 10)], fill="white")

    if not prices_today:
        draw.text((4, 24), "Brak danych PSE RCE", font=FONT_SMALL, fill="white")
        draw.text((4, 36), "Sprawdz polaczenie", font=FONT_TINY, fill="white")
        return

    # Wybierz dane do wykresu: dziś (rano bierzemy jutro jeśli dostępne)
    data = prices_today
    if prices_tomorrow and len(prices_tomorrow) >= 20:
        # po 14:00 mamy jutro — pokaż jutro
        now_h = datetime.now().hour
        if now_h >= 14:
            data = prices_tomorrow

    if not data:
        return

    chart_top, chart_bottom = 12, 54
    chart_h = chart_bottom - chart_top
    col_w   = 128 / 24
    now_h   = datetime.now().hour

    all_prices = list(data.values())
    min_p = min(all_prices)
    max_p = max(all_prices)
    rng   = max_p - min_p if max_p != min_p else 1

    for hour in range(24):
        price = data.get(hour)
        x = int(hour * col_w)
        w = max(int(col_w) - 1, 1)
        if price is not None:
            bar_h = max(int(((price - min_p) / rng) * chart_h), 2)
            y_top = chart_bottom - bar_h
            # Ciemniejszy = taniej (odwrócona logika — niski słupek = niska cena)
            fill = "white"
            draw.rectangle([x, y_top, x+w, chart_bottom], fill=fill)

    # Pozioma linia referencyjna 0.60 zl/kWh
    REF_PRICE = 0.60
    if min_p <= REF_PRICE <= max_p:
        ref_y = chart_bottom - int(((REF_PRICE - min_p) / rng) * chart_h)
        for x in range(0, 128, 4):
            draw.point((x, ref_y), fill="white")
        draw.text((104, ref_y - 7), "0.60", font=FONT_TINY, fill="white")

    draw.line([(0, chart_bottom+1), (127, chart_bottom+1)], fill="white")
    for tick in [0, 6, 12, 18, 23]:
        draw.text((int(tick * col_w), chart_bottom+2), str(tick), font=FONT_TINY, fill="white")

    # Linia teraz
    if data == prices_today:
        now_x = int(now_h * col_w) + int(col_w / 2)
        draw.line([(now_x, chart_top), (now_x, chart_bottom)], fill="white")

    # Min/max etykiety
    min_h = min(data, key=data.get)
    max_h = max(data, key=data.get)
    draw.text((0, 0), f"min@{min_h}h={min_p:.3f}", font=FONT_TINY, fill="white")
    draw.text((70, 0), f"max={max_p:.3f}", font=FONT_TINY, fill="white")

# ── Pozostałe widoki PV (bez zmian) ──────────────────────────────────────────
def draw_today_big(draw, daily, hourly):
    if not daily: return
    today   = daily[0]
    now_h   = datetime.now().hour
    current = next((h for h in hourly if h["hour"] == now_h), None)
    draw.text((0, 0),  "DZIS",                      font=FONT_TINY,  fill="white")
    draw.text((0, 9),  f"{today['prod']:.1f}",       font=FONT_LARGE, fill="white")
    draw.text((0, 30), "kWh est.",                   font=FONT_SMALL, fill="white")
    draw.text((0, 41), f"rad:{today['rad']:.1f}MJ",  font=FONT_TINY,  fill="white")
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
        draw.text((77, 45), "JUTRO",                   font=FONT_TINY, fill="white")
        draw.text((77, 54), f"{jutro['prod']:.1f}kWh", font=FONT_TINY, fill="white")

def draw_daily_pv(draw, daily):
    if not daily: return
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
    if not hourly: return
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
    now_x = int(datetime.now().hour * col_w) + int(col_w / 2)
    draw.line([(now_x, chart_top), (now_x, chart_bottom)], fill="white")

def draw_sun_bars(draw, daily):
    if not daily: return
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
    draw.text((4, 4),  "Pobieranie...",           font=FONT_TINY,  fill="white")
    draw.text((4, 18), "Open-Meteo + PSE RCE",    font=FONT_SMALL, fill="white")
    draw.text((4, 32), "Ruszcza/Polaniec",         font=FONT_TINY,  fill="white")
    draw.text((4, 46), f"PV {KWP}kWp | max {PV_MAX}kWh", font=FONT_TINY, fill="white")

def draw_error(draw, msg):
    draw.text((0, 0), "BLAD", font=FONT_SMALL, fill="white")
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
    import subprocess
    tz = subprocess.run(["timedatectl","show","--property=Timezone","--value"],
                        capture_output=True, text=True).stdout.strip()
    if tz != "Europe/Warsaw":
        print(f"[UWAGA] Strefa: '{tz}' — ustaw: sudo timedatectl set-timezone Europe/Warsaw")

    print(f"[OK] PSE RCE API: {PSE_API_URL}")

    serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
    device = ssd1306(serial, width=128, height=64)

    with canvas(device) as draw:
        draw_loading(draw)

    daily, hourly      = None, None
    prices_today       = {}
    prices_tomorrow    = {}
    last_fetch_pv      = 0
    last_fetch_price   = 0
    view_index         = 0
    # Widoki: 4 PV + 2 ceny energii
    views = ["today_big", "hourly_pv", "daily_pv", "sun_bars", "prices", "prices_chart"]
    last_switch = time.time()

    while True:
        now     = time.time()
        today   = datetime.now()
        tomorrow= today + timedelta(days=1)

        # Odśwież pogodę
        if now - last_fetch_pv > INTERVAL_PV or daily is None:
            print(f"[PV]    {today.strftime('%H:%M:%S')} — pobieram pogodę...")
            raw = fetch_weather()
            if raw:
                daily, hourly = parse_weather(raw)
                last_fetch_pv = now
                if daily:
                    d0 = daily[0]
                    print(f"[PV OK] Dziś: {d0['prod']:.1f}kWh | rad={d0['rad']:.1f}MJ/m²")
            else:
                with canvas(device) as draw:
                    draw_error(draw, "Brak sieci Open-Meteo")
                time.sleep(30)
                continue

        # Odśwież ceny
        if now - last_fetch_price > INTERVAL_PRICE or not prices_today:
            print(f"[CENY]  {today.strftime('%H:%M:%S')} — pobieram PSE RCE...")
            prices_today    = fetch_prices(today)
            prices_tomorrow = fetch_prices(tomorrow)
            last_fetch_price = now
            print(f"[CENY OK] Dziś: {len(prices_today)}h | Jutro: {len(prices_tomorrow)}h")

        # Zmiana widoku
        if now - time.time() + last_switch >= VIEW_SWITCH:
            pass  # handled below
        if time.time() - last_switch >= VIEW_SWITCH:
            view_index  = (view_index + 1) % len(views)
            last_switch = time.time()

        # Rysowanie z obsługą błędów I2C
        for attempt in range(3):
            try:
                with canvas(device) as draw:
                    v = views[view_index]
                    if   v == "today_big":    draw_today_big(draw, daily, hourly)
                    elif v == "hourly_pv":    draw_hourly_pv(draw, hourly, daily)
                    elif v == "daily_pv":     draw_daily_pv(draw, daily)
                    elif v == "sun_bars":     draw_sun_bars(draw, daily)
                    elif v == "prices":       draw_prices(draw, prices_today, prices_tomorrow)
                    elif v == "prices_chart": draw_prices_chart(draw, prices_today, prices_tomorrow)
                break  # sukces — wyjdź z pętli prób

            except OSError as e:
                print(f"[I2C błąd] {e} (próba {attempt+1}/3)")
                time.sleep(1)
                if attempt < 2:
                    # Spróbuj zreinicjować wyświetlacz
                    try:
                        serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
                        device = ssd1306(serial, width=128, height=64)
                        print("[I2C] reinit OK")
                    except Exception as reinit_err:
                        print(f"[I2C] reinit błąd: {reinit_err}")
                else:
                    print("[I2C] 3 nieudane próby — czekam 10s")
                    time.sleep(10)

        time.sleep(DRAW_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Zatrzymano.")
