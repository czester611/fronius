# -*- coding: utf-8 -*-
import json, urllib2, time
from RPLCD.i2c import CharLCD

lcd = CharLCD('PCF8574', 0x27, cols=20, rows=4)

IKONA_SLONCE = (
    0b00100,
    0b10101,
    0b01110,
    0b11111,
    0b11111,
    0b01110,
    0b10101,
    0b00100,
)
IKONA_SIEC_POBOR = (
    0b00100,
    0b01110,
    0b11111,
    0b00100,
    0b00100,
    0b00000,
    0b11111,
    0b00000,
)
IKONA_BAT_PELNA = (
    0b01110,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
)
IKONA_BAT_SREDNIA = (
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
    0b11111,
    0b11111,
)
IKONA_BAT_PUSTA = (
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
)
# Domek wypelniony - duze obciazenie
IKONA_DOM_PELNY = (
    0b00100,
    0b01110,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
)
# Domek w polowie
IKONA_DOM_SREDNI = (
    0b00100,
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b11111,
    0b11111,
    0b11111,
)
# Domek pusty - male obciazenie
IKONA_DOM_PUSTY = (
    0b00100,
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
)

lcd.create_char(0, IKONA_SLONCE)       # \x00 slonce
lcd.create_char(1, IKONA_SIEC_POBOR)  # \x01 siec
lcd.create_char(2, IKONA_BAT_PELNA)   # \x02 bat pelna
lcd.create_char(3, IKONA_BAT_SREDNIA) # \x03 bat srednia
lcd.create_char(4, IKONA_BAT_PUSTA)   # \x04 bat pusta
lcd.create_char(5, IKONA_DOM_PELNY)   # \x05 dom pelny
lcd.create_char(6, IKONA_DOM_SREDNI)  # \x06 dom sredni
lcd.create_char(7, IKONA_DOM_PUSTY)   # \x07 dom pusty

MAX_LOAD = 14000  # W

def ikona_baterii(soc):
    if soc >= 66: return u'\x02'
    elif soc >= 33: return u'\x03'
    else: return u'\x04'

def ikona_domu(load):
    procent = float(abs(load)) / MAX_LOAD
    if procent >= 0.5: return u'\x05'
    else:              return u'\x07'

def ikona_sieci(grid):
    return u'\x01'

def pobierz_dane():
    url = "http://192.168.5.244/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
    r = urllib2.urlopen(url, timeout=5)
    d = json.load(r)
    i = d["Body"]["Data"]["Inverters"]["1"]
    s = d["Body"]["Data"]["Site"]
    return {
        "soc":  i["SOC"],
        "pv":   s["P_PV"],
        "grid": s["P_Grid"],
        "load": s["P_Load"],
        "akku": s["P_Akku"],
    }

def pasek(wartosc, max_wartosc, dlugosc=5):
    wypelnione = int(round(float(min(wartosc, max_wartosc)) / max_wartosc * dlugosc))
    return u'\xff' * wypelnione + u'-' * (dlugosc - wypelnione)

def wyswietl(dane):
    soc  = dane['soc']
    pv   = int(dane['pv']   or 0)
    grid = int(dane['grid'] or 0)
    akku = int(dane['akku'] or 0)
    load = int(dane['load'] or 0)

    if akku > 0:
        znak_bat = u"-"
    elif akku < 0:
        znak_bat = u"+"
    else:
        znak_bat = u" "

    def linia(ikona, etykieta, wartosc):
        lewo = u"{} {}".format(ikona, etykieta)
        return u"{}{}".format(lewo, wartosc.rjust(20 - len(lewo)))

    lewo1 = u"{} SOC:{}%".format(ikona_baterii(soc), soc)
    prawo1 = u"{}{}W".format(znak_bat, abs(akku))
    linia1 = u"{}{}".format(lewo1, prawo1.rjust(20 - len(lewo1)))

    linie = [
        linia1,
        linia(u'\x00', u"PV:{}".format(pasek(pv, 3600)),     u"{}W".format(pv)),
        linia(u'\x01', u"Siec:",                              u"{}W".format(grid)),
        linia(ikona_domu(load), u"Load:{}".format(pasek(load, 14000)), u"{}W".format(abs(load))),
    ]
    for i, tekst in enumerate(linie):
        lcd.cursor_pos = (i, 0)
        lcd.write_string(tekst.ljust(20))

while True:
    try:
        dane = pobierz_dane()
        wyswietl(dane)
    except Exception as e:
        lcd.cursor_pos = (0, 0)
        lcd.write_string("Blad polaczenia".ljust(20))
        print("Blad: {}".format(e))
    time.sleep(1)
