import json
import os
import random
import sys
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

from kivy.animation import Animation
from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import FadeTransition, NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

APP_VERSION = "bulles-1"
WEB_OK = platform == "android" or bool(os.environ.get("PRONOTE_TEST_WEB"))  # fenêtre EduConnect : Android seulement
Window.softinput_mode = "below_target"


# =====================================================================================
#  Journal (pour comprendre ce qui ne marche pas) : jamais de mot de passe, jeton ni cookie
# =====================================================================================
LOG_LINES = []
SECRETS = set()


def scrub(text):
    text = str(text)
    for secret in sorted(SECRETS, key=len, reverse=True):
        if secret and len(secret) >= 3:
            text = text.replace(secret, "***")
    return text


def log(msg, *args):
    try:
        line = time.strftime("%H:%M:%S ") + scrub(msg % args if args else msg)
    except Exception:
        line = time.strftime("%H:%M:%S ") + scrub(msg)
    LOG_LINES.append(line)
    del LOG_LINES[:-400]
    try:
        app = App.get_running_app()
        if app:
            os.makedirs(app.user_data_dir, exist_ok=True)
            with open(os.path.join(app.user_data_dir, "app.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def _hook(exc_type, exc, tb):
    log("ERREUR NON GÉRÉE : %s : %s | %s", getattr(exc_type, "__name__", exc_type), exc,
        " > ".join("%s:%s" % (os.path.basename(f.filename), f.lineno) for f in traceback.extract_tb(tb)[-4:]))


class _KivyErrors(ExceptionHandler):
    def handle_exception(self, inst):
        _hook(type(inst), inst, inst.__traceback__)
        return ExceptionManager.PASS


sys.excepthook = _hook
threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)
ExceptionManager.add_handler(_KivyErrors())


# =====================================================================================
#  Statistiques (fonctions simples, sans interface : faciles à tester)
# =====================================================================================
def num(x):
    """'15,5' -> 15.5 ; 'Absent', 'Disp', '' ou None -> None"""
    try:
        return float(str(x).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def norm20(value, out_of="20"):
    v, o = num(value), num(out_of)
    if v is None or not o or o <= 0:
        return None
    return v / o * 20


def fmt(x, nd=1):
    return "—" if x is None else ("%.*f" % (nd, x)).replace(".", ",")


# (note minimale, libellé, couleur) : palette adaptée au daltonisme, et le libellé écrit accompagne toujours la couleur
LEVELS = [
    (16, "Excellent", (0.34, 0.71, 0.91)),
    (14, "Très bien", (0.00, 0.62, 0.45)),
    (12, "Bien", (0.94, 0.89, 0.26)),
    (10, "Correct", (0.90, 0.62, 0.00)),
    (8, "Fragile", (0.84, 0.37, 0.00)),
    (-1, "À revoir", (0.80, 0.47, 0.65)),
]


def level(v):
    if v is None:
        return "Sans note", (0.5, 0.5, 0.55)
    for lo, label, col in LEVELS:
        if v >= lo:
            return label, col
    return LEVELS[-1][1], LEVELS[-1][2]


def _lum(c):
    f = lambda u: u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def best_text(rgb):
    """Noir ou blanc : celui qui est le plus lisible (contraste le plus élevé) sur cette couleur."""
    l = _lum(rgb)
    return (0, 0, 0, 1) if (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) else (1, 1, 1, 1)


def wavg(pairs):
    """Moyenne pondérée [(valeur, coefficient)]"""
    pairs = [(v, c if c and c > 0 else 1.0) for v, c in pairs if v is not None]
    tot = sum(c for _, c in pairs)
    return sum(v * c for v, c in pairs) / tot if tot else None


BINS = [(0, 8, "<8"), (8, 10, "8-10"), (10, 12, "10-12"), (12, 14, "12-14"), (14, 16, "14-16"), (16, 20.01, "16+")]


def compute_stats(d):
    """À partir des données Pronote (dict) : moyennes, matières, répartition, évolution, devoirs, cours..."""
    grades = [g for g in d.get("grades", []) if g.get("v") is not None]
    by_sub = {}
    for g in grades:
        by_sub.setdefault(g["subject"], []).append(g)
    official = {a["subject"]: a for a in d.get("averages", [])}
    subs = []
    for name in list(by_sub) + [n for n in official if n not in by_sub]:
        gl = by_sub.get(name, [])
        calc = wavg([(g["v"], g.get("coef") or 1.0) for g in gl])
        off = official.get(name, {})
        avg = norm20(off.get("student"), off.get("out_of") or "20")
        avg = avg if avg is not None else calc
        cls = norm20(off.get("class_avg"), off.get("out_of") or "20")
        if cls is None:
            cl = [g["class_v"] for g in gl if g.get("class_v") is not None]
            cls = sum(cl) / len(cl) if cl else None
        recent = sorted(gl, key=lambda g: g["date"])
        trend = None
        if len(recent) >= 2:
            diff = recent[-1]["v"] - (sum(g["v"] for g in recent[:-1]) / (len(recent) - 1))
            trend = "up" if diff > 1 else "down" if diff < -1 else "flat"
        label, col = level(avg)
        subs.append({"name": name, "avg": avg, "class": cls, "n": len(gl), "level": label, "color": col,
                     "trend": trend, "gap": None if avg is None or cls is None else avg - cls})
    subs.sort(key=lambda s: (s["avg"] is None, -(s["avg"] or 0)))
    ranked = [s for s in subs if s["avg"] is not None]
    overall = norm20(d.get("overall"), "20")
    if overall is None and ranked:
        overall = sum(s["avg"] for s in ranked) / len(ranked)
    class_overall = norm20(d.get("class_overall"), "20")
    bins = [sum(1 for g in grades if lo <= g["v"] < hi) for lo, hi, _ in BINS]
    chron = sorted(grades, key=lambda g: g["date"])
    recent_vals = [g["v"] for g in chron][-12:]
    trend = None
    if len(chron) >= 4:
        last = [g["v"] for g in chron][-3:]
        prev = [g["v"] for g in chron][-6:-3]
        if prev:
            diff = sum(last) / len(last) - sum(prev) / len(prev)
            trend = "up" if diff > 0.5 else "down" if diff < -0.5 else "flat"
    best = max(grades, key=lambda g: g["v"]) if grades else None
    worst = min(grades, key=lambda g: g["v"]) if grades else None
    hw = d.get("homework", [])
    todo = [h for h in hw if not h.get("done")]
    today = d.get("today", "")
    lessons_today = [l for l in d.get("lessons", []) if l.get("day") == today]
    now = d.get("now", "")
    nxt = next((l for l in lessons_today if not l.get("canceled") and l["end"] > now), None)
    return {
        "overall": overall, "class_overall": class_overall,
        "gap": None if overall is None or class_overall is None else overall - class_overall,
        "level": level(overall), "n_grades": len(grades), "subjects": subs,
        "best_subject": ranked[0] if ranked else None, "worst_subject": ranked[-1] if len(ranked) > 1 else None,
        "best": best, "worst": worst, "bins": bins, "recent": recent_vals, "trend": trend,
        "hw_todo": len(todo), "hw_total": len(hw), "next_hw": todo[0] if todo else None,
        "lessons_today": len(lessons_today), "canceled_today": sum(1 for l in lessons_today if l.get("canceled")),
        "next_lesson": nxt,
        "absences": d.get("absences", 0), "delays": d.get("delays", 0), "punishments": d.get("punishments", 0),
    }


def first_name(full):
    """Pronote écrit « NOM Prénom » : on prend le premier mot qui n'est pas tout en majuscules."""
    parts = (full or "").split()
    return next((t for t in parts if t != t.upper()), parts[-1] if parts else "").title()


DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def day_label(iso):
    try:
        dd = datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return iso
    t = date.today()
    if dd == t:
        return "Aujourd'hui"
    if dd == t + timedelta(days=1):
        return "Demain"
    return "%s %02d/%02d" % (DAYS[dd.weekday()], dd.day, dd.month)


def build_data(c):
    """Lit toutes les données utiles dans pronotepy (chaque partie est protégée : une erreur n'arrête pas le reste)."""
    d = {"name": "", "klass": "", "period": "", "grades": [], "averages": [], "overall": None, "class_overall": None,
         "homework": [], "lessons": [], "absences": 0, "delays": 0, "punishments": 0, "warnings": []}
    today = date.today()
    d["today"] = today.isoformat()
    d["now"] = datetime.now().strftime("%H:%M")
    d["updated"] = d["now"]

    def part(name, fn):
        try:
            fn()
        except Exception as e:
            d["warnings"].append("%s : %s" % (name, type(e).__name__))
            log("données : « %s » indisponible (%s : %s)", name, type(e).__name__, str(e)[:100])

    def ident():
        d["name"] = c.info.name
        try:
            d["klass"] = c.info.class_name
        except Exception:
            pass
    part("identité", ident)
    period = None
    try:
        period = c.current_period
        d["period"] = period.name
    except Exception as e:
        d["warnings"].append("période")
        log("données : période indisponible (%s)", type(e).__name__)

    def grades():
        for g in period.grades:
            out = g.out_of or "20"
            d["grades"].append({
                "subject": g.subject.name, "date": g.date.isoformat(), "text": str(g.grade), "out_of": out,
                "v": norm20(g.grade, out), "coef": num(g.coefficient) or 1.0, "class_v": norm20(g.average, out),
                "min": norm20(g.min, out), "max": norm20(g.max, out), "comment": g.comment or ""})
    if period:
        part("notes", grades)

        def averages():
            for a in period.averages:
                d["averages"].append({"subject": a.subject.name, "student": a.student, "class_avg": a.class_average,
                                      "out_of": a.out_of or "20"})
        part("moyennes", averages)
        part("moyenne générale", lambda: d.update(overall=period.overall_average))
        part("moyenne de la classe", lambda: d.update(class_overall=period.class_overall_average))
        part("absences", lambda: d.update(absences=sum(a.days or 0 for a in period.absences)))
        part("retards", lambda: d.update(delays=len(period.delays)))
        part("punitions", lambda: d.update(punishments=len(period.punishments)))

    def homework():
        for h in c.homework(today, today + timedelta(days=14)):
            d["homework"].append({"subject": h.subject.name, "text": h.description or "", "date": h.date.isoformat(),
                                  "done": bool(h.done)})
        d["homework"].sort(key=lambda h: h["date"])
    part("devoirs", homework)

    def lessons():
        for l in c.lessons(today, today + timedelta(days=5)):
            d["lessons"].append({
                "day": l.start.date().isoformat(), "start": l.start.strftime("%H:%M"), "end": l.end.strftime("%H:%M"),
                "subject": l.subject.name if l.subject else (l.status or "Cours"), "room": l.classroom or "",
                "teacher": l.teacher_name or "", "canceled": bool(l.canceled), "test": bool(getattr(l, "test", False))})
        d["lessons"].sort(key=lambda l: (l["day"], l["start"]))
    part("emploi du temps", lessons)
    return d


# =====================================================================================
#  Thèmes, taille du texte, animations (réglages d'accessibilité)
# =====================================================================================
THEMES = {
    "dark": dict(bg=(.05, .06, .11, 1), card=(.11, .12, .20, 1), card2=(.16, .17, .28, 1), text=(.96, .96, .99, 1),
                 sub=(.68, .70, .82, 1), acc=(.58, .52, 1, 1), on_acc=(0, 0, 0, 1), track=(.22, .23, .36, 1), deco=(.58, .52, 1, .08)),
    "light": dict(bg=(.94, .95, .99, 1), card=(1, 1, 1, 1), card2=(.89, .91, .98, 1), text=(.07, .08, .14, 1),
                  sub=(.34, .36, .48, 1), acc=(.33, .27, .83, 1), on_acc=(1, 1, 1, 1), track=(.82, .85, .94, 1), deco=(.33, .27, .83, .09)),
    "contrast": dict(bg=(0, 0, 0, 1), card=(.09, .09, .09, 1), card2=(.19, .19, .19, 1), text=(1, 1, 1, 1),
                     sub=(.92, .92, .92, 1), acc=(1, .85, 0, 1), on_acc=(0, 0, 0, 1), track=(.35, .35, .35, 1), deco=(1, 1, 1, 0)),
}
TH = dict(THEMES["dark"])
PREFS = {"scale": 1.0, "theme": "dark", "anim": True, "link": ""}


def apply_theme():
    TH.clear()
    TH.update(THEMES.get(PREFS["theme"], THEMES["dark"]))
    Window.clearcolor = TH["bg"]


def fs(n):
    return sp(n * PREFS["scale"])


def R(n):
    return dp(n)


# =====================================================================================
#  Éléments « bulles »
# =====================================================================================
class Bubble(BoxLayout):
    """Conteneur tout rond (coins très arrondis)."""

    def __init__(self, fill=None, radius=26, auto=False, **kw):
        kw.setdefault("padding", R(16))
        kw.setdefault("spacing", R(6))
        super().__init__(**kw)
        self.radius = radius
        with self.canvas.before:
            self._c = Color(*(fill or TH["card"]))
            self._r = RoundedRectangle(radius=[R(radius)])
        self.bind(pos=self._u, size=self._u)
        if auto:
            self.size_hint_y = None
            self.bind(minimum_height=self.setter("height"))

    def _u(self, *_):
        self._r.pos, self._r.size = self.pos, self.size
        self._r.radius = [min(R(self.radius), self.height / 2)]


class Txt(Label):
    """Texte qui revient à la ligne et prend la hauteur qu'il lui faut."""

    def __init__(self, text="", size=14, color="text", bold=False, halign="left", **kw):
        col = TH[color] if isinstance(color, str) else color
        super().__init__(text=text, font_size=fs(size), color=col, bold=bold, halign=halign, valign="middle",
                         size_hint_y=None, **kw)
        self.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        self.bind(texture_size=lambda i, ts: setattr(i, "height", max(ts[1], R(18))))


def col(*widgets, **kw):
    b = BoxLayout(orientation="vertical", size_hint_y=None, spacing=kw.pop("spacing", R(4)), **kw)
    b.bind(minimum_height=b.setter("height"))
    for w in widgets:
        b.add_widget(w)
    return b


def row(*widgets, **kw):
    b = BoxLayout(orientation="horizontal", size_hint_y=None, spacing=kw.pop("spacing", R(12)), **kw)
    b.bind(minimum_height=b.setter("height"))
    for w in widgets:
        b.add_widget(w)
    return b


class Disc(FloatLayout):
    """Rond avec un texte au centre, et un anneau de progression facultatif."""

    def __init__(self, text="", fill=None, text_color=None, size=64, font=20, ring=None, ring_color=None, **kw):
        super().__init__(size_hint=(None, None), size=(R(size), R(size)), **kw)
        self.ring, self.fill = ring, fill
        self.ring_color = ring_color or TH["acc"]
        with self.canvas.before:
            self._fc = Color(*(fill or TH["card2"]))
            self._e = Ellipse()
            if ring is not None:
                self._tc = Color(*TH["track"])
                self._tl = Line(width=R(7))
                self._rc = Color(*self.ring_color)
                self._rl = Line(width=R(7), cap="round")
        self.lab = Label(text=text, font_size=fs(font), bold=True, color=text_color or TH["text"], halign="center", valign="middle",
                         size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self.lab.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.lab)
        self.bind(pos=self._u, size=self._u)

    def _u(self, *_):
        x, y = self.pos
        w, h = self.size
        if self.ring is None:
            self._e.pos, self._e.size = self.pos, self.size
        else:  # anneau : le disque est un peu plus petit
            pad = R(11)
            self._e.pos, self._e.size = (x + pad, y + pad), (w - 2 * pad, h - 2 * pad)
            cx, cy, r = x + w / 2, y + h / 2, w / 2 - R(4)
            self._tl.circle = (cx, cy, r)
            self._rl.circle = (cx, cy, r, 0, 360 * max(0.001, min(1, self.ring)))


class PillButton(ButtonBehavior, Bubble):
    """Gros bouton arrondi (zone de toucher confortable)."""

    def __init__(self, text, kind="card", size=16, height=56, on=None, **kw):
        hh = R(height) * (1 + (PREFS["scale"] - 1) * 0.55)
        fill = TH["acc"] if kind == "acc" else TH["card2"]
        super().__init__(fill=fill, radius=height, size_hint_y=None, height=hh, padding=(R(18), 0), **kw)
        self.lab = Label(text=text, font_size=fs(size), bold=True, color=TH["on_acc"] if kind == "acc" else TH["text"],
                         halign="center", valign="middle")
        self.lab.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.lab)
        self.bind(on_press=lambda *_: setattr(self, "opacity", 0.7), on_release=lambda *_: setattr(self, "opacity", 1))
        if on:
            self.bind(on_release=lambda *_: on())

    def set_text(self, t):
        self.lab.text = t


def segmented(options, current, on_pick):
    """Une rangée de gros boutons ronds : un seul est sélectionné."""
    b = BoxLayout(size_hint_y=None, spacing=R(8), height=R(52) * (1 + (PREFS["scale"] - 1) * 0.55))
    for value, label in options:
        b.add_widget(PillButton(label, "acc" if value == current else "card", size=14, height=52, on=lambda v=value: on_pick(v)))
    return b


class BarRow(BoxLayout):
    """Moyenne d'une matière : barre colorée, repère de la moyenne de la classe, et le texte en toutes lettres."""

    def __init__(self, s, **kw):
        super().__init__(orientation="vertical", size_hint_y=None, spacing=R(4), **kw)
        avg, cls = s["avg"], s["class"]
        head = row(Txt(s["name"], 15, bold=True), Txt(fmt(avg) + "/20", 15, "acc", bold=True, halign="right", size_hint_x=None, width=R(78)), spacing=R(6))
        gap = "" if s["gap"] is None else "  |  %s%s vs classe" % ("+" if s["gap"] >= 0 else "-", fmt(abs(s["gap"])))
        trend = {"up": "  |  en hausse", "down": "  |  en baisse", "flat": ""}.get(s["trend"], "")
        sub = Txt("%s%s%s" % (s["level"], gap, trend), 12, "sub")
        bar = Widget(size_hint_y=None, height=R(14))
        with bar.canvas:
            Color(*TH["track"])
            self._t = RoundedRectangle(radius=[R(7)])
            Color(*s["color"])
            self._f = RoundedRectangle(radius=[R(7)])
            Color(*TH["text"])
            self._m = Rectangle()
        def upd(*_):
            x, y = bar.pos
            w, h = bar.size
            self._t.pos, self._t.size = (x, y), (w, h)
            self._f.pos, self._f.size = (x, y), (max(h, w * min(1, (avg or 0) / 20)), h)
            if cls is not None:
                self._m.pos, self._m.size = (x + w * min(1, cls / 20) - R(1.5), y - R(2)), (R(3), h + R(4))
            else:
                self._m.size = (0, 0)
        bar.bind(pos=upd, size=upd)
        for w in (head, sub, bar):
            self.add_widget(w)
        self.bind(minimum_height=self.setter("height"))


class HistCol(BoxLayout):
    def __init__(self, label, count, top, color, **kw):
        super().__init__(orientation="vertical", **kw)
        self.add_widget(Txt(str(count), 13, "text", bold=True, halign="center"))
        area = Widget()
        with area.canvas:
            Color(*color)
            rr = RoundedRectangle(radius=[R(8)])
        def upd(*_):
            h = area.height * (count / top if top else 0)
            rr.pos = (area.x + area.width * 0.18, area.y)
            rr.size = (area.width * 0.64, max(h, R(4) if count else 0))
        area.bind(pos=upd, size=upd)
        self.add_widget(area)
        self.add_widget(Txt(label, 11, "sub", halign="center"))


class Histogram(BoxLayout):
    def __init__(self, bins, **kw):
        super().__init__(size_hint_y=None, height=R(150) * (1 + (PREFS["scale"] - 1) * 0.4), spacing=R(2), **kw)
        top = max(bins) or 1
        for (lo, hi, label), n in zip(BINS, bins):
            self.add_widget(HistCol(label, n, top, level(lo + 0.5)[1]))


class Spark(Widget):
    """Courbe des dernières notes."""

    def __init__(self, vals, **kw):
        super().__init__(size_hint_y=None, height=R(110), **kw)
        self.vals = vals
        with self.canvas:
            Color(*TH["track"])
            self._g = Line(width=R(1))
            Color(*TH["acc"])
            self._l = Line(width=R(3), joint="round", cap="round")
            self._dots = [Ellipse() for _ in vals]
        self.bind(pos=self._u, size=self._u)

    def _u(self, *_):
        n = len(self.vals)
        if n < 2:
            return
        lo, hi = max(0, min(self.vals) - 1), min(20, max(self.vals) + 1)
        span = (hi - lo) or 1
        pad = R(10)
        pts = []
        for i, v in enumerate(self.vals):
            x = self.x + pad + (self.width - 2 * pad) * i / (n - 1)
            y = self.y + pad + (self.height - 2 * pad) * (v - lo) / span
            pts += [x, y]
            self._dots[i].pos, self._dots[i].size = (x - R(5), y - R(5)), (R(10), R(10))
        self._l.points = pts
        self._g.points = [self.x, self.y + R(1), self.right, self.y + R(1)]


# =====================================================================================
#  Barre de navigation « bulle » flottante
# =====================================================================================
class NavIcon(Widget):
    kind = StringProperty("home")
    p = NumericProperty(0)  # 0 = inactif, 1 = actif

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.draw, size=self.draw, p=self.draw, kind=self.draw)

    def draw(self, *_):
        self.canvas.clear()
        s = min(self.width, self.height) * 0.30 * (1 + 0.15 * self.p)
        cx, cy = self.center_x, self.center_y
        acc, sub = TH["acc"], TH["sub"]
        colr = [sub[i] + (acc[i] - sub[i]) * self.p for i in range(3)]
        P = lambda pts: [v for x, y in pts for v in (cx + x * s, cy + y * s)]
        w = max(R(1.8), R(1.8) * PREFS["scale"] * 0.8)
        with self.canvas:
            if self.p > 0:
                Color(acc[0], acc[1], acc[2], 0.22 * self.p)
                r = s * 1.9
                Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
            Color(*colr, 1)
            if self.kind == "home":
                Line(points=P([(-1, 0), (0, .9), (1, 0)]), width=w, joint="round", cap="round")
                Line(points=P([(-.7, 0), (-.7, -.9), (.7, -.9), (.7, 0)]), width=w, joint="round", cap="round")
            elif self.kind == "grades":
                for x, h in ((-.6, -.2), (0, .3), (.6, .85)):
                    Line(points=P([(x, -.9), (x, h)]), width=w * 1.6, cap="round")
            elif self.kind == "stats":
                Line(circle=(cx, cy, .95 * s), width=w)
                Line(points=P([(0, 0), (0, .95)]), width=w, cap="round")
                Line(points=P([(0, 0), (.82, -.47)]), width=w, cap="round")
            elif self.kind == "homework":
                Line(rounded_rectangle=(cx - .8 * s, cy - .9 * s, 1.6 * s, 1.8 * s, R(4)), width=w)
                Line(points=P([(-.4, 0), (-.1, -.3), (.45, .4)]), width=w, joint="round", cap="round")
            else:  # edt
                Line(rounded_rectangle=(cx - .9 * s, cy - .8 * s, 1.8 * s, 1.7 * s, R(4)), width=w)
                Line(points=P([(-.9, .35), (.9, .35)]), width=w)
                Line(points=P([(-.45, .7), (-.45, 1.1)]), width=w, cap="round")
                Line(points=P([(.45, .7), (.45, 1.1)]), width=w, cap="round")


