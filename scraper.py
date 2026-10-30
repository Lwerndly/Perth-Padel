#!/usr/bin/env python3
"""Refresh data.json for the Perth Padel Availability page.

Runs on GitHub Actions (see .github/workflows/refresh.yml). Reads each venue's
public booking availability and writes data.json next to index.html.

Every run refreshes today plus the next 2 days. Once an hour (the first run
after the top of the hour) it refreshes the full 14 days. Days that were not
refreshed keep their previous slots, so each run stays light on the venues.
"""
import http.cookiejar, json, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

PERTH = timezone(timedelta(hours=8))
DAYS = 14
UA = "Mozilla/5.0 (PerthPadelAvailability; personal court finder)"

VENUES = [
    {"id": "p360", "name": "Padel 360 Cockburn", "suburb": "Yangebup", "platform": "Playtomic",
     "book": "https://playtomic.com/clubs/padel-360-cockburn",
     "tenant": "578aacaa-c3a3-4b7c-998d-59c50153cdaf",
     "courts": {"a110969d-32a7-448f-a382-9e82f76ca904": "Padel 1",
                "d5617435-63be-4a53-b0d0-fccd562ddd2f": "Padel 2",
                "b741285d-f5d9-4584-9f4e-102a888216ea": "Padel 3",
                "926fd9a8-aa25-41d5-8fb9-962a51b797ea": "Padel 4"}},
    {"id": "pcw", "name": "Padel Crush Wembley", "suburb": "Wembley", "platform": "Playtomic",
     "book": "https://playtomic.com/clubs/padel-crush-wembley",
     "tenant": "7101c019-8a3d-46bb-bb11-cf5a0649816f",
     "courts": {"8d68105e-bb0b-4d44-9b6c-46ade360706e": "Padel 1",
                "f61379d2-8575-4d63-bb0f-aceaac7fc593": "Padel 2",
                "fdd152d3-03ac-49e2-ade0-5050f48fe1e1": "Padel 3",
                "f91be122-4f96-416a-ad7c-6a125da23945": "Padel 4"}},
    {"id": "pw", "name": "Padel West Melville", "suburb": "Melville", "platform": "SportLogic",
     "book": "https://padelwest.sportlogic.net.au/secure/customer/booking/v2/public/venue/1",
     "base": "https://padelwest.sportlogic.net.au"},
    {"id": "pp", "name": "Play Padel Reabold", "suburb": "Floreat", "platform": "SportLogic",
     "book": "https://app.playpadel.com.au/secure/customer/booking/v2/public/venue/1",
     "base": "https://app.playpadel.com.au"},
]
UNCHECKED = [{"name": "West Coast Padel", "suburb": "Dalkeith",
              "reason": "Books only through the Wansport app, which has no public web page to read. Book in the app",
              "book": "https://westcoastpadel.com.au/"}]


def get(url, tries=3, opener=None, headers=None):
    opener = opener or urllib.request.build_opener()
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
            with opener.open(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def playtomic(v, dates):
    found = {}
    for d in dates:
        url = f"https://playtomic.com/api/clubs/availability?tenant_id={v['tenant']}&date={d}&sport_id=PADEL"
        for res in json.loads(get(url)):
            court = v["courts"].get(res["resource_id"])
            if not court:
                continue
            for s in res["slots"]:
                t = datetime.fromisoformat(f"{res['start_date']}T{s['start_time']}+00:00").astimezone(PERTH)
                day, hm = t.strftime("%Y-%m-%d"), t.strftime("%H:%M")
                if day not in dates:
                    continue
                price = float(re.sub(r"[^\d.]", "", s["price"]) or 0)
                cur = found.setdefault(day, {}).setdefault(court, {}).get(hm)
                if not cur or s["duration"] < cur[0]:
                    found[day][court][hm] = (s["duration"], price)
        time.sleep(0.5)
    courts = sorted(v["courts"].values())
    slots = {d: {c: [[hm, m, p] for hm, (m, p) in sorted(found.get(d, {}).get(c, {}).items())] for c in courts}
             for d in dates}
    return courts, slots


def sportlogic(v, dates):
    # Open the public booking page first, as a browser does, so the site sets
    # its session cookie; the calendar widget needs it.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    get(v["book"], opener=opener, headers={"Accept": "text/html"})
    ajax = {"X-Requested-With": "XMLHttpRequest", "Referer": v["book"]}
    courts, slots = set(), {}
    for d in dates:
        html = get(f"{v['base']}/secure/customer/booking/v2/public/calendar-widget?date={d.replace('-', '')}",
                   opener=opener, headers=ajax)
        courts.update(int(c) for c in re.findall(r'id="(?:available|waiting)_C(\d+)_\d{8}_\d{4}"', html))
        day = {}
        for c, _, hh, mm in re.findall(r'id="available_C(\d+)_(\d{8})_(\d\d)(\d\d)"', html):
            day.setdefault(f"Court {c}", []).append([f"{hh}:{mm}", 30])
        slots[d] = day
        time.sleep(0.5)
    if not courts:
        raise RuntimeError("no courts found on page")
    names = [f"Court {c}" for c in sorted(courts)]
    for d in dates:
        slots[d] = {c: sorted(slots[d].get(c, [])) for c in names}
    return names, slots


def main():
    now = datetime.now(PERTH)
    full = "--full" in sys.argv or now.minute < 15
    all_dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS)]
    dates = all_dates if full else all_dates[:3]
    try:
        old = json.load(open("data.json"))
    except Exception:
        old = {}
    old_by_id = {v["id"]: v for v in old.get("venues", [])}

    out_venues, ok_times = [], []
    for v in VENUES:
        prev = old_by_id.get(v["id"], {})
        meta = {k: v[k] for k in ("id", "name", "suburb", "platform", "book")}
        if v["platform"] == "Playtomic" and prev:
            # Playtomic refuses requests from GitHub's servers (HTTP 403), so these
            # venues are refreshed from Liam's Mac instead. Keep what is there.
            out_venues.append({**prev, **meta})
            print(f"{v['id']}: skipped (Playtomic is checked from the Mac)")
            continue
        try:
            courts, fresh = (playtomic if v["platform"] == "Playtomic" else sportlogic)(v, dates)
            slots = {d: prev.get("slots", {}).get(d) for d in all_dates}
            slots.update(fresh)
            slots = {d: s for d, s in slots.items() if s is not None}
            stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            out_venues.append({**meta, "status": "ok", "checked": stamp, "courts": courts, "slots": slots})
            ok_times.append(stamp)
            print(f"{v['id']}: ok, {len(dates)} days")
        except Exception as e:
            print(f"{v['id']}: FAILED {e}")
            if prev:
                out_venues.append({**prev, **meta, "status": "error",
                                   "slots": {d: s for d, s in prev.get("slots", {}).items() if d >= all_dates[0]}})
            else:
                out_venues.append({**meta, "status": "error", "checked": None, "courts": [], "slots": {}})

    if not ok_times:
        print("Every venue failed; leaving data.json unchanged.")
        sys.exit(1)
    data = {"updated": min(ok_times), "venues": out_venues, "unchecked": UNCHECKED}
    if old.get("refreshTrigger"):
        data["refreshTrigger"] = old["refreshTrigger"]
    with open("data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
