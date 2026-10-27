import ctypes
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
import tkinter.font as tkfont

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
DATA_DIR = os.path.join(BASE, "data")
STATE_PATH = os.path.join(DATA_DIR, "window.json")
PLAYERS_PATH = os.path.join(DATA_DIR, "players_history.json")
SALES_PATH = os.path.join(DATA_DIR, "sales.json")
LOG_PATH = os.path.join(DATA_DIR, "error.log")
APPS_PATH = os.path.join(DATA_DIR, "apps.json")
STORE_STATS_PATH = os.path.join(DATA_DIR, "store.json")

FIN_BASE = "https://partner.steam-api.com/IPartnerFinancialsService/"
WISHLIST_URL = FIN_BASE + "GetAppWishlistReporting/v001/"
CHANGED_DATES_URL = FIN_BASE + "GetChangedDatesForPartner/v001/"
SALES_URL = FIN_BASE + "GetDetailedSales/v001/"
PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
PARTNER_PLAYERS_URL = "https://partner.steam-api.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
APPLIST_URL = "https://partner.steam-api.com/ISteamApps/GetPartnerAppListForWebAPIKey/v2/"
STORE_URL = "https://store.steampowered.com/api/appdetails"
STEAMDB_URL = "https://steamdb.info/app/{appid}/charts/"
DETAILS_URL = "https://partner.steampowered.com/app/details/{appid}/?dateStart=2000-01-01&dateEnd={date}"
STORE_QUERY_URL = "https://api.steampowered.com/IStoreQueryService/Query/v1/"
STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
FOLLOWERS_URL = "https://steamcommunity.com/games/{appid}/memberslistxml/"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/155.0 Safari/537.36")

DISCOVER_INTERVAL_S = 24 * 3600
RECENT_DAYS = 3
PARALLEL_REQUESTS = 3
SALES_KEEP_DAYS = 45
LOG_MAX_BYTES = 512 * 1024
RANK_SORTS = {"dau": 13, "sellers": 10, "wishlist": 20}
RANK_PAGE = 1000
RANK_MAX_PAGES = 10
RANK_INTERVAL_S = 24 * 3600
FOLLOWERS_INTERVAL_S = 24 * 3600
FOLLOWERS_GAP_S = 10
PLACEHOLDER_PREFIX = "YOUR_"

KEY = "#010203"
WIN_BG = "#1e1e1e"
WIN_BORDER = "#333333"
CARD_BG = "#2a2a2a"
HOVER_BG = "#2b2b2b"
SEP = "#383838"
BTN_BG = "#363636"
TEXT = "#ffffff"
BODY = "#deddda"
DIM = "#9a9996"
GREEN = "#33d17a"
RED = "#f0566a"
BLUE = "#4f8fe6"
PURPLE = "#c061cb"
ORANGE = "#e09a5c"
YELLOW = "#f6d32d"
TILE = {
    "green": ("#1f3a2b", "#33d17a", "#2ec27e"),
    "blue": ("#22304a", "#4f8fe6", None),
    "purple": ("#372640", "#c061cb", None),
    "orange": ("#3a2e24", "#d0915e", None),
}
PILL = {"green": ("#1d3a29", GREEN), "red": ("#3f1d24", RED)}
RING_COLORS = ["#33d17a", "#3584e4", "#ff7800", "#c061cb", "#2190a4", "#e01b24", "#f6d32d", "#9a9996"]

IC_STAR, IC_HEART, IC_GAME, IC_CARD = "", "", "", ""
IC_REFRESH, IC_CLOSE, IC_OPEN = "", "", ""
IC_CHEV_R, IC_CHEV_D = "", ""


def log_error(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def http_text(url, params, timeout=20, ua="SteamDataPCWidget/1.0"):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def http_json(url, params, timeout=20):
    return json.loads(http_text(url, params, timeout))


def http_json_retry(url, params):
    for wait in (5, 15, 30, 60, None):
        try:
            return http_json(url, params)
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)


def is_transient(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, (urllib.error.URLError, TimeoutError, OSError))


def fmt(n):
    return "—" if n is None else f"{n:,}"