class NavTab(ButtonBehavior, BoxLayout):
    def __init__(self, kind, label, **kw):
        super().__init__(orientation="vertical", padding=(0, R(4)), **kw)
        self.icon = NavIcon(kind=kind)
        self.lbl = Label(text=label, font_size=fs(10.5), bold=True, color=TH["sub"], size_hint_y=None, height=fs(14))
        self.add_widget(self.icon)
        self.add_widget(self.lbl)

    def set_active(self, on):
        if PREFS["anim"]:
            Animation(p=1 if on else 0, d=0.35, t="out_back").start(self.icon)
        else:
            self.icon.p = 1 if on else 0
        self.lbl.color = TH["acc"] if on else TH["sub"]


class Page(Screen):
    """Une page défilante avec ses bulles. Le bas est laissé libre pour la barre flottante."""

    def __init__(self, title, **kw):
        super().__init__(**kw)
        sv = ScrollView(do_scroll_x=False, bar_width=R(3))
        self.box = BoxLayout(orientation="vertical", spacing=R(12), padding=(R(16), R(18), R(16), R(110)), size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        sv.add_widget(self.box)
        self.add_widget(sv)
        self.title = title

    def fill(self, widgets):
        self.box.clear_widgets()
        for w in widgets:
            self.box.add_widget(w)


def deco(widget):
    """Quelques grosses bulles transparentes en fond (pour l'ambiance)."""
    with widget.canvas.before:
        Color(*TH["deco"])
        els = [Ellipse() for _ in range(4)]
    spec = [(0.78, 0.82, 0.55), (-0.12, 0.45, 0.42), (0.62, 0.08, 0.34), (0.10, 0.92, 0.26)]

    def upd(*_):
        for e, (fx, fy, fr) in zip(els, spec):
            d = widget.width * fr
            e.size = (d, d)
            e.pos = (widget.x + widget.width * fx, widget.y + widget.height * fy)
    widget.bind(pos=upd, size=upd)


# =====================================================================================
#  Fenêtre de connexion EduConnect (vraie fenêtre Android : tu te connectes toi-même sur la page EduConnect)
# =====================================================================================
DESKTOP_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class EduFlow:
    """Suit la navigation de la fenêtre : on est « connecté » quand on revient sur Pronote après être passé par EduConnect."""

    def __init__(self, url):
        self.host = urlparse(url).netloc
        self.left = False

    def update(self, current_url, cookie, hosts=()):
        if any(h and h != self.host for h in hosts):  # historique : on est déjà passé par une autre page (EduConnect)
            self.left = True
        host = urlparse(current_url or "").netloc
        if not host:
            return False
        if host != self.host:
            self.left = True
            return False
        return bool(self.left and cookie)


def cookie_jar(cookies, url):
    """Transforme les cookies de la fenêtre en cookies utilisables par pronotepy.
    `cookies` : 'a=1; b=2' (pour le site Pronote) ou {adresse: 'a=1; b=2'} pour plusieurs sites (Pronote, hub EduConnect...)."""
    import requests
    jar = requests.cookies.RequestsCookieJar()
    if not isinstance(cookies, dict):
        cookies = {url: cookies}
    for site, cookie in cookies.items():
        host = urlparse(site).hostname or ""
        for part in (cookie or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                jar.set(k, v, domain=host, path="/")
    return jar


def short_trail(hosts):
    """Parcours résumé pour les messages d'erreur, ex. '0670020h > hubeduconnect > educonnect > 0670020h' (sans aucun cookie)."""
    out = []
    for h in hosts:
        n = h.split(".")[0]
        if not out or out[-1] != n:
            out.append(n)
    return " > ".join(out[:8])


class EduPopup:
    """Ouvre le lien Pronote du lycée dans une fenêtre Android : Pronote renvoie vers la VRAIE page EduConnect,
    l'utilisateur s'y connecte lui-même (l'app ne voit jamais le mot de passe). Bouton Retour = fermer la fenêtre."""

    DEFAULT_SITES = ("https://hubeduconnect.index-education.net/", "https://educonnect.education.gouv.fr/")

    def __init__(self, url, on_done, on_error):
        self.url, self.on_done, self.on_error = url, on_done, on_error
        self.flow = EduFlow(url)
        self.cur_url, self.cookie = "", ""
        self.hosts = [urlparse(url).netloc]  # sites visités pendant la connexion
        self.pages = []  # pages visitées (adresse sans « ? »), pour demander aussi les cookies propres à chaque chemin
        self.ua = ""  # « User-Agent » de la fenêtre
        self.cookies = {}  # {adresse: cookies}
        self.finished = False
        self._step("début")  # étape en cours (pour dire où ça bloque en cas d'erreur)
        self.web = self.cm = self.dlg = None
        self.showing = self.was_showing = False
        self.ticks = 0
        self._ev = None

    def _step(self, name):
        self.step = name
        log("fenêtre : étape %s", name)

    def _ui(self, fn):
        from android.runnable import run_on_ui_thread
        run_on_ui_thread(fn)()

    def open(self):
        try:
            self._step("import android.runnable")
            self._ui(self._build)
            self._ev = Clock.schedule_interval(self._poll, 0.4)
        except Exception as e:
            self._fail("%s : %s : %s" % (self.step, type(e).__name__, str(e)[:100]))

    def _fail(self, detail):
        log("fenêtre : ÉCHEC %s", detail)
        self.finished = True
        if self._ev:
            self._ev.cancel()
        Clock.schedule_once(lambda _: self.on_error("ERR " + detail))

    def _build(self):  # exécuté sur le thread d'interface d'Android
        try:
            self._step("import jnius")
            from jnius import autoclass
            self._step("activité")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            self._step("WebView")
            web = autoclass("android.webkit.WebView")(act)
            st = web.getSettings()
            st.setJavaScriptEnabled(True)
            st.setDomStorageEnabled(True)
            for call, arg in (("setUserAgentString", DESKTOP_UA), ("setUseWideViewPort", True), ("setLoadWithOverviewMode", True),
                              ("setSupportZoom", True), ("setBuiltInZoomControls", True), ("setDisplayZoomControls", False)):
                try:  # page « ordinateur » : la page mobile de Pronote renvoie vers Google Play
                    getattr(st, call)(arg)
                except Exception as e:
                    log("fenêtre : réglage %s impossible (%s)", call, type(e).__name__)
            self.ua = DESKTOP_UA
            web.setWebViewClient(autoclass("android.webkit.WebViewClient")())  # reste dans la fenêtre pendant les redirections
            self._step("cookies")
            cm = autoclass("android.webkit.CookieManager").getInstance()
            cm.removeAllCookies(None)  # connexion « propre » : on refait tout le parcours EduConnect à chaque fois
            cm.setAcceptCookie(True)
            cm.setAcceptThirdPartyCookies(web, True)
            self._step("fenêtre")
            try:
                theme = autoclass("android.R$style").Theme_Black_NoTitleBar_Fullscreen
            except Exception:
                theme = 16973832
            dlg = autoclass("android.app.Dialog")(act, theme)
            dlg.setContentView(web)
            dlg.setCancelable(True)  # le bouton Retour ferme la fenêtre
            self._step("affichage")
            dlg.show()
            web.requestFocus()
            self._step("chargement")
            web.loadUrl(self.url)
            self.web, self.cm, self.dlg = web, cm, dlg
            self._step("ok")
        except Exception as e:
            self._fail("%s : %s : %s" % (self.step, type(e).__name__, str(e)[:100]))

    def _read(self):  # thread d'interface
        try:
            if self.web is None:
                return
            self.showing = bool(self.dlg.isShowing())
            self.was_showing = self.was_showing or self.showing
            self.cur_url = self.web.getUrl() or ""
            self._see(self.cur_url)
            try:  # historique de la fenêtre : attrape aussi les pages passées trop vite pour être vues
                hist = self.web.copyBackForwardList()
                for i in range(hist.getSize()):
                    self._see(hist.getItemAtIndex(i).getUrl())
            except Exception:
                pass
            sites = [self.url] + self.pages + ["https://%s/" % h for h in self.hosts] + list(self.DEFAULT_SITES)
            self.cookies = {u: self.cm.getCookie(u) or "" for u in dict.fromkeys(sites)}
            self.cookie = self.cookies.get(self.url, "")
        except Exception:
            pass

    def _see(self, url):
        p = urlparse(url or "")
        host = p.netloc
        if host and host not in self.hosts:
            self.hosts.append(host)
        page = "%s://%s%s" % (p.scheme, host, p.path) if host else ""
        if page and page not in self.pages:
            self.pages.append(page)
            log("fenêtre : nouvelle page visitée (%s%s)", host, p.path[:60])

    def _poll(self, dt):
        if self.finished:
            return False
        self._ui(self._read)
        self.ticks += 1
        if self.flow.update(self.cur_url, self.cookie, self.hosts):  # retour sur Pronote après EduConnect
            self._finish()
            return False
        if self.was_showing and not self.showing:  # fenêtre fermée avec le bouton Retour
            if any(self.cookies.values()):
                self._finish()  # on tente quand même (ex. connexion directe sans EduConnect)
            else:
                self._cancel()
            return False
        if self.web is not None and not self.was_showing and self.ticks > 25:  # ~10 s sans fenêtre visible
            self._fail("la fenêtre ne s'affiche pas (étape : %s)" % self.step)
            return False

    def _cancel(self):
        log("fenêtre : fermée sans connexion")
        self.finished = True
        self._close()
        Clock.schedule_once(lambda _: self.on_error("Connexion annulée."))

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        cookies = {u: c for u, c in self.cookies.items() if c}
        for c in cookies.values():  # valeurs des cookies : jamais dans le journal
            SECRETS.update(p.partition("=")[2].strip() for p in c.split(";"))
        log("fenêtre : terminée, parcours = %s, sites avec cookies = %d, retour sur Pronote = %s",
            short_trail(self.hosts), len(cookies), self.flow.left)
        for u, c in cookies.items():  # on note les NOMS des cookies (jamais leurs valeurs)
            log("fenêtre : cookies de %s%s : %s", urlparse(u).netloc, urlparse(u).path[:30],
                ", ".join(p.partition("=")[0].strip() for p in c.split(";")))
        log("fenêtre : user-agent %s", (self.ua or "?")[:90])
        self._close()
        self.on_done(self.url, cookies, short_trail(self.hosts), self.ua)

    def _close(self):
        def close():
            try:
                if self.dlg is not None and self.dlg.isShowing():
                    self.dlg.dismiss()
                if self.web is not None:
                    self.web.destroy()
            except Exception:
                pass
        if self._ev:
            self._ev.cancel()
        try:
            self._ui(close)
        except Exception:
            pass


def clear_web_cookies():
    """Efface la session EduConnect gardée par la fenêtre (utilisé à la déconnexion)."""
    try:
        from android.runnable import run_on_ui_thread

        def clear():
            try:
                from jnius import autoclass
                autoclass("android.webkit.CookieManager").getInstance().removeAllCookies(None)
                log("session EduConnect de la fenêtre effacée")
            except Exception as e:
                log("effacement de la session EduConnect impossible (%s)", type(e).__name__)
        run_on_ui_thread(clear)()
    except Exception:
        pass




# =====================================================================================
#  Lien de l'établissement (avec ou sans « ?identifiant=... »)
# =====================================================================================
def clean_link(text):
    t = "".join((text or "").strip().strip("\"'<>").split())
    if not t:
        return ""
    if "://" not in t:
        t = "https://" + t
    return "https://" + t.split("://", 1)[1]


def link_variants(link):
    """Adresses à essayer avec pronotepy (la même page Pronote, écrite de plusieurs façons)."""
    p = urlparse(clean_link(link))
    path = p.path.replace("mobile.eleve.html", "eleve.html").replace("mobile.parent.html", "parent.html")
    if not path.endswith(".html"):
        path = (path.rstrip("/") or "/pronote") + "/eleve.html"
    base = "%s://%s%s" % (p.scheme, p.netloc, path)
    out = [base]
    if p.query:
        out.append(base + "?" + p.query)
    out.append(clean_link(link))
    return list(dict.fromkeys(out))


def desktop_link(link):
    """La page Pronote « ordinateur » (sans mobile. ni ?identifiant=...) : la page mobile renvoie vers Google Play."""
    p = urlparse(clean_link(link))
    path = p.path.replace("mobile.eleve.html", "eleve.html").replace("mobile.parent.html", "parent.html")
    if not path.endswith(".html"):
        path = (path.rstrip("/") or "/pronote") + "/eleve.html"
    return "%s://%s%s" % (p.scheme, p.netloc, path)


def diagnose(jar, url, ua=""):
    """Refait la requête de pronotepy avec les mêmes cookies et décrit ce que Pronote répond (sans cookie, sans ticket)."""
    import re
    import requests
    out = []
    try:
        s = requests.Session()
        s.headers["User-Agent"] = ua or "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:73.0) Gecko/20100101 Firefox/73.0"
        for c in jar:
            s.cookies.set_cookie(c)
        r = s.get(url, timeout=20, allow_redirects=True)
        for h in r.history:
            hp = urlparse(h.url)
            out.append("%d %s%s (cookies reçus : %d)" % (h.status_code, hp.hostname, hp.path[:50], len(h.cookies)))
        fp = urlparse(r.url)
        body = r.text or ""
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        low = body.lower()
        words = [w for w in ("start(", "educonnect", "monbureaunumerique", "mot de passe", "identifiant", "erreur", "refus", "ticket",
                             "expir", "form", "login") if w in low]
        out.append("FINAL %d %s%s | longueur %d | titre : %s | mots : %s" % (
            r.status_code, fp.hostname, fp.path[:50], len(body), (m.group(1).strip()[:60] if m else "-"), ",".join(words) or "-"))
    except Exception as e:
        out.append("diagnostic impossible : %s : %s" % (type(e).__name__, str(e)[:100]))
    return out


def register_link_secrets(link):
    q = parse_qs(urlparse(link).query)
    for v in q.get("identifiant", []):
        SECRETS.add(v)


# =====================================================================================
#  L'application
# =====================================================================================
class PronoteApp(App):
    POPUP_CLS = EduPopup

    # ---------- démarrage ----------
    def build(self):
        self.title = "Pronote"
        self._load_prefs()
        apply_theme()
        self.data = self.stats = self.client = None
        self.busy = False
        self.root_sm = ScreenManager(transition=NoTransition())
        self._build_screens()
        return self.root_sm

    def on_start(self):
        import kivy
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            if os.path.getsize(self._path("app.log")) > 200_000:
                os.remove(self._path("app.log"))
        except OSError:
            pass
        log("=== démarrage : appli %s | Python %s | Kivy %s | plateforme %s", APP_VERSION, sys.version.split()[0], kivy.__version__, platform)
        try:
            from importlib.metadata import version
            log("pronotepy %s", version("pronotepy"))
        except Exception as e:
            log("pronotepy : version inconnue (%s)", type(e).__name__)
        for mod in ("pronotepy", "autoslot", "bs4", "Crypto", "requests", "jnius", "android"):
            try:
                __import__(mod)
                log("module %s : ok", mod)
            except Exception as e:
                log("module %s : MANQUANT (%s)", mod, type(e).__name__)
        if os.path.exists(self._path("saved.json")):  # ancienne version : des mots de passe étaient enregistrés -> on les efface
            try:
                os.remove(self._path("saved.json"))
                log("anciens identifiants enregistrés : supprimés")
            except OSError:
                pass
        if PREFS["link"]:
            register_link_secrets(PREFS["link"])
        creds = self._load_json("creds.json")
        log("réglages : taille %s, thème %s, animations %s | lien enregistré : %s | jeton enregistré : %s",
            PREFS["scale"], PREFS["theme"], PREFS["anim"], bool(PREFS["link"]), bool(creds))
        if creds:
            self.set_status("Reconnexion...")
            self._start("token", creds)

    # ---------- fichiers ----------
    def _path(self, name):
        return os.path.join(self.user_data_dir, name)

    def _load_json(self, name):
        try:
            with open(self._path(name), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_json(self, name, data):
        os.makedirs(self.user_data_dir, exist_ok=True)
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load_prefs(self):
        p = self._load_json("settings.json") or {}
        for k in PREFS:
            if k in p:
                PREFS[k] = p[k]
        if PREFS["theme"] not in THEMES:
            PREFS["theme"] = "dark"
        PREFS["scale"] = min(1.6, max(1.0, float(PREFS["scale"] or 1.0)))

    def _save_prefs(self):
        self._save_json("settings.json", PREFS)

    def _device_uuid(self):
        d = self._load_json("device.json")
        if not d:
            d = {"uuid": uuid.uuid4().hex}
            self._save_json("device.json", d)
        return d["uuid"]

    # ---------- écrans ----------
    def _build_screens(self):
        self.pg = {}
        self.root_sm.add_widget(self._login_screen())
        self.root_sm.add_widget(self._main_screen())
        self.root_sm.add_widget(self._settings_screen())

    def _login_screen(self):
        s = Screen(name="login")
        deco(s)
        sv = ScrollView(do_scroll_x=False)
        box = BoxLayout(orientation="vertical", padding=(R(22), R(36), R(22), R(30)), spacing=R(16), size_hint_y=None)
        box.bind(minimum_height=box.setter("height"))
        logo = Disc("P", fill=TH["acc"], text_color=TH["on_acc"], size=92, font=44)
        logo.pos_hint = {"center_x": .5}
        wrap = FloatLayout(size_hint_y=None, height=R(100))
        logo.pos_hint = {"center_x": .5, "center_y": .5}
        wrap.add_widget(logo)
        box.add_widget(wrap)
        box.add_widget(Txt("Pronote", 32, "text", bold=True, halign="center"))
        box.add_widget(Txt("Tes notes, tes devoirs et tes statistiques, simplement.", 15, "sub", halign="center"))
        # champ du lien
        inp = Bubble(radius=30, size_hint_y=None, height=R(60) * (1 + (PREFS["scale"] - 1) * 0.5), padding=(R(18), 0, R(6), 0), fill=TH["card"])
        self.link_in = TextInput(text=PREFS["link"], hint_text="Colle ici le lien de ton lycée", multiline=False, font_size=fs(15),
                                 background_color=(0, 0, 0, 0), background_normal="", background_active="", foreground_color=TH["text"],
                                 hint_text_color=TH["sub"], cursor_color=TH["acc"], padding=(0, R(18)), input_type="url", write_tab=False)
        inp.add_widget(self.link_in)
        inp.add_widget(PillButton("Coller", "card", size=14, height=44, on=self.paste_link, size_hint_x=None, width=R(92) * PREFS["scale"]))
        box.add_widget(inp)
        self.login_status = Txt("", 14, "sub", halign="center")
        box.add_widget(self.login_status)
        self.btn_connect = PillButton("Se connecter avec EduConnect", "acc", size=17, height=64, on=self.connect)
        box.add_widget(self.btn_connect)
        help_ = Bubble(orientation="vertical", auto=True, padding=R(18), spacing=R(8))
        for t in ("1.  Colle le lien donné par ton lycée.", "2.  Appuie sur le bouton ci-dessus.",
                  "3.  Connecte-toi sur la page EduConnect qui s'ouvre. L'appli ne voit jamais ton mot de passe."):
            help_.add_widget(Txt(t, 14, "text"))
        box.add_widget(help_)
        box.add_widget(row(PillButton("Réglages", "card", size=14, height=48, on=lambda: self.open_settings()),
                           PillButton("Journal", "card", size=14, height=48, on=self.show_log), spacing=R(10)))
        sv.add_widget(box)
        s.add_widget(sv)
        return s

    def _main_screen(self):
        s = Screen(name="main")
        fl = FloatLayout()
        deco(fl)
        self.pages_sm = ScreenManager(transition=FadeTransition(duration=0.15) if PREFS["anim"] else NoTransition())
        titles = (("home", "Accueil"), ("grades", "Notes"), ("stats", "Stats"), ("homework", "Devoirs"), ("edt", "Cours"))
        for k, t in titles:
            self.pg[k] = Page(t, name=k)
            self.pages_sm.add_widget(self.pg[k])
        fl.add_widget(self.pages_sm)
        n = len(titles)
        nav = BoxLayout(size_hint=(None, None), width=min(Window.width - R(20), R(520)), height=R(66) * (1 + (PREFS["scale"] - 1) * 0.55),
                        pos_hint={"center_x": .5, "y": 0.012}, padding=(R(8), R(4)))
        with nav.canvas.before:
            Color(*TH["card2"])
            rr = RoundedRectangle(pos=nav.pos, size=nav.size, radius=[nav.height / 2])
        nav.bind(pos=lambda *_: setattr(rr, "pos", nav.pos), size=lambda *_: (setattr(rr, "size", nav.size), setattr(rr, "radius", [nav.height / 2])))
        self.tabs = {}
        for k, t in titles:
            tab = NavTab(k if k != "grades" else "grades", t)
            tab.bind(on_release=lambda _, k=k: self.goto(k))
            self.tabs[k] = tab
            nav.add_widget(tab)
        fl.add_widget(nav)
        s.add_widget(fl)
        self.tabs["home"].set_active(True)
        return s

    def _settings_screen(self):
        s = Screen(name="settings")
        deco(s)
        sv = ScrollView(do_scroll_x=False)
        box = BoxLayout(orientation="vertical", padding=(R(18), R(24), R(18), R(40)), spacing=R(14), size_hint_y=None)
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(PillButton("Retour", "card", size=15, height=50, on=self.close_settings))
        box.add_widget(Txt("Réglages", 28, "text", bold=True))

        def section(title, widget):
            b = Bubble(orientation="vertical", auto=True, padding=R(16), spacing=R(10))
            b.add_widget(Txt(title, 16, "text", bold=True))
            b.add_widget(widget)
            box.add_widget(b)
        section("Taille du texte", segmented([(1.0, "Normal"), (1.25, "Grand"), (1.5, "Très grand")], PREFS["scale"], lambda v: self.set_pref("scale", v)))
        section("Thème", segmented([("dark", "Sombre"), ("light", "Clair"), ("contrast", "Contraste")], PREFS["theme"], lambda v: self.set_pref("theme", v)))
        section("Animations", segmented([(True, "Oui"), (False, "Non")], PREFS["anim"], lambda v: self.set_pref("anim", v)))
        acts = Bubble(orientation="vertical", auto=True, padding=R(16), spacing=R(10))
        acts.add_widget(Txt("Compte", 16, "text", bold=True))
        if self.data:
            acts.add_widget(PillButton("Actualiser les données", "card", size=15, height=52, on=lambda: (self.close_settings(), self.refresh())))
        acts.add_widget(PillButton("Journal (aide au dépannage)", "card", size=15, height=52, on=self.show_log))
        if self.data or self._load_json("creds.json"):
            acts.add_widget(PillButton("Se déconnecter", "card", size=15, height=52, on=self.logout))
        box.add_widget(acts)
        box.add_widget(Txt("Version %s" % APP_VERSION, 12, "sub", halign="center"))
        sv.add_widget(box)
        s.add_widget(sv)
        return s

    def open_settings(self):
        self._back_to = self.root_sm.current
        self.root_sm.current = "settings"

    def close_settings(self):
        self.root_sm.current = getattr(self, "_back_to", "login") if (self.data or getattr(self, "_back_to", "") == "login") else "login"
        if self.root_sm.current == "main":
            self.goto(self.pages_sm.current)

    def set_pref(self, key, value):
        PREFS[key] = value
        self._save_prefs()
        log("réglage : %s = %s", key, value)
        Clock.schedule_once(lambda _: self.rebuild("settings"), 0)

    def rebuild(self, stay):
        """Reconstruit l'interface (nouvelle taille de texte, nouveau thème...) sans perdre les données."""
        page = self.pages_sm.current if hasattr(self, "pages_sm") else "home"
        link = self.link_in.text if hasattr(self, "link_in") else PREFS["link"]
        apply_theme()
        self.root_sm.transition = NoTransition()
        self.root_sm.clear_widgets()
        self._build_screens()
        self.link_in.text = link
        self.root_sm.current = stay
        self.populate()
        if stay == "main":
            self.goto(page)

    def goto(self, key):
        self.pages_sm.current = key
        for k, t in self.tabs.items():
            t.set_active(k == key)

    def set_status(self, text):
        for attr in ("login_status",):
            w = getattr(self, attr, None)
            if w is not None:
                w.text = text

    # ---------- connexion EduConnect ----------
    def paste_link(self, *_):
        try:
            t = Clipboard.paste() or ""
        except Exception:
            t = ""
        if t.strip():
            self.link_in.text = t.strip()
            self.set_status("Lien collé.")
        else:
            self.set_status("Rien à coller : copie d'abord le lien de ton lycée.")

    def connect(self, *_):
        link = clean_link(self.link_in.text)
        host = urlparse(link).netloc
        log("bouton « Se connecter avec EduConnect » (hôte : %s)", host or "-")
        if not host or "." not in host:
            self.set_status("Colle d'abord le lien de ton lycée (il commence par https://).")
            return
        if "parent.html" in link:
            self.set_status("Ce lien est celui d'un compte parent : l'appli gère seulement les comptes élèves.")
            return
        register_link_secrets(link)
        PREFS["link"] = link
        self._save_prefs()
        self.set_status("Ouverture de la page EduConnect...")
        try:
            web_link = desktop_link(link)
            log("la fenêtre ouvre la page ordinateur de Pronote (hôte : %s)", urlparse(web_link).netloc)
            self.popup = self.POPUP_CLS(web_link, lambda u, c, tr="", ua="": self._popup_done(link, c, tr, ua), self._popup_error)
            self.popup.open()
        except Exception as e:
            self._popup_error("ERR %s" % e)

    def _popup_error(self, msg):
        log("fenêtre EduConnect : erreur (%s)", msg[:200])
        if "annulée" in msg:
            self.set_status("Connexion annulée.")
        else:
            self.set_status("La page EduConnect n'a pas pu s'ouvrir. Ouvre le Journal, copie-le et envoie-le moi. [%s]" % msg.replace("ERR ", "")[:150])

    def _popup_done(self, url, cookies, trail="", ua=""):
        if not any(cookies.values() if isinstance(cookies, dict) else [cookies]):
            self.set_status("Aucune connexion détectée. Réessaie. [%s]" % trail)
            return
        self.set_status("Connexion à Pronote...")
        self._start("web", {"link": url, "cookies": cookies, "trail": trail, "ua": ua})

    def _start(self, kind, p):
        self.busy = True
        threading.Thread(target=self._work, args=(kind, p), daemon=True).start()

    @staticmethod
    def _friendly(e):
        s = str(e)
        if "Page html is different" in s:
            return "Pronote n'a pas accepté la connexion faite sur la page EduConnect."
        if "IP address is suspended" in s:
            return "Pronote a bloqué ton adresse IP un moment. Réessaie plus tard."
        if "MFA" in type(e).__name__:
            return "Ton compte demande une double authentification (pas gérée)."
        if "Connection" in type(e).__name__ or "Max retries" in s:
            return "Pas de connexion internet, ou lien Pronote incorrect."
        return s[:200] or type(e).__name__

    def _work(self, kind, p):
        log("connexion : début (type : %s)", kind)
        try:
            import pronotepy
            c = None
            if kind == "web":
                import pronotepy.pronoteAPI as pa
                host = urlparse(p["link"]).hostname
                jar_all = cookie_jar(p["cookies"], p["link"])
                jar_cas = cookie_jar({u: ck for u, ck in p["cookies"].items() if urlparse(u).hostname != host}, p["link"])
                variants = link_variants(p["link"])
                ua = p.get("ua") or ""
                default_ua = pa.HEADERS["User-Agent"]
                # (cookies, user-agent, adresse) : on essaie plusieurs combinaisons, chacune est notée dans le journal
                tries = [("tous les cookies", jar_all, default_ua, variants[0]), ("tous les cookies + UA de la fenêtre", jar_all, ua, variants[0]),
                         ("cookies EduConnect seulement", jar_cas, default_ua, variants[0]),
                         ("tous les cookies, lien complet", jar_all, default_ua, variants[-1])]
                if len(variants) > 2:
                    tries.append(("tous les cookies, avec ?identifiant", jar_all, default_ua, variants[1]))
                last = None
                for i, (label, jar, agent, v) in enumerate(tries):
                    if not agent:
                        continue
                    try:
                        pa.HEADERS["User-Agent"] = agent
                        c = pronotepy.Client(v, username="webview", password="webview", ent=lambda *a, jar=jar, **k: jar)
                        log("connexion : essai n°%d accepté par Pronote (%s)", i + 1, label)
                        break
                    except Exception as e:
                        last = e
                        log("connexion : essai n°%d refusé (%s) : %s", i + 1, label, type(e).__name__)
                    finally:
                        pa.HEADERS["User-Agent"] = default_ua
                if c is None:
                    for line in diagnose(jar_all, variants[0], ua):  # ce que Pronote répond vraiment avec nos cookies
                        log("diagnostic : %s", line)
                    raise last
                try:  # on échange la session contre un jeton : prochaines connexions sans refaire EduConnect
                    pin = "".join(random.choice("123456789") for _ in range(4))
                    c = pronotepy.Client.qrcode_login(c.request_qr_code_data(pin), pin, self._device_uuid())
                    log("jeton de reconnexion créé")
                except Exception as e:
                    log("jeton de reconnexion impossible (%s : %s)", type(e).__name__, str(e)[:80])
            elif kind == "token":
                c = pronotepy.Client.token_login(**p)
            else:  # refresh
                c = self.client
                try:
                    d = build_data(c)
                    if not d["name"]:
                        raise RuntimeError("session expirée")
                    log("actualisation : réussie")
                    Clock.schedule_once(lambda _: self._done(d, c))
                    return
                except Exception as e:
                    log("actualisation : la session n'est plus valable (%s), je tente le jeton", type(e).__name__)
                    creds = self._load_json("creds.json")
                    if not creds:
                        raise RuntimeError("Session expirée : reconnecte-toi avec EduConnect.")
                    c = pronotepy.Client.token_login(**creds)
            if not c.logged_in:
                raise RuntimeError("Connexion refusée")
            try:
                creds_new = c.export_credentials()
                SECRETS.add(str(creds_new.get("password", "")))
                self._save_json("creds.json", creds_new)  # le jeton change à chaque connexion
            except Exception as e:
                log("jeton non enregistré (%s)", type(e).__name__)
            d = build_data(c)
            log("connexion : RÉUSSIE (%s) | notes : %d, devoirs : %d, cours : %d, avertissements : %s", kind,
                len(d["grades"]), len(d["homework"]), len(d["lessons"]), d["warnings"] or "aucun")
            Clock.schedule_once(lambda _: self._done(d, c))
        except Exception as e:
            frames = " > ".join("%s:%s" % (os.path.basename(f.filename), f.lineno) for f in traceback.extract_tb(e.__traceback__)[-4:])
            log("connexion : ÉCHEC (%s) | %s : %s | %s", kind, type(e).__name__, str(e)[:200], frames)
            msg = self._friendly(e)
            if kind == "web":
                msg += " Réessaie ; si ça recommence, ouvre le Journal, copie-le et envoie-le moi. [%s]" % p.get("trail", "")
            if kind == "token":
                msg = "Ta session a expiré : appuie sur « Se connecter avec EduConnect »."
                try:
                    os.remove(self._path("creds.json"))
                except OSError:
                    pass
            if kind == "refresh":
                Clock.schedule_once(lambda _: self._refresh_failed(msg))
            else:
                Clock.schedule_once(lambda _: self.set_status(msg))
        finally:
            self.busy = False

    def _done(self, d, c=None):
        self.data, self.stats = d, compute_stats(d)
        if c is not None:
            self.client = c
        self.set_status("")
        self.populate()
        self.root_sm.current = "main"
        self.goto(self.pages_sm.current)

    def _refresh_failed(self, msg):
        self.home_msg.text = msg
        log("actualisation : échec (%s)", msg[:120])

    def refresh(self):
        if self.busy or not self.data:
            return
        if hasattr(self, "home_msg"):
            self.home_msg.text = "Actualisation..."
        self._start("refresh", {})

    def logout(self):
        log("déconnexion")
        try:
            os.remove(self._path("creds.json"))
        except OSError:
            pass
        clear_web_cookies()
        self.data = self.stats = self.client = None
        self.rebuild("login")
        self.set_status("Tu es déconnecté.")

    # ---------- contenu des pages ----------
    def populate(self):
        if not self.data:
            return
        d, s = self.data, self.stats
        self.pg["home"].fill(self._home(d, s))
        self.pg["grades"].fill(self._grades(d, s))
        self.pg["stats"].fill(self._stats(d, s))
        self.pg["homework"].fill(self._homework(d, s))
        self.pg["edt"].fill(self._edt(d, s))

    def _title(self, t, sub=""):
        w = [Txt(t, 28, "text", bold=True)]
        if sub:
            w.append(Txt(sub, 13, "sub"))
        return w

    def _tile(self, value, caption):
        b = Bubble(orientation="vertical", auto=True, padding=R(14), spacing=R(2))
        b.add_widget(Txt(value, 24, "acc", bold=True, halign="center"))
        b.add_widget(Txt(caption, 12, "sub", halign="center"))
        return b

    def _grid(self, tiles):
        g = GridLayout(cols=2, spacing=R(10), size_hint_y=None)
        g.bind(minimum_height=g.setter("height"))
        for t in tiles:
            g.add_widget(t)
        return g

    def _home(self, d, s):
        out = self._title("Salut %s" % first_name(d["name"]),
                          " • ".join(x for x in (d["klass"], d["period"], "mis à jour à %s" % d["updated"]) if x))
        ov = s["overall"]
        label, color = s["level"]
        gap = s["gap"]
        info = [Txt("Moyenne générale", 16, "text", bold=True), Txt(label, 15, "text")]
        if s["class_overall"] is not None:
            info.append(Txt("Classe : %s%s" % (fmt(s["class_overall"]), "" if gap is None else "  (%s%s)" % ("+" if gap >= 0 else "-", fmt(abs(gap)))), 13, "sub"))
        if s["trend"]:
            info.append(Txt({"up": "En hausse : tes dernières notes montent", "down": "En baisse : tes dernières notes baissent", "flat": "Stable : tes notes ne bougent pas beaucoup"}[s["trend"]], 13, "sub"))
        big = Bubble(auto=True, padding=R(18), spacing=R(16), orientation="horizontal")
        big.add_widget(Disc(fmt(ov), fill=TH["card2"], size=132, font=30, ring=(ov or 0) / 20, ring_color=color))
        big.add_widget(col(*info, spacing=R(4)))
        out.append(big)
        tiles = [self._tile(str(s["n_grades"]), "notes"), self._tile(str(s["hw_todo"]), "devoirs à faire"),
                 self._tile(str(s["lessons_today"]), "cours aujourd'hui"),
                 self._tile(s["best_subject"]["name"] if s["best_subject"] else "—", "meilleure matière")]
        out.append(self._grid(tiles))
        nl = s["next_lesson"]
        if nl:
            b = Bubble(orientation="vertical", auto=True, padding=R(16))
            b.add_widget(Txt("Prochain cours", 13, "sub"))
            b.add_widget(Txt("%s  •  %s–%s" % (nl["subject"], nl["start"], nl["end"]), 17, "text", bold=True))
            if nl["room"]:
                b.add_widget(Txt("Salle %s" % nl["room"], 13, "sub"))
            out.append(b)
        nh = s["next_hw"]
        if nh:
            b = Bubble(orientation="vertical", auto=True, padding=R(16))
            b.add_widget(Txt("Prochain devoir à faire", 13, "sub"))
            b.add_widget(Txt("%s  •  pour %s" % (nh["subject"], day_label(nh["date"])), 16, "text", bold=True))
            b.add_widget(Txt(nh["text"][:160], 13, "text"))
            out.append(b)
        self.home_msg = Txt("", 13, "sub", halign="center")
        out.append(self.home_msg)
        out.append(row(PillButton("Actualiser", "acc", size=15, height=52, on=self.refresh),
                       PillButton("Réglages", "card", size=15, height=52, on=self.open_settings), spacing=R(10)))
        return out

    def _grades(self, d, s):
        out = self._title("Notes", "%d notes • %s" % (s["n_grades"], d["period"]))
        gl = sorted(d["grades"], key=lambda g: g["date"], reverse=True)[:80]
        if not gl:
            out.append(Txt("Aucune note pour le moment.", 15, "sub"))
        for g in gl:
            v = g["v"]
            lab, colr = level(v)
            disc = Disc(fmt(v) if v is not None else g["text"][:4], fill=colr if v is not None else TH["track"],
                        text_color=best_text(colr) if v is not None else TH["text"], size=62, font=17)
            more = []
            if g["out_of"] not in ("20", "20,00"):
                more.append("note : %s/%s" % (g["text"], g["out_of"]))
            if abs((g["coef"] or 1) - 1) > 1e-9:
                more.append("coef %s" % fmt(g["coef"], 1).replace(",0", ""))
            line2 = [datetime.strptime(g["date"], "%Y-%m-%d").strftime("%d/%m")] + more
            line3 = lab
            if g["class_v"] is not None and v is not None:
                diff = v - g["class_v"]
                line3 += "  |  classe %s (%s%s)" % (fmt(g["class_v"]), "+" if diff >= 0 else "-", fmt(abs(diff)))
            b = Bubble(auto=True, padding=R(12), spacing=R(12), orientation="horizontal")
            b.add_widget(disc)
            b.add_widget(col(Txt(g["subject"], 15, "text", bold=True), Txt("  •  ".join(line2), 12, "sub"), Txt(line3, 12, "sub")))
            out.append(b)
        return out

    def _stats(self, d, s):
        out = self._title("Statistiques", d["period"])
        gap = s["gap"]
        tiles = [self._tile(fmt(s["overall"]), "moyenne générale"), self._tile(fmt(s["class_overall"]), "moyenne de la classe")]
        if s["best"]:
            tiles += [self._tile("%s" % fmt(s["best"]["v"]), "meilleure note (%s)" % s["best"]["subject"]),
                      self._tile("%s" % fmt(s["worst"]["v"]), "note la plus basse (%s)" % s["worst"]["subject"])]
        out.append(self._grid(tiles))
        if gap is not None:
            out.append(Txt("Tu es %s la moyenne de la classe de %s point(s)." % ("au-dessus de" if gap >= 0 else "en dessous de", fmt(abs(gap))), 14, "text"))
        subs = [x for x in s["subjects"] if x["avg"] is not None]
        if subs:
            out.append(Txt("Tes matières", 20, "text", bold=True))
            grid = GridLayout(cols=3, spacing=R(10), size_hint_y=None)
            grid.bind(minimum_height=grid.setter("height"))
            for x in subs:
                cell = col(Txt(x["name"], 12, "text", halign="center"), spacing=R(4))
                dd = Disc(fmt(x["avg"]), fill=x["color"], text_color=best_text(x["color"]), size=min(70 + 6 * x["n"], 96), font=17)
                holder = FloatLayout(size_hint_y=None, height=R(100))
                dd.pos_hint = {"center_x": .5, "center_y": .5}
                holder.add_widget(dd)
                cell.add_widget(holder)
                cell.add_widget(Txt(x["level"], 11, "sub", halign="center"))
                grid.add_widget(cell)
            out.append(grid)
            out.append(Txt("Moyenne par matière (le trait = la classe)", 18, "text", bold=True))
            bars = Bubble(orientation="vertical", auto=True, padding=R(16), spacing=R(14))
            for x in subs:
                bars.add_widget(BarRow(x))
            out.append(bars)
        if s["n_grades"]:
            out.append(Txt("Répartition de tes notes", 20, "text", bold=True))
            hb = Bubble(orientation="vertical", auto=True, padding=R(14))
            hb.add_widget(Histogram(s["bins"]))
            out.append(hb)
            if len(s["recent"]) >= 2:
                out.append(Txt("Évolution de tes dernières notes", 20, "text", bold=True))
                sb = Bubble(orientation="vertical", auto=True, padding=R(14))
                sb.add_widget(Spark(s["recent"]))
                tr = {"up": "Ça monte ces derniers temps.", "down": "Ça baisse un peu ces derniers temps.", "flat": "C'est stable."}.get(s["trend"], "")
                if tr:
                    sb.add_widget(Txt(tr, 13, "sub"))
                out.append(sb)
        out.append(Txt("Vie scolaire", 20, "text", bold=True))
        out.append(self._grid([self._tile(str(s["absences"]), "jour(s) d'absence"), self._tile(str(s["delays"]), "retard(s)"),
                               self._tile(str(s["punishments"]), "punition(s)"), self._tile("%d/%d" % (s["hw_total"] - s["hw_todo"], s["hw_total"]), "devoirs faits")]))
        return out

    def _homework(self, d, s):
        out = self._title("Devoirs", "%d à faire sur %d pour les 2 prochaines semaines" % (s["hw_todo"], s["hw_total"]))
        if not d["homework"]:
            out.append(Txt("Aucun devoir pour le moment.", 15, "sub"))
        last = None
        for h in d["homework"]:
            if h["date"] != last:
                last = h["date"]
                out.append(Txt(day_label(h["date"]), 17, "acc", bold=True))
            b = Bubble(orientation="vertical", auto=True, padding=R(14), spacing=R(4))
            state = "Fait" if h["done"] else "À faire"
            b.add_widget(Txt("%s  —  %s" % (h["subject"], state), 15, "text", bold=True))
            b.add_widget(Txt(h["text"] or "(pas de détail)", 13, "sub" if h["done"] else "text"))
            out.append(b)
        return out

    def _edt(self, d, s):
        out = self._title("Emploi du temps", "les 5 prochains jours")
        if not d["lessons"]:
            out.append(Txt("Aucun cours trouvé.", 15, "sub"))
        last = None
        for l in d["lessons"]:
            if l["day"] != last:
                last = l["day"]
                out.append(Txt(day_label(l["day"]), 17, "acc", bold=True))
            b = Bubble(auto=True, padding=R(12), spacing=R(12), orientation="horizontal")
            b.add_widget(Txt("%s\n%s" % (l["start"], l["end"]), 14, "text", bold=True, size_hint_x=None, width=R(64) * PREFS["scale"]))
            tags = []
            if l["canceled"]:
                tags.append("ANNULÉ")
            if l["test"]:
                tags.append("ÉVALUATION")
            info = [Txt(l["subject"], 15, "sub" if l["canceled"] else "text", bold=True)]
            line = "  •  ".join(x for x in ((("Salle " + l["room"]) if l["room"] else ""), l["teacher"]) if x)
            if line:
                info.append(Txt(line, 12, "sub"))
            if tags:
                info.append(Txt(" — ".join(tags), 12, "acc", bold=True))
            b.add_widget(col(*info))
            out.append(b)
        return out

    # ---------- journal ----------
    def show_log(self, *_):
        text = "\n".join(LOG_LINES[-150:]) or "(journal vide)"
        box = BoxLayout(orientation="vertical", spacing=R(8), padding=R(8))
        sv = ScrollView()
        lab = Label(text=text, font_size=sp(10), color=TH["text"], halign="left", valign="top", size_hint_y=None)
        lab.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        lab.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        sv.add_widget(lab)
        box.add_widget(sv)
        rw = BoxLayout(size_hint_y=None, height=R(52), spacing=R(8))
        pop = Popup(title="Journal (copie-le et envoie-le moi)", content=box, size_hint=(0.97, 0.92), background="",
                    background_color=TH["card"], title_color=TH["text"], separator_color=TH["acc"])
        copy_btn = PillButton("Copier le journal", "acc", size=14, height=48)

        def do_copy(*_):
            Clipboard.copy(("Journal Pronote app - version %s\n" % APP_VERSION) + "\n".join(LOG_LINES))
            copy_btn.set_text("Copié !")

        def do_clear(*_):
            del LOG_LINES[:]
            lab.text = "(journal vide)"
        copy_btn.bind(on_release=do_copy)
        rw.add_widget(copy_btn)
        rw.add_widget(PillButton("Effacer", "card", size=14, height=48, on=lambda: do_clear()))
        rw.add_widget(PillButton("Fermer", "card", size=14, height=48, on=pop.dismiss))
        box.add_widget(rw)
        pop.open()


if __name__ == "__main__":
    PronoteApp().run()
