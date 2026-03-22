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
IKONA_SIEC = (
    0b00100,
    0b01110,
    0b11111,
    0b00100,
    0b00100,
    0b00000,
    0b11111,
    0b00000,
)
IKONA_BAT_100 = (
    0b01110,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
)
IKONA_BAT_75 = (
    0b01110,
    0b11111,
    0b10001,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
    0b11111,
)
IKONA_BAT_50 = (
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
    0b11111,
    0b11111,
)
IKONA_BAT_25 = (
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
    0b11111,
)
IKONA_BAT_0 = (
    0b01110,
    0b11111,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b10001,
    0b11111,
)
IKONA_DOMEK = (
    0b00100,
    0b01110,
    0b11111,
    0b11011,
    0b11011,
    0b11011,
    0b11111,
    0b00000,
)

lcd.create_char(0, IKONA_SLONCE)
lcd.create_char(1, IKONA_SIEC)
lcd.create_char(2, IKONA_BAT_100)
lcd.create_char(3, IKONA_BAT_75)
lcd.create_char(4, IKONA_BAT_50)
lcd.create_char(5, IKONA_BAT_25)
lcd.create_char(6, IKONA_BAT_0)
lcd.create_char(7, IKONA_DOMEK)

MAX_LOAD = 14000

def ikona_baterii(soc):
    if soc >= 80:   return u'\x02'
    elif soc >= 60: return u'\x03'
    elif soc >= 40: return u'\x04'
    elif soc >= 20: return u'\x05'
    else:           return u'\x06'

def pasek(wartosc, max_wartosc, dlugosc=5):
    wypelnione = int(round(float(min(wartosc, max_wartosc)) / max_wartosc * dlugosc))
    return u'\xff' * wypelnione + u'-' * (dlugosc - wypelnione)

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
        linia(u'\x00', u"PV:{}".format(pasek(pv, 3600)),          u"{}W".format(pv)),
        linia(u'\x01', u"Siec:",                                   u"{}W".format(grid)),
        linia(u'\x07', u"Load:{}".format(pasek(load, MAX_LOAD)),   u"{}W".format(abs(load))),
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