def fmt_money(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def fmt_day(d):
    return d.strftime("%b ") + str(d.day)


def steam_today(day_start_hour):
    return (dt.datetime.now() - dt.timedelta(hours=day_start_hour)).date()


def is_placeholder(value):
    return not value or value.startswith(PLACEHOLDER_PREFIX)


class NoWishlistData(Exception):
    pass


class WishlistStore:
    def __init__(self, key, appid):
        self.key = key
        self.appid = appid
        self.path = os.path.join(DATA_DIR, f"wishlist_{appid}.json")
        self.data = load_json(self.path, {"min_date": None, "days": {}})

    def _fetch_day(self, date):
        r = http_json_retry(WISHLIST_URL, {"key": self.key, "appid": self.appid, "date": date}).get("response", {})
        s = r.get("wishlist_summary")
        day = None
        if s is not None:
            day = {k: int(s.get(f"wishlist_{k}", 0)) for k in ("adds", "deletes", "purchases", "gifts")}
        return r.get("app_min_date"), day

    def refresh(self, progress=None):
        today = dt.datetime.now(dt.timezone.utc).date()
        min_date, _ = self._fetch_day(today.isoformat())
        if min_date:
            self.data["min_date"] = min_date
        if not self.data["min_date"]:
            raise NoWishlistData()

        start = dt.date.fromisoformat(self.data["min_date"].replace("/", "-")[:10])
        recent_cutoff = today - dt.timedelta(days=RECENT_DAYS)
        days = self.data["days"]
        needed = []
        d = start
        while d <= today:
            iso = d.isoformat()
            if iso not in days or d >= recent_cutoff:
                needed.append(iso)
            d += dt.timedelta(days=1)

        with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as ex:
            futures = {ex.submit(self._fetch_day, iso): iso for iso in needed}
            for i, fut in enumerate(as_completed(futures), 1):
                self._store_day(futures[fut], fut.result()[1], recent_cutoff)
                if i % 25 == 0:
                    save_json(self.path, self.data)
                    if progress:
                        progress(i, len(needed))
        save_json(self.path, self.data)

    def _store_day(self, iso, day, recent_cutoff):
        days = self.data["days"]
        if day is not None:
            days[iso] = day
        elif dt.date.fromisoformat(iso) < recent_cutoff:
            days[iso] = {"adds": 0, "deletes": 0, "purchases": 0, "gifts": 0}
        else:
            days.pop(iso, None)

    def summary(self, today):
        days = self.data["days"]
        total = sum(v["adds"] - v["deletes"] - v["purchases"] - v["gifts"] for v in days.values())
        return {
            "total": total if days else None,
            "today": days.get(today.isoformat()),
            "yesterday": days.get((today - dt.timedelta(days=1)).isoformat()),
        }


class SalesStore:
    def __init__(self, key):
        self.key = key
        self.data = load_json(SALES_PATH, {"hw": "0", "days": {}})

    def _day_totals(self, date):
        hw, totals = 0, {}
        while True:
            r = http_json_retry(SALES_URL, {"key": self.key, "date": date, "highwatermark_id": hw}).get("response", {})
            for it in r.get("results", []):
                a = str(it.get("primary_appid") or it.get("appid") or 0)
                t = totals.setdefault(a, {"net": 0.0, "gross": 0.0, "units": 0})
                t["net"] += float(it.get("net_sales_usd") or 0)
                t["gross"] += float(it.get("gross_sales_usd") or 0)
                t["units"] += int(it.get("net_units_sold") or 0)
            max_id = int(r.get("max_id", hw))
            if max_id == hw:
                return totals
            hw = max_id

    def refresh(self):
        r = http_json_retry(CHANGED_DATES_URL, {"key": self.key, "highwatermark": self.data["hw"]}).get("response", {})
        cutoff = dt.date.today() - dt.timedelta(days=SALES_KEEP_DAYS)
        dates = [d for d in r.get("dates", []) if dt.date.fromisoformat(d.replace("/", "-")) >= cutoff]
        with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as ex:
            for d, totals in zip(dates, ex.map(self._day_totals, dates)):
                self.data["days"][d.replace("/", "-")] = totals
        self.data["days"] = {d: v for d, v in self.data["days"].items() if dt.date.fromisoformat(d) >= cutoff}
        self.data["hw"] = str(r.get("result_highwatermark", self.data["hw"]))
        save_json(SALES_PATH, self.data)

    def for_app(self, appid, today):
        days = [d for d in sorted(self.data["days"], reverse=True)
                if d <= today.isoformat() and str(appid) in self.data["days"][d]]
        if not days:
            return None, None
        return dt.date.fromisoformat(days[0]), self.data["days"][days[0]][str(appid)]

    def total(self, today):
        days = [d for d in sorted(self.data["days"], reverse=True) if d <= today.isoformat() and self.data["days"][d]]
        if not days:
            return None, None
        return dt.date.fromisoformat(days[0]), sum(v["net"] for v in self.data["days"][days[0]].values())


class StoreStats:
    def __init__(self):
        self.data = load_json(STORE_STATS_PATH, {"ranks_ts": 0, "apps": {}})

    def _app(self, appid):
        return self.data["apps"].setdefault(str(appid), {})

    def for_app(self, appid):
        return self.data["apps"].get(str(appid))

    def refresh_reviews(self, appids):
        inp = {"ids": [{"appid": a} for a in appids], "context": {"language": "english", "country_code": "US"},
               "data_request": {"include_reviews": True}}
        items = http_json(STORE_ITEMS_URL, {"input_json": json.dumps(inp)}).get("response", {}).get("store_items", [])
        for it in items:
            rv = (it.get("reviews") or {}).get("summary_filtered")
            if it.get("appid") and rv:
                self._app(it["appid"]).update(reviews=int(rv.get("review_count", 0)),
                                              review_pct=int(rv.get("percent_positive", 0)))
        save_json(STORE_STATS_PATH, self.data)

    def refresh_followers(self, appids):
        due = [a for a in appids if time.time() - self._app(a).get("followers_ts", 0) >= FOLLOWERS_INTERVAL_S]
        for i, a in enumerate(due):
            if i:
                time.sleep(FOLLOWERS_GAP_S)
            try:
                xml = http_text(FOLLOWERS_URL.format(appid=a), {"xml": 1}, ua=BROWSER_UA)
            except urllib.error.HTTPError as e:
                log_error(f"followers {a}: {e}")
                if e.code == 429:
                    break
                continue
            except (urllib.error.URLError, OSError) as e:
                log_error(f"followers {a}: {e}")
                break
            m = re.search(r"<groupDetails>.*?<memberCount>(\d+)", xml, re.S)
            if m:
                self._app(a).update(followers=int(m.group(1)), followers_ts=int(time.time()))
            save_json(STORE_STATS_PATH, self.data)

    def ranks_due(self):
        return time.time() - self.data.get("ranks_ts", 0) >= RANK_INTERVAL_S

    @staticmethod
    def _query_page(sort, start):
        inp = {"query_name": "widget", "context": {"language": "english", "country_code": "US"},
               "query": {"start": start, "count": RANK_PAGE, "sort": sort}}
        for wait in (5, 15, 30, 60, None):
            try:
                return http_json(STORE_QUERY_URL, {"input_json": json.dumps(inp)}).get("response", {}).get("ids", [])
            except urllib.error.HTTPError as e:
                if wait is None or (e.code != 429 and e.code < 500):
                    raise
            except (urllib.error.URLError, OSError):
                if wait is None:
                    raise
            time.sleep(wait)

    @staticmethod
    def _scored(ids, j):
        near = [it.get("appid") or 0 for it in ids[max(0, j - 10):j + 11]]
        if len(near) < 8:
            return True
        return not (all(x < y for x, y in zip(near, near[1:])) or all(x > y for x, y in zip(near, near[1:])))

    def refresh_ranks(self, appids, progress=None):
        found = {}
        for i, (name, sort) in enumerate(RANK_SORTS.items()):
            ranks = found[name] = {}
            for page in range(RANK_MAX_PAGES):
                if progress and page % 10 == 0:
                    progress(f"store ranks {i + 1}/{len(RANK_SORTS)}")
                ids = self._query_page(sort, page * RANK_PAGE)
                for j, it in enumerate(ids):
                    a = it.get("appid")
                    if a in appids and a not in ranks:
                        ranks[a] = page * RANK_PAGE + j + 1 if self._scored(ids, j) else None
                if not ids or len(ranks) == len(appids):
                    break
                time.sleep(1)
        for a in appids:
            self._app(a)["ranks"] = {name: found[name].get(a) for name in RANK_SORTS}
        self.data["ranks_ts"] = int(time.time())
        save_json(STORE_STATS_PATH, self.data)


def _store_details(appid):
    try:
        v = http_json(STORE_URL, {"appids": appid}).get(str(appid), {})
        return v.get("data") if v.get("success") else None
    except Exception:
        return None


def _base_name(name):
    n = name.strip()
    for suffix in (" Free Demo", " (Demo)", " - Demo", " Demo", " Playtest"):
        if n.lower().endswith(suffix.lower()):
            return n[: -len(suffix)].strip(" -:").lower()
    return n.lower()


def discover_apps(publisher_key, exclude=()):
    r = http_json(APPLIST_URL, {"key": publisher_key, "type_filter": "game,demo"})
    apps = [a for a in (r.get("applist", {}).get("apps", {}).get("app") or []) if a]
    apps = [a for a in apps if int(a["appid"]) not in exclude]
    games = {int(a["appid"]): {"name": a.get("app_name") or str(a["appid"]), "appid": int(a["appid"])}
             for a in apps if a.get("app_type", "").lower() == "game"}
    demos = [a for a in apps if a.get("app_type", "").lower() == "demo"]

    unmatched = []
    for d in demos:
        did = int(d["appid"])
        parent = None
        info = _store_details(did)
        if info and info.get("fullgame"):
            parent = int(info["fullgame"]["appid"])
        if parent not in games:
            want = _base_name(d.get("app_name", ""))
            parent = next((gid for gid, g in games.items() if g["name"].lower() == want), None)
        if parent in games:
            games[parent]["demo_appid"] = did
        else:
            unmatched.append({"name": d.get("app_name") or str(did), "demo_appid": did})
    return sorted(games.values(), key=lambda g: g["name"].lower()) + unmatched


def merge_games(discovered, manual):
    out = {g.get("appid") or g.get("demo_appid"): dict(g) for g in discovered}
    for g in manual:
        k = g.get("appid") or g.get("demo_appid")
        if k:
            out.setdefault(k, {}).update({kk: v for kk, v in g.items() if v})
    return list(out.values())


class PlayerTracker:
    def __init__(self, publisher_key=""):
        self.publisher_key = publisher_key
        self.history = load_json(PLAYERS_PATH, {})

    def poll(self, appid):
        try:
            r = http_json(PLAYERS_URL, {"appid": appid}).get("response", {})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            if not self.publisher_key:
                return None
            try:
                r = http_json(PARTNER_PLAYERS_URL, {"appid": appid, "key": self.publisher_key}).get("response", {})
            except urllib.error.HTTPError as e2:
                if e2.code == 404:
                    return None
                raise
        if r.get("result") != 1:
            return None
        count = int(r.get("player_count", 0))
        now = time.time()
        h = self.history.setdefault(str(appid), [])
        h.append([int(now), count])
        cutoff = now - 26 * 3600
        self.history[str(appid)] = [p for p in h if p[0] >= cutoff]
        return count

    def peak_24h(self, appid):
        h = self.history.get(str(appid), [])
        now = time.time()
        pts = [p for p in h if p[0] >= now - 24 * 3600]
        if not pts:
            return None, 0
        coverage_h = (now - pts[0][0]) / 3600
        return max(c for _, c in pts), coverage_h

    def save(self):
        save_json(PLAYERS_PATH, self.history)


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, splinesteps=16, **kw)


