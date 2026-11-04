#!/usr/bin/env python3
"""Ranný e-mail pre správcu: dnešné zákazky, voľno tímu a čakajúce žiadosti o voľno."""
import json, os, smtplib, ssl, sys, urllib.request, urllib.parse
from datetime import datetime
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Bratislava")
PROJECT = os.environ["FB_PROJECT"]
API_KEY = os.environ["FB_API_KEY"]
PORTAL = os.environ["PORTAL_KEY"]
DRY = os.environ.get("DRY_RUN") == "1"
FORCE = os.environ.get("FORCE") == "1"


def val(v):
    """Firestore REST typed value -> python."""
    if "stringValue" in v: return v["stringValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return v["doubleValue"]
    if "booleanValue" in v: return v["booleanValue"]
    if "nullValue" in v: return None
    if "timestampValue" in v: return v["timestampValue"]
    if "arrayValue" in v: return [val(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: val(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def fetch(col):
    out, token = [], None
    while True:
        q = {"pageSize": "300", "key": API_KEY}
        if token: q["pageToken"] = token
        url = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/portals/{PORTAL}/{col}?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)
        for doc in d.get("documents", []):
            o = {k: val(x) for k, x in doc.get("fields", {}).items()}
            o["id"] = doc["name"].rsplit("/", 1)[-1]
            out.append(o)
        token = d.get("nextPageToken")
        if not token: return out


def fmt_d(ds):
    try:
        y, m, d = ds.split("-"); return f"{int(d)}.{int(m)}.{y}"
    except Exception:
        return ds or ""


def build(today, montaze, volno, clients):
    cl = {c["id"]: c for c in clients}
    jobs = [m for m in montaze if m.get("date") == today and m.get("stav") != "Hotová"]
    jobs.sort(key=lambda m: m.get("time") or "99")
    rows = []
    for m in jobs:
        c = cl.get(m.get("clientId") or "", {})
        icon = "🔧" if not m.get("kind") else {"Čistenie": "🧹", "Oprava": "🔨"}.get(m.get("servisType"), "🛠")
        urgent = "🚨 " if m.get("urgentna") or m.get("urgent") else ""
        addr = m.get("address") or c.get("adresa") or ""
        tel = m.get("telefon") or c.get("telefon") or ""
        who = ", ".join(m.get("memberNames") or []) or "nepriradené"
        name = m.get("clientName") or (f"{c.get('meno','')} {c.get('priezvisko','')}".strip())
        rows.append((m.get("time") or "bez času", f"{urgent}{icon} {m.get('title') or 'Zákazka'}",
                     name, addr, tel, who))

    away = [v for v in volno if v.get("stav") == "Schválené" and v.get("od", "") <= today <= (v.get("do") or v.get("od", ""))]
    soon = [v for v in volno if v.get("stav") == "Schválené" and v.get("od", "") > today]
    pend = [v for v in volno if v.get("stav") == "Žiadosť" and (v.get("do") or v.get("od", "")) >= today]

    def when(v):
        if v.get("casOd"): return f"{fmt_d(v['od'])}, {v['casOd']}–{v['casDo']}"
        return fmt_d(v["od"]) + (f" – {fmt_d(v['do'])}" if v.get("do") and v["do"] != v["od"] else "")

    h = [f"<h2>Dnes {fmt_d(today)}</h2>"]
    h.append(f"<h3>Zákazky ({len(rows)})</h3>")
    if rows:
        h.append("<table cellpadding='6' style='border-collapse:collapse'>")
        for t, title, name, addr, tel, who in rows:
            h.append("<tr style='border-top:1px solid #ddd'><td><b>" + escape(t) + "</b></td><td><b>" + escape(title) + "</b><br>"
                     + escape(name) + (f"<br>📍 {escape(addr)}" if addr else "") + (f"<br>📞 {escape(tel)}" if tel else "")
                     + f"<br>👷 {escape(who)}</td></tr>")
        h.append("</table>")
    else:
        h.append("<p>Dnes nie je naplánovaná žiadna zákazka.</p>")
    h.append(f"<h3>🏖 Voľno tímu dnes ({len(away)})</h3>")
    h.append("<ul>" + "".join(f"<li>{escape(v.get('memberName',''))} – {escape(when(v))}</li>" for v in away) + "</ul>" if away else "<p>Nikto nemá voľno.</p>")
    if soon:
        h.append("<h3>Plánované voľno</h3><ul>" + "".join(f"<li>{escape(v.get('memberName',''))} – {escape(when(v))}</li>" for v in sorted(soon, key=lambda v: v['od'])[:10]) + "</ul>")
    if pend:
        h.append(f"<h3>⏳ Žiadosti o voľno čakajú na schválenie ({len(pend)})</h3><ul>" + "".join(f"<li>{escape(v.get('memberName',''))} – {escape(when(v))}</li>" for v in pend) + "</ul>")
    subj = f"Dnes {fmt_d(today)}: {len(rows)} zákaziek" + (f", voľno: {len(away)}" if away else "") + (f", čaká {len(pend)} žiadostí" if pend else "")
    return subj, "".join(h)


def main():
    now = datetime.now(TZ)
    if not FORCE and now.hour != 6:
        print(f"Teraz je {now:%H:%M}, nie je 6. hodina, preskakujem."); return
    today = now.strftime("%Y-%m-%d")
    subj, html = build(today, fetch("montaze"), fetch("volno"), fetch("clients"))
    if DRY:
        print(subj); print(html); return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, os.environ["GMAIL_USER"], os.environ["MAIL_TO"]
    msg.set_content("Zobrazte e-mail v HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    print("Odoslané:", subj)


if __name__ == "__main__":
    main()
