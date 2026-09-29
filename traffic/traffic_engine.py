#!/usr/bin/env python3
"""
Traffic-Engine für Testseiten (GTM-/GA4-Übungsseiten).

Liest eine Einstellungsdatei (konfiguration.json) und besucht die Seite mit einem
echten Chromium-Browser (Playwright). GTM, GA4 und alle Trigger lösen deshalb aus
wie bei einem Menschen. Formulare werden standardmäßig im Browser abgefangen:
GTM misst die Einsendung, beim Formularempfänger kommt nichts an.

NUR FÜR EIGENE TESTSEITEN MIT EIGENER GA4-TEST-PROPERTY.

Aufrufe (im Ordner mit konfiguration.json):
  python traffic_engine.py --probe                     1 Besuch, Browser sichtbar
  python traffic_engine.py --probe --aktion NAME       1 Besuch mit bestimmter Aktion
  python traffic_engine.py --besuche 10                10 Besuche am Stück
  python traffic_engine.py --fenster vormittag         ein Zeitfenster aus der Konfiguration, heute
  python traffic_engine.py --alle-fenster              alle Zeitfenster von heute nacheinander
  python traffic_engine.py --alle-fenster --taeglich   jeden Tag wiederholen (an den eingestellten Wochentagen)
  python traffic_engine.py --takt 15                   für Zeitplaner (GitHub Actions): wird alle 15 Min.
                                                       aufgerufen und macht anteilig Besuche, wenn gerade
                                                       ein Zeitfenster läuft (Zeit: Europe/Berlin)
  python traffic_engine.py --takt 15 --nur-pruefen     gibt nur die Anzahl aus, ohne Browser
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

ORDNER = Path.cwd()
MAX_PRO_TAG = 300  # Schutzgrenze, damit kein Hosting-Kontingent aufgefressen wird
WOCHENTAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"]
TRACKING_HOSTS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "google.com",
                  "googleadservices.com", "facebook.com", "facebook.net", "clarity.ms", "bing.com",
                  "linkedin.com", "licdn.com", "hotjar.com", "tiktok.com")

DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
MOBIL_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
VORNAMEN = ["Anna", "Jonas", "Sabine", "Mehmet", "Laura", "Peter", "Julia", "Tobias"]


# ---------------------------------------------------------------------------
def lade_konfiguration(pfad):
    with open(pfad, encoding="utf-8") as f:
        k = json.load(f)
    k.setdefault("quellen", [{"gewicht": 100, "utm_source": None}])
    k.setdefault("mobil_anteil", 0.5)
    k.setdefault("wiederkehr_anteil", 0.25)
    k.setdefault("formulare_abfangen", True)
    k.setdefault("einwilligung_selektor", None)
    k.setdefault("zeitfenster", [])
    k["url"] = k["url"] if k["url"].endswith("/") else k["url"] + "/"
    if not k.get("aktionen"):
        sys.exit("Keine Aktionen in der Konfiguration.")
    return k


def log(text):
    print(f"[{datetime.now(BERLIN):%H:%M:%S}] {text}", file=sys.stderr, flush=True)


def warte(von, bis):
    time.sleep(random.uniform(von, bis))


def gewichtet(liste):
    return random.choices(liste, weights=[e.get("gewicht", 1) for e in liste], k=1)[0]


def protokolliere(zeile):
    datei = ORDNER / "protokoll.csv"
    neu = not datei.exists()
    with datei.open("a", encoding="utf-8") as f:
        if neu:
            f.write("zeit;quelle;geraet;besucher;aktion;ga4_treffer;status\n")
        f.write(";".join(str(x) for x in zeile) + "\n")


def zielurl(k):
    q = gewichtet(k["quellen"])
    if not q.get("utm_source"):
        return k["url"], "direkt"
    teile = [f"utm_source={q['utm_source']}", f"utm_medium={q.get('utm_medium', 'referral')}"]
    if q.get("utm_campaign"):
        teile.append(f"utm_campaign={q['utm_campaign']}")
    return k["url"] + "?" + "&".join(teile), f"{q['utm_source']}/{q.get('utm_medium', '')}"


# ---------------------------------------------------------------------------
# Formulare abfangen: jeder POST außer an Tracking-Dienste wird im Browser beantwortet
# ---------------------------------------------------------------------------
def abfangen(context):
    def behandle(route, request):
        host = urlparse(request.url).netloc
        if request.method != "POST" or any(host.endswith(t) for t in TRACKING_HOSTS):
            return route.continue_()
        if request.is_navigation_request():
            try:
                antwort = context.request.get(request.url)
                return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=antwort.text())
            except Exception:
                return route.fulfill(status=200, content_type="text/html", body="<html><body>OK</body></html>")
        return route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    context.route("**/*", behandle)


# ---------------------------------------------------------------------------
# Felder automatisch mit Testdaten füllen
# ---------------------------------------------------------------------------
def fuelle_felder(page, container):
    felder = page.locator(f"{container} input, {container} textarea, {container} select")
    radios_erledigt = set()
    for i in range(felder.count()):
        f = felder.nth(i)
        try:
            if not f.is_visible():
                continue
            tag = f.evaluate("e => e.tagName.toLowerCase()")
            typ = (f.get_attribute("type") or "text").lower()
            name = (f.get_attribute("name") or f.get_attribute("id") or "").lower()
            if typ in ("hidden", "submit", "button", "reset", "file", "image"):
                continue
            if tag == "select":
                werte = f.evaluate("e => [...e.options].filter(o => o.value && !o.disabled).map(o => o.value)")
                if werte:
                    f.select_option(random.choice(werte))
            elif typ == "checkbox":
                f.check()
            elif typ == "radio":
                if name not in radios_erledigt:
                    f.check()
                    radios_erledigt.add(name)
            elif tag == "textarea":
                f.fill("Automatischer Testbesuch – bitte ignorieren.")
            elif typ == "email" or "mail" in name:
                f.fill("test@example.com")
            elif typ == "tel" or "telefon" in name or "phone" in name:
                f.fill("030 000000")
            elif typ == "number":
                f.fill("1")
            elif typ == "date":
                f.fill((datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"))
            elif "plz" in name or "zip" in name:
                f.fill("10115")
            elif "ort" in name or "stadt" in name or "city" in name:
                f.fill("Teststadt")
            elif "firma" in name or "company" in name:
                f.fill("Testfirma")
            else:
                f.fill(f"{random.choice(VORNAMEN)} Test")
            warte(0.3, 1.0)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Aktionen
# ---------------------------------------------------------------------------
def scrolle(page, n=None):
    for _ in range(n or random.randint(2, 5)):
        page.mouse.wheel(0, random.randint(250, 700))
        warte(0.8, 2.2)


def fuehre_aus(page, aktion):
    art = aktion["art"]
    sel = aktion.get("selektor")
    if art == "umsehen":
        warte(6, 15)
        scrolle(page, 2)
        return
    ziel = page.locator(sel).first
    ziel.scroll_into_view_if_needed(timeout=10000)
    warte(0.8, 2.0)

    if art == "klick":
        ziel.click(no_wait_after=True)
        warte(2, 4)
    elif art == "seite":
        with page.expect_navigation(timeout=15000):
            ziel.click()
        warte(4, 10)
        scrolle(page)
    elif art == "formular_standard":
        fuelle_felder(page, sel)
        knopf = aktion.get("absenden") or (
            f"{sel} button[type=submit], {sel} input[type=submit], {sel} button:not([type])")
        warte(1, 2.5)
        try:
            with page.expect_navigation(timeout=15000):
                page.locator(knopf).first.click()
        except Exception:
            pass  # manche Formulare bleiben auf der Seite
        warte(4, 8)
    elif art == "formular_ajax":
        fuelle_felder(page, sel)
        warte(1, 2.5)
        page.locator(aktion["absenden"]).first.click()
        if aktion.get("erfolg_selektor"):
            page.wait_for_selector(aktion["erfolg_selektor"], state="visible", timeout=15000)
        warte(3, 6)
    else:
        raise ValueError(f"Unbekannte Aktionsart: {art}")


# ---------------------------------------------------------------------------
# Ein Besuch
# ---------------------------------------------------------------------------
def ein_besuch(browser, k, aktion_name=None):
    stamm = ORDNER / "stammbesucher"
    stamm.mkdir(exist_ok=True)
    mobil = random.random() < k["mobil_anteil"]

    vorhandene = sorted(stamm.glob("*.json"))
    stamm_datei, besucher = None, "neu"
    if vorhandene and random.random() < k["wiederkehr_anteil"]:
        stamm_datei, besucher = random.choice(vorhandene), "wiederkehrend"
    elif len(vorhandene) < 8:
        stamm_datei = stamm / f"besucher_{len(vorhandene) + 1}.json"

    opt = dict(locale="de-DE", timezone_id="Europe/Berlin",
               user_agent=MOBIL_UA if mobil else DESKTOP_UA,
               viewport={"width": 390, "height": 844} if mobil else random.choice(
                   [{"width": 1440, "height": 900}, {"width": 1920, "height": 1080}, {"width": 1366, "height": 768}]),
               is_mobile=mobil, has_touch=mobil, device_scale_factor=3 if mobil else 2)
    if besucher == "wiederkehrend":
        opt["storage_state"] = str(stamm_datei)

    context = browser.new_context(**opt)
    if k["formulare_abfangen"]:
        abfangen(context)
    ga4 = []
    context.on("request", lambda r: ga4.append(1) if "/g/collect" in r.url else None)

    if aktion_name:
        aktion = next((a for a in k["aktionen"] if a["name"] == aktion_name), None)
        if not aktion:
            sys.exit(f"Aktion '{aktion_name}' gibt es nicht. Vorhanden: {', '.join(a['name'] for a in k['aktionen'])}")
    else:
        aktion = gewichtet(k["aktionen"])

    url, quelle = zielurl(k)
    status = "ok"
    page = context.new_page()
    page.on("popup", lambda neu: neu.close())
    try:
        page.goto(url, wait_until="load", timeout=30000)
        if k["einwilligung_selektor"] and besucher == "neu":
            try:
                page.locator(k["einwilligung_selektor"]).first.click(timeout=5000)
                warte(1, 2)
            except Exception:
                pass
        warte(2, 5)
        scrolle(page)
        fuehre_aus(page, aktion)
        warte(4, 7)  # GA4 Zeit zum Senden geben
    except Exception as e:  # ein Fehler darf den Lauf nicht stoppen
        status = f"fehler: {type(e).__name__}"
    finally:
        if stamm_datei is not None:
            try:
                context.storage_state(path=str(stamm_datei))
            except Exception:
                pass
        context.close()

    geraet = "Handy" if mobil else "Desktop"
    log(f"{geraet:7} | {quelle:24} | {besucher:13} | {aktion['name']:22} | GA4-Treffer: {len(ga4):2} | {status}")
    protokolliere([datetime.now().isoformat(timespec="seconds"), quelle, geraet, besucher, aktion["name"], len(ga4), status])
    return len(ga4)


# ---------------------------------------------------------------------------
# Zeitfenster
# ---------------------------------------------------------------------------
def fenster_laeuft_heute(f):
    tage = [t.lower()[:2] for t in f.get("wochentage", WOCHENTAGE)]
    return WOCHENTAGE[datetime.now().weekday()] in tage


def ein_fenster(browser, k, f):
    von, bis = f["von"], f["bis"]
    lo, hi = f.get("anzahl", [10, 15]) if isinstance(f.get("anzahl"), list) else (f.get("anzahl", 10),) * 2
    n = min(random.randint(lo, hi), MAX_PRO_TAG)
    jetzt = datetime.now()
    start = jetzt.replace(hour=von, minute=0, second=0, microsecond=0)
    ende = jetzt.replace(hour=bis, minute=0, second=0, microsecond=0)
    if not fenster_laeuft_heute(f):
        log(f"Fenster '{f['name']}' ist heute nicht vorgesehen.")
        return
    if jetzt >= ende - timedelta(minutes=10):
        log(f"Fenster '{f['name']}' ({von}-{bis} Uhr) ist heute schon vorbei.")
        return
    if jetzt > start:
        log(f"Start innerhalb von '{f['name']}': Besuche verteilen sich auf die Restzeit bis {bis} Uhr.")
        start = jetzt
    dauer = (ende - start).total_seconds() - 300
    plan = sorted(start + timedelta(seconds=random.uniform(0, dauer)) for _ in range(n))
    log(f"Fenster '{f['name']}' {von}-{bis} Uhr: {n} Besuche, erster {plan[0]:%H:%M}, letzter {plan[-1]:%H:%M}.")
    summe = 0
    for nr, t in enumerate(plan, 1):
        pause = (t - datetime.now()).total_seconds()
        if pause > 0:
            log(f"Besuch {nr}/{n} um {t:%H:%M}.")
            time.sleep(pause)
        summe += ein_besuch(browser, k)
    log(f"Fenster '{f['name']}' erledigt: {n} Besuche, {summe} GA4-Treffer.")


def besuche_im_takt(k, takt_minuten):
    """Anzahl Besuche für einen Aufruf, der alle takt_minuten erfolgt (z. B. per GitHub Actions)."""
    jetzt = datetime.now(BERLIN)
    for f in k["zeitfenster"]:
        if not (f["von"] <= jetzt.hour < f["bis"]):
            continue
        tage = [t.lower()[:2] for t in f.get("wochentage", WOCHENTAGE)]
        if WOCHENTAGE[jetzt.weekday()] not in tage:
            continue
        anz = f.get("anzahl", 10)
        mittel = sum(anz) / 2 if isinstance(anz, list) else anz
        erwartet = mittel * takt_minuten / ((f["bis"] - f["von"]) * 60)
        n = int(erwartet) + (1 if random.random() < erwartet - int(erwartet) else 0)
        log(f"Takt {takt_minuten} Min., Fenster '{f['name']}' läuft: {n} Besuch(e) (Erwartung {erwartet:.2f}).")
        return min(n, 10)
    log(f"Takt: {jetzt:%H:%M} Uhr Berliner Zeit liegt in keinem Zeitfenster.")
    return 0


def alle_fenster(browser, k, taeglich):
    fenster = sorted(k["zeitfenster"], key=lambda f: f["von"])
    if not fenster:
        sys.exit("Keine Zeitfenster in der Konfiguration.")
    while True:
        for f in fenster:
            ein_fenster(browser, k, f)
        if not taeglich:
            return
        morgen = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        log(f"Tagesplan erledigt. Weiter am {morgen:%d.%m.}.")
        time.sleep(max(0, (morgen - datetime.now()).total_seconds()))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Traffic-Engine für Testseiten")
    ap.add_argument("--konfiguration", default="konfiguration.json")
    ap.add_argument("--probe", action="store_true", help="1 Besuch mit sichtbarem Browser")
    ap.add_argument("--aktion", help="bestimmte Aktion erzwingen (Name aus der Konfiguration)")
    ap.add_argument("--besuche", type=int, default=0)
    ap.add_argument("--fenster", help="Name eines Zeitfensters aus der Konfiguration")
    ap.add_argument("--alle-fenster", action="store_true")
    ap.add_argument("--taeglich", action="store_true", help="mit --alle-fenster: jeden Tag wiederholen")
    ap.add_argument("--sichtbar", action="store_true")
    ap.add_argument("--takt", type=int, help="Aufrufabstand in Minuten für Zeitplaner wie GitHub Actions")
    ap.add_argument("--nur-pruefen", action="store_true", help="mit --takt: nur die Anzahl ausgeben")
    a = ap.parse_args()

    k = lade_konfiguration(ORDNER / a.konfiguration)
    if a.takt:
        n = besuche_im_takt(k, a.takt)
        if a.nur_pruefen:
            print(n)
            return
        if n == 0:
            return
        a.besuche = n
    if not (a.probe or a.besuche or a.fenster or a.alle_fenster):
        ap.print_help()
        return

    from playwright.sync_api import sync_playwright  # erst hier, damit --nur-pruefen ohne Playwright läuft
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not (a.probe or a.sichtbar))
        try:
            if a.probe:
                ein_besuch(browser, k, a.aktion)
            elif a.besuche:
                summe = 0
                for i in range(min(a.besuche, MAX_PRO_TAG)):
                    summe += ein_besuch(browser, k, a.aktion)
                    if i < a.besuche - 1:
                        warte(5, 20)
                log(f"Fertig: {a.besuche} Besuche, {summe} GA4-Treffer.")
            elif a.fenster:
                f = next((f for f in k["zeitfenster"] if f["name"] == a.fenster), None)
                if not f:
                    sys.exit(f"Zeitfenster '{a.fenster}' fehlt in der Konfiguration.")
                ein_fenster(browser, k, f)
            else:
                alle_fenster(browser, k, a.taeglich)
        except KeyboardInterrupt:
            log("Beendet.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