class Widget:
    W = 380
    PAD = 12

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.day_start = int(config.get("day_start_hour", 10))
        self.key = config.get("financial_api_key", "").strip()
        self.has_key = not is_placeholder(self.key)
        pub = config.get("publisher_api_key", "").strip()
        self.pub_key = "" if is_placeholder(pub) else pub
        self.exclude = {int(a) for a in config.get("exclude_appids", [])}
        self.manual_games = [g for g in config.get("games", []) if g.get("appid") or g.get("demo_appid")]
        self.discovered = load_json(APPS_PATH, {}) if self.pub_key else {}
        self.discover_error = None
        self._set_games(merge_games(self.discovered.get("games", []), self.manual_games))
        self.players = PlayerTracker(self.pub_key)
        self.sales = SalesStore(self.key)
        self.store = StoreStats()
        self.wl_error = {}
        self.wl_nodata = set()
        self.player_now = {}
        self.last_update = None
        self.busy = False
        self.progress_text = None
        self.steam_down = None
        self._moved = False

        self.f_title = tkfont.Font(family="Segoe UI", size=-15, weight="bold")
        self.f_date = tkfont.Font(family="Segoe UI", size=-12, weight="bold")
        self.f_body = tkfont.Font(family="Segoe UI", size=-14)
        self.f_small = tkfont.Font(family="Segoe UI", size=-12)
        self.f_pill = tkfont.Font(family="Segoe UI", size=-12, weight="bold")
        self.f_tile_num = tkfont.Font(family="Segoe UI", size=-16, weight="bold")
        self.f_tile_lbl = tkfont.Font(family="Segoe UI", size=-15, weight="bold")
        self.f_section = tkfont.Font(family="Segoe UI", size=-15, weight="bold")
        self.f_icon = tkfont.Font(family="Segoe MDL2 Assets", size=-14)
        self.f_icon_s = tkfont.Font(family="Segoe MDL2 Assets", size=-10)

        self._setup_window()
        self.draw()
        self.root.after(200, self.manual_refresh)

    def _setup_window(self):
        r = self.root
        r.title("Steam Stats")
        r.overrideredirect(True)
        r.configure(bg=KEY)
        r.attributes("-transparentcolor", KEY)
        state = load_json(STATE_PATH, {})
        self.topmost = state.get("topmost", False)
        self.expanded = set(state.get("expanded", []))
        r.attributes("-topmost", self.topmost)
        r.attributes("-alpha", self.config.get("opacity", 0.97))
        x, y = state.get("x", 60), state.get("y", 60)
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = gsm(76), gsm(77), gsm(78), gsm(79)
        except Exception:
            vx, vy, vw, vh = 0, 0, r.winfo_screenwidth(), r.winfo_screenheight()
        if not (vx - 300 < x < vx + vw - 60 and vy - 10 < y < vy + vh - 60):
            x, y = 60, 60
        r.geometry(f"+{x}+{y}")

        self.c = tk.Canvas(r, width=self.W, height=200, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack()
        self.c.bind("<ButtonPress-1>", self._drag_start)
        self.c.bind("<B1-Motion>", self._drag_move)
        self.c.bind("<ButtonRelease-1>", lambda e: self._save_state())
        self.c.bind("<Button-3>", self._menu)

        self.menu = tk.Menu(r, tearoff=0, bg=CARD_BG, fg=BODY, activebackground="#3584e4",
                            activeforeground=TEXT, bd=0)
        self.menu.add_command(label="Refresh", command=self.manual_refresh)
        self.topmost_var = tk.BooleanVar(value=self.topmost)
        self.menu.add_checkbutton(label="Always on top", variable=self.topmost_var, command=self._toggle_topmost)
        self.startup_var = tk.BooleanVar(value=os.path.exists(startup_path()))
        self.menu.add_checkbutton(label="Start with Windows", variable=self.startup_var, command=self._toggle_startup)
        self.menu.add_command(label="Open settings (config.json)", command=lambda: os.startfile(CONFIG_PATH))
        self.menu.add_separator()
        self.menu.add_command(label="Close", command=self._close)

    def _drag_start(self, e):
        self._moved = False
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self._moved = True
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _save_state(self):
        save_json(STATE_PATH, {"x": self.root.winfo_x(), "y": self.root.winfo_y(), "topmost": self.topmost,
                               "expanded": sorted(self.expanded)})

    def _close(self):
        self._save_state()
        self.root.after(0, self.root.destroy)

    def _menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def _toggle_topmost(self):
        self.topmost = self.topmost_var.get()
        self.root.attributes("-topmost", self.topmost)
        self._save_state()

    def _toggle_startup(self):
        path = startup_path()
        if self.startup_var.get():
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'CreateObject("WScript.Shell").Run """{pyw}"" ""{os.path.abspath(__file__)}""", 0, False\n')
        elif os.path.exists(path):
            os.remove(path)

    def _set_games(self, games):
        self.games = games
        self.wishlists = {g["appid"]: WishlistStore(self.key, g["appid"]) for g in games if g.get("appid")}

    def _on_click(self, tag, action):
        def handler(e):
            if not self._moved:
                action()
        self.c.tag_bind(tag, "<ButtonRelease-1>", handler)
        self.c.tag_bind(tag, "<Enter>", lambda e: self.c.configure(cursor="hand2"))
        self.c.tag_bind(tag, "<Leave>", lambda e: self.c.configure(cursor=""))

    def _hover(self, tag, rect):
        self.c.tag_bind(tag, "<Enter>", lambda e: (self.c.itemconfigure(rect, fill=HOVER_BG),
                                                    self.c.configure(cursor="hand2")), add="+")
        self.c.tag_bind(tag, "<Leave>", lambda e: (self.c.itemconfigure(rect, fill=WIN_BG),
                                                    self.c.configure(cursor="")), add="+")

    def _toggle(self, key):
        self.expanded ^= {key}
        self._save_state()
        self.draw()

    def _game_rows(self, today):
        rows = []
        for i, g in enumerate(sorted(self.games, key=lambda g: g.get("appid") or g.get("demo_appid"))):
            appid = g.get("appid")
            key = str(appid or g.get("demo_appid"))
            wl = None
            if appid and self.has_key and appid not in self.wl_nodata and appid in self.wishlists:
                wl = self.wishlists[appid].summary(today)
                if wl["total"] is None and appid not in self.wl_error:
                    wl = None
            players = [(k, g[k]) for k in ("appid", "demo_appid")
                       if g.get(k) and self.player_now.get(g[k], "?") is not None]
            sale_day, sale = self.sales.for_app(appid, today) if appid and self.has_key else (None, None)
            if not wl and not players and not sale:
                continue
            rows.append({"g": g, "key": key, "wl": wl, "players": players, "sale_day": sale_day,
                         "sale": sale, "color": RING_COLORS[i % len(RING_COLORS)]})
        rows.sort(key=lambda r: (-(r["wl"]["today"]["adds"] if r["wl"] and r["wl"]["today"] else -1),
                                 -((r["wl"] or {}).get("total") or 0)))
        return rows

    def draw(self):
        c, W, P = self.c, self.W, self.PAD
        c.delete("all")
        today = steam_today(self.day_start)
        rows = self._game_rows(today)

        y = 26
        c.create_text(P + 6, y, text="Steam Stats", font=self.f_title, fill=TEXT, anchor="w")
        x = P + 6 + self.f_title.measure("Steam Stats") + 8
        c.create_text(x, y + 1, text=f"{today:%a} {fmt_day(today)}", font=self.f_date, fill=TEXT, anchor="w")
        cx = W - P - 14
        c.create_oval(cx - 12, y - 12, cx + 12, y + 12, fill=BTN_BG, outline="", tags="close")
        c.create_text(cx, y, text=IC_CLOSE, font=self.f_icon_s, fill=TEXT, tags="close")
        self._on_click("close", self._close)
        rx = cx - 32
        c.create_oval(rx - 12, y - 12, rx + 12, y + 12, fill=BTN_BG, outline="", tags="refresh")
        c.create_text(rx, y, text=IC_REFRESH, font=self.f_icon_s, fill=BLUE if self.busy else TEXT, tags="refresh")
        self._on_click("refresh", self.manual_refresh)

        y = 52
        tw = (W - 2 * P - 8) / 2
        th = 58
        wl_rows = [r["wl"] for r in rows if r["wl"]]
        adds = sum(w["today"]["adds"] for w in wl_rows if w["today"]) if wl_rows else None
        total = sum(w["total"] or 0 for w in wl_rows) if wl_rows else None
        now_players = [v for v in self.player_now.values() if v is not None]
        sale_day, sale_total = self.sales.total(today) if self.has_key else (None, None)
        sale_lbl = "Revenue" if sale_day in (None, today) else f"Revenue · {fmt_day(sale_day)}"
        tiles = [
            ("green", IC_STAR, "Today", f"+{fmt(adds)}" if adds is not None else "—"),
            ("blue", IC_HEART, "Wishlists", fmt(total)),
            ("purple", IC_GAME, "Players", fmt(sum(now_players)) if now_players else "—"),
            ("orange", IC_CARD, sale_lbl, fmt_money(sale_total) if sale_total is not None else "—"),
        ]
        for i, (color, icon, label, value) in enumerate(tiles):
            bg, fg, border = TILE[color]
            tx = P + (i % 2) * (tw + 8)
            ty = y + (i // 2) * (th + 8)
            rrect(c, tx, ty, tx + tw, ty + th, 10, fill=bg, outline=border or "", width=1.5 if border else 0)
            c.create_text(tx + 12, ty + 17, text=icon, font=self.f_icon, fill=fg, anchor="w")
            c.create_text(tx + tw - 12, ty + 17, text=value, font=self.f_tile_num, fill=fg, anchor="e")
            c.create_text(tx + 12, ty + 41, text=label, font=self.f_tile_lbl, fill=fg, anchor="w")
        y += 2 * th + 8 + 22

        c.create_text(P + 4, y, text="Games", font=self.f_section, fill=TEXT, anchor="w")
        y += 14
        c.create_line(P + 4, y, W - P - 4, y, fill=SEP)
        y += 6
        if not rows:
            msg = self.discover_error or ("loading games…" if self.pub_key
                                          else "add publisher_api_key to config.json")
            c.create_text(P + 6, y + 16, text=msg, font=self.f_small, fill=DIM, anchor="w")
            y += 34
        for r in rows:
            y = self._draw_game(r, y, today)

        y += 6
        if self.busy:
            status, color = self.progress_text or "updating…", BLUE
        elif self.steam_down:
            last = f" · last data {self.last_update:%H:%M}" if self.last_update else ""
            status, color = f"{self.steam_down}{last}", RED
        elif self.last_update:
            status, color = f"updated {self.last_update:%H:%M}", DIM
        else:
            status, color = "loading…", DIM
        c.create_text(P + 6, y + 8, text=status, font=self.f_small, fill=color, anchor="w")
        H = int(y + 26)

        bg = rrect(c, 0, 0, W - 1, H - 1, 14, fill=WIN_BG, outline=WIN_BORDER)
        c.tag_lower(bg)
        c.configure(height=H)
        self.root.geometry(f"{W}x{H}")

    def _draw_game(self, r, y, today):
        c, W, P = self.c, self.W, self.PAD
        g, key, wl = r["g"], r["key"], r["wl"]
        open_ = key in self.expanded
        tag = f"row{key}"
        rh = 34
        hover = rrect(c, P, y, W - P, y + rh, 8, fill=WIN_BG, outline="", tags=tag)
        cy = y + rh / 2
        c.create_oval(P + 8, cy - 8, P + 24, cy + 8, outline=r["color"], width=2, tags=tag)
        c.create_text(P + 34, cy, text=g.get("name", key), font=self.f_body, fill=BODY, anchor="w", tags=tag)
        c.create_text(W - P - 10, cy, text=IC_CHEV_D if open_ else IC_CHEV_R, font=self.f_icon_s,
                      fill=DIM, anchor="e", tags=tag)
        rx = W - P - 28
        if wl and wl["total"] is not None:
            c.create_text(rx, cy, text=fmt(wl["total"]), font=self.f_small, fill=DIM, anchor="e", tags=tag)
            rx -= self.f_small.measure(fmt(wl["total"])) + 8
            t = wl["today"]
            if t:
                net = t["adds"] - t["deletes"]
                if net:
                    rx = self._pill(rx, cy, f"+{fmt(net)}" if net > 0 else f"−{fmt(-net)}",
                                    "green" if net > 0 else "red", tag)
        elif r["players"]:
            now = self.player_now.get(r["players"][0][1])
            text = f"{fmt(now)} players"
            c.create_text(rx, cy, text=text, font=self.f_small, fill=DIM, anchor="e", tags=tag)
            rx -= self.f_small.measure(text) + 8

        if not open_:
            counts = [(self.player_now.get(a), "in game" if kind == "appid" else "in demo") for kind, a in r["players"]]
            counts = [(n, where) for n, where in counts if n]
            if counts:
                x = P + 34 + self.f_body.measure(g.get("name", key)) + 10
                for text in (" · ".join(f"{fmt(n)} {where}" for n, where in counts),
                             " ".join(fmt(n) for n, _ in counts)):
                    if x + 10 + self.f_small.measure(text) < rx:
                        c.create_oval(x, cy - 3, x + 6, cy + 3, fill=PURPLE, outline="", tags=tag)
                        c.create_text(x + 10, cy, text=text, font=self.f_small, fill=PURPLE, anchor="w", tags=tag)
                        break
        self._on_click(tag, lambda: self._toggle(key))
        self._hover(tag, hover)
        y += rh

        if not open_:
            return y

        lines = []
        if wl and g.get("appid") in self.wl_error:
            lines.append(("Wishlist", RED, [(self.wl_error[g["appid"]][:40], RED)], None))
        elif wl:
            for label, d in (("Today", wl["today"]), ("Yesterday", wl["yesterday"])):
                if d:
                    lines.append((label, GREEN if d["adds"] >= d["deletes"] else RED, [
                        (f"+{fmt(d['adds'])}", GREEN if d["adds"] else DIM), ("   ", DIM),
                        (f"−{fmt(d['deletes'])}", RED if d["deletes"] else DIM)], None))
            lines.append(("Total", BLUE, [(fmt(wl["total"]), TEXT), (" wishlists", DIM)], None))
        for kind, appid in r["players"]:
            now = self.player_now.get(appid)
            peak, cov = self.players.peak_24h(appid)
            segs = [(fmt(now), TEXT), (" now · 24h peak ", DIM), (("~" if cov < 23 else "") + fmt(peak), TEXT)]
            lines.append(("Game" if kind == "appid" else "Demo", PURPLE, segs, appid))
        if r["sale"]:
            s = r["sale"]
            segs = [(fmt_money(s["net"]), TEXT), (f" net · {fmt(s['units'])} sold", DIM)]
            if r["sale_day"] != today:
                segs.append((f" · {fmt_day(r['sale_day'])}", DIM))
            lines.append(("Revenue", ORANGE, segs, None))
        lines += self._store_lines(g.get("appid"))

        lh = 26
        top = y + 2
        bottom = top + len(lines) * lh + 8
        rrect(c, P + 2, top, W - P - 2, bottom, 8, fill=CARD_BG, outline="")
        ly = top + 4
        for label, bar, segs, link in lines:
            my = ly + lh / 2
            c.create_text(P + 12, my, text=label, font=self.f_small, fill=DIM, anchor="w")
            c.create_rectangle(P + 70, my - 7, P + 72, my + 7, fill=bar, outline="")
            x = P + 80
            for text, color in segs:
                c.create_text(x, my, text=text, font=self.f_body, fill=color, anchor="w")
                x += self.f_body.measure(text)
            if link:
                self._draw_links(link, my, today)
            ly += lh
        return bottom + 6

    def _store_lines(self, appid):
        st = self.store.for_app(appid) if appid else None
        if not st:
            return []
        lines = []
        ranks = st.get("ranks") or {}
        segs = []
        for name, label in (("dau", " DAU"), ("sellers", " top sellers")):
            if ranks.get(name):
                segs += [("   " if segs else "", DIM), (f"#{fmt(ranks[name])}", TEXT), (label, DIM)]
        if segs:
            lines.append(("Rank", YELLOW, segs, None))
        if ranks.get("wishlist"):
            lines.append(("Rank" if not segs else "", YELLOW,
                          [(f"#{fmt(ranks['wishlist'])}", TEXT), (" wishlist activity", DIM)], None))
        segs = []
        if st.get("followers") is not None:
            segs += [(fmt(st["followers"]), TEXT), (" followers", DIM)]
        if st.get("reviews"):
            segs += [(" · " if segs else "", DIM), (fmt(st["reviews"]), TEXT),
                     (f" reviews {st.get('review_pct', 0)}%", DIM)]
        if segs:
            lines.append(("Store", YELLOW, segs, None))
        return lines

    def _draw_links(self, appid, my, today):
        c, W, P = self.c, self.W, self.PAD
        lx = W - P - 12
        for name, color, url in (
                ("SteamDB", DIM, STEAMDB_URL.format(appid=appid)),
                ("Detail", BLUE, DETAILS_URL.format(appid=appid, date=today.isoformat()))):
            ltag = f"{name}{appid}"
            c.create_text(lx, my, text=IC_OPEN, font=self.f_icon_s, fill=color, anchor="e", tags=ltag)
            lx -= 14
            c.create_text(lx, my, text=name, font=self.f_small, fill=color, anchor="e", tags=ltag)
            lx -= self.f_small.measure(name) + 10
            self._on_click(ltag, lambda u=url: webbrowser.open(u))

    def _pill(self, x_right, cy, text, kind, tag):
        bg, fg = PILL[kind]
        w = self.f_pill.measure(text) + 14
        rrect(self.c, x_right - w, cy - 10, x_right, cy + 10, 6, fill=bg, outline="", tags=tag)
        self.c.create_text(x_right - w / 2, cy, text=text, font=self.f_pill, fill=fg, tags=tag)
        return x_right - w - 6

    def refresh_all(self, rediscover=False):
        if self.busy:
            return
        self.busy = True
        self.progress_text = None
        self.draw()
        threading.Thread(target=self._worker, args=(rediscover,), daemon=True).start()

    def manual_refresh(self):
        self.refresh_all(rediscover=True)

    def _discover(self, force=False):
        if not self.pub_key:
            return
        if not force and self.games and time.time() - self.discovered.get("ts", 0) < DISCOVER_INTERVAL_S:
            return
        try:
            found = discover_apps(self.pub_key, self.exclude)
            self.discover_error = None if found else "no apps are linked to this publisher key"
        except urllib.error.HTTPError as e:
            self.discover_error = "invalid publisher key (403)" if e.code in (401, 403) else f"HTTP {e.code}"
            log_error(f"discover: {e}")
            return
        except Exception as e:
            self.discover_error = str(e)
            log_error(f"discover: {e}")
            return
        self.discovered = {"ts": int(time.time()), "games": found}
        save_json(APPS_PATH, self.discovered)
        self._set_games(merge_games(found, self.manual_games))

    def _mark_down(self, e):
        code = getattr(e, "code", None)
        self.steam_down = f"Steam not responding ({code})" if code else "offline"

    def _worker(self, rediscover=False):
        self.steam_down = None
        ok = True
        redraw = lambda: self.root.after(0, self.draw)
        try:
            self._discover(force=rediscover)
            for g in self.games:
                for kind in ("appid", "demo_appid"):
                    a = g.get(kind)
                    if not a:
                        continue
                    try:
                        self.player_now[a] = self.players.poll(a)
                    except Exception as e:
                        ok = False
                        self._mark_down(e)
                        log_error(f"players {a}: {e}")
            self.players.save()
            redraw()

            if self.has_key:
                names = {g.get("appid"): g.get("name", "") for g in self.games}
                for appid, store in list(self.wishlists.items()):
                    def progress(done, total, name=names.get(appid, appid)):
                        self.progress_text = f"{name}: wishlist history {done}/{total}"
                        redraw()
                    try:
                        store.refresh(progress)
                        self.wl_error.pop(appid, None)
                        self.wl_nodata.discard(appid)
                    except NoWishlistData:
                        self.wl_nodata.add(appid)
                    except Exception as e:
                        if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
                            self.wl_error[appid] = "invalid financial API key (403)"
                        elif is_transient(e):
                            ok = False
                            self._mark_down(e)
                        else:
                            self.wl_error[appid] = str(e)
                            log_error(f"wishlist {appid}: {traceback.format_exc()}")
                            continue
                        log_error(f"wishlist {appid}: {e}")
                    redraw()
                try:
                    self.progress_text = "updating sales…"
                    self.sales.refresh()
                except Exception as e:
                    ok = False
                    self._mark_down(e)
                    log_error(f"sales: {e if is_transient(e) else traceback.format_exc()}")
            self._refresh_store(redraw)
            if ok:
                self.last_update = dt.datetime.now()
        finally:
            self.busy = False
            redraw()

    def _refresh_store(self, redraw):
        appids = [g["appid"] for g in self.games if g.get("appid")]
        if not appids:
            return
        try:
            self.progress_text = "updating store data…"
            redraw()
            self.store.refresh_reviews(appids)
            self.store.refresh_followers(appids)
            if self.store.ranks_due():
                def progress(text):
                    self.progress_text = text
                    redraw()
                self.store.refresh_ranks(set(appids), progress)
        except Exception as e:
            log_error(f"store: {e if is_transient(e) else traceback.format_exc()}")

    def schedule(self):
        interval = max(1, int(self.config.get("refresh_interval_min", 60))) * 60

        def ms_to_next_slot():
            now = time.time()
            return int(((now // interval + 1) * interval - now) * 1000) + 2000

        def tick():
            if self.busy:
                self.root.after(60_000, tick)
                return
            self.refresh_all()
            self.root.after(ms_to_next_slot(), tick)

        self.root.after(ms_to_next_slot(), tick)


def startup_path():
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup", "SteamStatsWidget.vbs")


DEFAULT_CONFIG = {
    "financial_api_key": "YOUR_FINANCIAL_API_KEY",
    "publisher_api_key": "YOUR_PUBLISHER_API_KEY",
    "exclude_appids": [],
    "games": [],
    "refresh_interval_min": 60,
    "day_start_hour": 10,
    "opacity": 0.97,
}


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, DEFAULT_CONFIG)
    config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    root = tk.Tk()
    widget = Widget(root, config)
    widget.schedule()
    root.mainloop()


if __name__ == "__main__":
    main()
