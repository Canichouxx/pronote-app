import threading
from datetime import date

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

BG = (0.05, 0.05, 0.07, 1)
CARD = (0.10, 0.10, 0.14, 1)
ACCENT = (0.47, 0.42, 1.0)
GREY = (0.45, 0.45, 0.52)
TEXT = (0.93, 0.93, 0.96, 1)
Window.clearcolor = BG


# ---------- Icônes animées (dessinées en vecteur, pas de police) ----------
class NavIcon(Widget):
    kind = StringProperty("home")
    p = NumericProperty(0)  # 0 = inactif, 1 = actif

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.draw, size=self.draw, p=self.draw, kind=self.draw)

    def draw(self, *_):
        self.canvas.clear()
        s = min(self.width, self.height) * 0.42 * (1 + 0.18 * self.p)
        cx, cy = self.center_x, self.center_y + dp(3) * self.p
        col = [GREY[i] + (ACCENT[i] - GREY[i]) * self.p for i in range(3)]

        def P(pts):
            return [v for x, y in pts for v in (cx + x * s, cy + y * s)]

        w = dp(1.8)
        with self.canvas:
            Color(*col, 1)
            if self.kind == "home":
                Line(points=P([(-1, 0), (0, 0.9), (1, 0)]), width=w, joint="round", cap="round")
                Line(points=P([(-0.7, 0), (-0.7, -0.9), (0.7, -0.9), (0.7, 0)]), width=w, joint="round", cap="round")
            elif self.kind == "grades":
                for x, h in ((-0.6, -0.2), (0, 0.3), (0.6, 0.85)):
                    Line(points=P([(x, -0.9), (x, h)]), width=w * 1.6, cap="round")
            elif self.kind == "homework":
                Line(rounded_rectangle=(cx - 0.8 * s, cy - 0.9 * s, 1.6 * s, 1.8 * s, dp(4)), width=w)
                Line(points=P([(-0.4, 0), (-0.1, -0.3), (0.45, 0.4)]), width=w, joint="round", cap="round")
            else:  # edt
                Line(rounded_rectangle=(cx - 0.9 * s, cy - 0.8 * s, 1.8 * s, 1.7 * s, dp(4)), width=w)
                Line(points=P([(-0.9, 0.35), (0.9, 0.35)]), width=w)
                Line(points=P([(-0.45, 0.7), (-0.45, 1.1)]), width=w, cap="round")
                Line(points=P([(0.45, 0.7), (0.45, 1.1)]), width=w, cap="round")


class NavTab(ButtonBehavior, BoxLayout):
    def __init__(self, kind, label, **kw):
        super().__init__(orientation="vertical", padding=(0, dp(6)), **kw)
        self.icon = NavIcon(kind=kind)
        self.lbl = Label(text=label, font_size=sp(11), color=GREY + (1,), size_hint_y=None, height=dp(16))
        self.add_widget(self.icon)
        self.add_widget(self.lbl)

    def set_active(self, on):
        Animation(p=1 if on else 0, d=0.35, t="out_back").start(self.icon)
        Animation(color=(ACCENT if on else GREY) + (1,), d=0.25).start(self.lbl)


# ---------- Cartes & écrans ----------
class Card(BoxLayout):
    def __init__(self, title, sub="", right="", **kw):
        super().__init__(size_hint_y=None, height=dp(66), padding=dp(14), spacing=dp(8), **kw)
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(16)])
        self.bind(pos=lambda *_: setattr(self._bg, "pos", self.pos),
                  size=lambda *_: setattr(self._bg, "size", self.size))
        left = BoxLayout(orientation="vertical")
        for txt, size, col in ((title, 15, TEXT), (sub, 12, GREY + (1,))):
            lb = Label(text=txt, font_size=sp(size), color=col, halign="left", valign="middle")
            lb.bind(size=lambda i, v: setattr(i, "text_size", v))
            left.add_widget(lb)
        self.add_widget(left)
        self.add_widget(Label(text=right, font_size=sp(17), bold=True, color=ACCENT + (1,),
                              size_hint_x=None, width=dp(70)))


class ListScreen(Screen):
    def __init__(self, title, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        root.add_widget(Label(text=title, font_size=sp(26), bold=True, color=TEXT,
                              size_hint_y=None, height=dp(48), halign="left",
                              text_size=(Window.width - dp(32), None)))
        sv = ScrollView()
        self.box = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        sv.add_widget(self.box)
        root.add_widget(sv)
        self.add_widget(root)

    def set_items(self, items):
        self.box.clear_widgets()
        for t, s, r in items or [("Rien à afficher", "", "")]:
            self.box.add_widget(Card(t, s, r))


class HomeScreen(ListScreen):
    pass


# ---------- App ----------
class PronoteApp(App):
    def build(self):
        self.title = "Pronote"
        self.root_sm = ScreenManager(transition=FadeTransition(duration=0.2))
        self.root_sm.add_widget(self._login())
        self.root_sm.add_widget(self._main())
        return self.root_sm

    def _login(self):
        s = Screen(name="login")
        box = BoxLayout(orientation="vertical", padding=dp(28), spacing=dp(14))
        box.add_widget(Label(text="Pronote", font_size=sp(36), bold=True, color=ACCENT + (1,)))
        kw = dict(multiline=False, size_hint_y=None, height=dp(48), background_color=CARD,
                  foreground_color=TEXT, cursor_color=ACCENT + (1,), padding=(dp(14), dp(13)))
        self.url = TextInput(hint_text="Lien Pronote du lycée", **kw)
        self.ent = Spinner(text="Aucun ENT (connexion directe)", values=self._ent_list(),
                           size_hint_y=None, height=dp(48), background_normal="",
                           background_color=CARD, color=TEXT)
        self.user = TextInput(hint_text="Identifiant", **kw)
        self.pwd = TextInput(hint_text="Mot de passe", password=True, **kw)
        self.status = Label(text="", color=GREY + (1,), size_hint_y=None, height=dp(24), font_size=sp(12))
        btn = Button(text="Se connecter", size_hint_y=None, height=dp(50), background_normal="",
                     background_color=ACCENT + (1,), bold=True)
        btn.bind(on_release=self.connect)
        for w in (self.url, self.ent, self.user, self.pwd, self.status, btn):
            box.add_widget(w)
        box.add_widget(Widget())
        s.add_widget(box)
        return s

    def _main(self):
        s = Screen(name="main")
        col = BoxLayout(orientation="vertical")
        self.sm = ScreenManager(transition=FadeTransition(duration=0.15))
        self.pages = {
            "home": HomeScreen("Accueil", name="home"),
            "grades": ListScreen("Notes", name="grades"),
            "homework": ListScreen("Devoirs", name="homework"),
            "edt": ListScreen("Emploi du temps", name="edt"),
        }
        for p in self.pages.values():
            self.sm.add_widget(p)
        col.add_widget(self.sm)
        bar = BoxLayout(size_hint_y=None, height=dp(64))
        self.tabs = {}
        for k, lab in (("home", "Accueil"), ("grades", "Notes"), ("homework", "Devoirs"), ("edt", "EDT")):
            t = NavTab(k, lab)
            t.bind(on_release=lambda _, k=k: self.goto(k))
            self.tabs[k] = t
            bar.add_widget(t)
        col.add_widget(bar)
        s.add_widget(col)
        self.tabs["home"].set_active(True)
        return s

    def goto(self, key):
        self.sm.current = key
        for k, t in self.tabs.items():
            t.set_active(k == key)

    NO_ENT = "Aucun ENT (connexion directe)"

    def _ent_list(self):
        # La liste des ENT vient directement de pronotepy (toujours à jour)
        try:
            import pronotepy.ent as ent
            names = sorted(n for n in dir(ent) if not n.startswith("_") and callable(getattr(ent, n)))
        except Exception:
            names = []
        return [self.NO_ENT] + names

    @staticmethod
    def _clean_url(url):
        # Garde seulement .../pronote/eleve.html (enlève ?login=true, etc.)
        url = url.split("?")[0].strip()
        if "eleve.html" not in url and "parent.html" not in url:
            url = url.rstrip("/") + "/eleve.html"
        return url

    def connect(self, *_):
        self.status.text = "Connexion..."
        threading.Thread(target=self._work, args=(self._clean_url(self.url.text), self.user.text.strip(),
                                                  self.pwd.text, self.ent.text), daemon=True).start()

    def _work(self, url, user, pwd, ent_name):
        try:
            import pronotepy
            import pronotepy.ent as ent_mod
            ent = None if ent_name == self.NO_ENT else getattr(ent_mod, ent_name)
            c = pronotepy.Client(url, username=user, password=pwd, ent=ent)
            if not c.logged_in:
                raise Exception("Identifiants refusés")
            grades = [(g.subject.name, g.date.strftime("%d/%m"), f"{g.grade}/{g.out_of}")
                      for g in c.current_period.grades]
            hw = [(h.subject.name, h.description[:80], h.date.strftime("%d/%m"))
                  for h in c.homework(date.today())]
            edt = [(l.subject.name if l.subject else "Cours", l.room or "", l.start.strftime("%H:%M"))
                   for l in c.lessons(date.today())]
            data = (c.info.name, grades, hw, edt)
            Clock.schedule_once(lambda _: self._done(data))
        except Exception as e:
            msg = f"Erreur : {e}"[:80]
            Clock.schedule_once(lambda _: setattr(self.status, "text", msg))

    def _done(self, data):
        name, grades, hw, edt = data
        self.pages["home"].set_items([(f"Salut {name}", "Connecté à Pronote", "")])
        self.pages["grades"].set_items(grades)
        self.pages["homework"].set_items(hw)
        self.pages["edt"].set_items(edt)
        self.root_sm.current = "main"


if __name__ == "__main__":
    PronoteApp().run()
