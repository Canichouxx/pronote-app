import json
import os
import random
import threading
import uuid
from datetime import date
from urllib.parse import urlparse

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
from kivy.utils import platform

BG = (0.05, 0.05, 0.07, 1)
CARD = (0.10, 0.10, 0.14, 1)
ACCENT = (0.47, 0.42, 1.0)
GREY = (0.45, 0.45, 0.52)
TEXT = (0.93, 0.93, 0.96, 1)
Window.clearcolor = BG
Window.softinput_mode = "below_target"
WEB_OK = platform == "android" or bool(os.environ.get("PRONOTE_TEST_WEB"))  # fenêtre de connexion : Android seulement

MODE_WEB = "EduConnect (fenêtre de connexion)"
MODE_EDU = "EduConnect (identifiant EduConnect)"
MODE_QR = "QR code Pronote"
MODE_PWD = "Identifiant Pronote + mot de passe"


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
        self.root_box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        self.root_box.add_widget(Label(text=title, font_size=sp(26), bold=True, color=TEXT,
                                       size_hint_y=None, height=dp(48), halign="left",
                                       text_size=(Window.width - dp(32), None)))
        sv = ScrollView()
        self.box = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        sv.add_widget(self.box)
        self.root_box.add_widget(sv)
        self.add_widget(self.root_box)

    def set_items(self, items):
        self.box.clear_widgets()
        for t, s, r in items or [("Rien à afficher", "", "")]:
            self.box.add_widget(Card(t, s, r))


class HomeScreen(ListScreen):
    def __init__(self, title, **kw):
        super().__init__(title, **kw)
        btn = Button(text="Se déconnecter", size_hint_y=None, height=dp(46), background_normal="",
                     background_color=CARD, color=GREY + (1,))
        btn.bind(on_release=lambda *_: App.get_running_app().logout())
        self.root_box.add_widget(btn)


# ---------- Fenêtre de connexion EduConnect (WebView Android) ----------
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
        host = urlparse(site).netloc
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
        self.cookies = {}  # {adresse: cookies}
        self.finished = False
        self.step = "début"  # étape en cours (pour dire où ça bloque en cas d'erreur)
        self.web = self.cm = self.dlg = None
        self.showing = self.was_showing = False
        self.ticks = 0
        self._ev = None

    def _ui(self, fn):
        from android.runnable import run_on_ui_thread
        run_on_ui_thread(fn)()

    def open(self):
        try:
            self.step = "import android.runnable"
            self._ui(self._build)
            self._ev = Clock.schedule_interval(self._poll, 0.4)
        except Exception as e:
            self._fail("%s : %s : %s" % (self.step, type(e).__name__, str(e)[:100]))

    def _fail(self, detail):
        self.finished = True
        if self._ev:
            self._ev.cancel()
        Clock.schedule_once(lambda _: self.on_error("ERR " + detail))

    def _build(self):  # exécuté sur le thread d'interface d'Android
        try:
            self.step = "import jnius"
            from jnius import autoclass
            self.step = "activité"
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            self.step = "WebView"
            web = autoclass("android.webkit.WebView")(act)
            st = web.getSettings()
            st.setJavaScriptEnabled(True)
            st.setDomStorageEnabled(True)
            web.setWebViewClient(autoclass("android.webkit.WebViewClient")())  # reste dans la fenêtre pendant les redirections
            self.step = "cookies"
            cm = autoclass("android.webkit.CookieManager").getInstance()
            cm.removeAllCookies(None)  # connexion « propre »
            cm.setAcceptCookie(True)
            cm.setAcceptThirdPartyCookies(web, True)
            self.step = "fenêtre"
            try:
                theme = autoclass("android.R$style").Theme_Black_NoTitleBar_Fullscreen
            except Exception:
                theme = 16973832
            dlg = autoclass("android.app.Dialog")(act, theme)
            dlg.setContentView(web)
            dlg.setCancelable(True)  # le bouton Retour ferme la fenêtre
            self.step = "affichage"
            dlg.show()
            web.requestFocus()
            self.step = "chargement"
            web.loadUrl(self.url)
            self.web, self.cm, self.dlg = web, cm, dlg
            self.step = "ok"
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
            sites = [self.url] + ["https://%s/" % h for h in self.hosts] + list(self.DEFAULT_SITES)
            self.cookies = {u: self.cm.getCookie(u) or "" for u in dict.fromkeys(sites)}
            self.cookie = self.cookies.get(self.url, "")
        except Exception:
            pass

    def _see(self, url):
        host = urlparse(url or "").netloc
        if host and host not in self.hosts:
            self.hosts.append(host)

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
        self.finished = True
        self._close()
        Clock.schedule_once(lambda _: self.on_error("Connexion annulée."))

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        cookies = {u: c for u, c in self.cookies.items() if c}
        self._close()
        self.on_done(self.url, cookies, short_trail(self.hosts))

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


def open_in_browser(url):
    """Ouvre un lien dans le navigateur du téléphone (Chrome)."""
    try:
        from jnius import autoclass
        Intent, Uri = autoclass("android.content.Intent"), autoclass("android.net.Uri")
        autoclass("org.kivy.android.PythonActivity").mActivity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    except Exception:
        import webbrowser
        webbrowser.open(url)


# ---------- App ----------
class PronoteApp(App):
    POPUP_CLS = EduPopup
    NO_ENT = "Aucun ENT (connexion directe)"

    # --- construction de l'interface ---
    def build(self):
        self.title = "Pronote"
        self.root_sm = ScreenManager(transition=FadeTransition(duration=0.2))
        self.root_sm.add_widget(self._login())
        self.root_sm.add_widget(self._main())
        return self.root_sm

    def on_start(self):
        # Reconnexion automatique si on s'est déjà connecté par QR code
        creds = self._load_json("creds.json")
        if creds:
            self.status.text = "Reconnexion..."
            threading.Thread(target=self._work, args=("token", creds), daemon=True).start()

    def _login(self):
        s = Screen(name="login")
        box = BoxLayout(orientation="vertical", padding=dp(28), spacing=dp(14))
        box.add_widget(Label(text="Pronote", font_size=sp(36), bold=True, color=ACCENT + (1,),
                             size_hint_y=None, height=dp(90)))
        kw = dict(multiline=False, size_hint_y=None, height=dp(48), background_color=CARD,
                  foreground_color=TEXT, cursor_color=ACCENT + (1,), padding=(dp(14), dp(13)))
        modes = ([MODE_WEB] if WEB_OK else []) + [MODE_EDU, MODE_QR, MODE_PWD]
        self.mode = Spinner(text=modes[0], values=modes, size_hint_y=None, height=dp(48),
                            background_normal="", background_color=CARD, color=TEXT)
        self.mode.bind(text=self._on_mode)
        # Méthode QR code
        self.qr = TextInput(hint_text="Colle ici le contenu du QR code", **dict(kw, multiline=True, height=dp(110)))
        self.pin = TextInput(hint_text="Code à 4 chiffres choisi avec le QR code", input_filter="int", **kw)
        # Méthode identifiant / mot de passe
        self.url = TextInput(hint_text="Lien Pronote du lycée", **kw)
        self.ent = Spinner(text=self.NO_ENT, values=self._ent_list(), size_hint_y=None, height=dp(48),
                           background_normal="", background_color=CARD, color=TEXT)
        self.user = TextInput(hint_text="Identifiant", **kw)
        self.pwd = TextInput(hint_text="Mot de passe", password=True, **kw)
        self.form = BoxLayout(orientation="vertical", spacing=dp(14), size_hint_y=None)
        self.form.bind(minimum_height=self.form.setter("height"))
        self.status = Label(text="", color=GREY + (1,), size_hint_y=None, height=dp(64), font_size=sp(12),
                            halign="center", valign="middle")
        self.status.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        self.hint = Label(text="Une fenêtre s'ouvre sur la vraie page EduConnect de ton lycée : tu t'y connectes toi-même.",
                          color=GREY + (1,), size_hint_y=None, height=dp(48), font_size=sp(12),
                          halign="center", valign="middle")
        self.hint.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        btn = Button(text="Se connecter", size_hint_y=None, height=dp(50), background_normal="",
                     background_color=ACCENT + (1,), bold=True)
        btn.bind(on_release=self.connect)
        self.btn = btn
        self.btn_open = Button(text="1. Ouvrir Pronote dans Chrome (EduConnect)", size_hint_y=None, height=dp(44),
                               background_normal="", background_color=CARD, color=TEXT)
        self.btn_open.bind(on_release=lambda *_: open_in_browser(self._clean_url(self.url.text)))
        for w in (self.mode, self.form, self.status, btn):
            box.add_widget(w)
        box.add_widget(Widget())
        s.add_widget(box)
        self._on_mode()
        return s

    def _on_mode(self, *_):
        self.form.clear_widgets()
        self.btn.text = "Ouvrir EduConnect" if self.mode.text == MODE_WEB else "Se connecter"
        if self.mode.text == MODE_WEB:
            widgets = (self.url, self.hint)
        elif self.mode.text == MODE_QR:
            widgets = (self.url, self.btn_open, self.qr, self.pin)
        elif self.mode.text == MODE_EDU:
            widgets = (self.url, self.user, self.pwd)
        else:
            widgets = (self.url, self.ent, self.user, self.pwd)
        for w in widgets:
            self.form.add_widget(w)

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

    # --- stockage local (jeton de reconnexion) ---
    def _path(self, name):
        return os.path.join(self.user_data_dir, name)

    def _load_json(self, name):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except Exception:
            return None

    def _save_json(self, name, data):
        os.makedirs(self.user_data_dir, exist_ok=True)
        with open(self._path(name), "w") as f:
            json.dump(data, f)

    def _device_uuid(self):
        d = self._load_json("device.json")
        if not d:
            d = {"uuid": uuid.uuid4().hex}
            self._save_json("device.json", d)
        return d["uuid"]

    def logout(self):
        try:
            os.remove(self._path("creds.json"))
        except OSError:
            pass
        self.status.text = ""
        self.root_sm.current = "login"

    # --- connexion ---
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
        """Accepte n'importe quel lien donné par le lycée et en sort .../pronote/eleve.html"""
        from urllib.parse import unquote, urlparse
        url = "".join(url.strip().strip("\"'<>").split())  # espaces / retours à la ligne collés par erreur
        if "service=" in url:  # lien EduConnect : https://.../cas/login?service=<lien Pronote>
            url = unquote(url.split("service=", 1)[1].split("&")[0])
        if "://" not in url:
            url = "https://" + url
        url = "https://" + url.split("://", 1)[1]
        url = url.split("?")[0].split("#")[0]  # enlève ?login=true, etc.
        url = url.replace("mobile.eleve.html", "eleve.html").replace("mobile.parent.html", "parent.html")
        if "eleve.html" not in url and "parent.html" not in url:
            p = urlparse(url)
            path = p.path.rstrip("/")
            if not path:
                path = "/pronote"
            url = f"{p.scheme}://{p.netloc}{path}/eleve.html"
        return url

    @staticmethod
    def _friendly(e):
        s = str(e)
        if "Page html is different" in s:
            return ("Le lien ne mène pas à la page de connexion Pronote (ton lycée utilise sûrement "
                    "EduConnect). Utilise la méthode QR code.")
        if "probably wrong login information" in s:
            return "Identifiant ou mot de passe EduConnect incorrect."
        if "HubEduConnect" in s:
            return "Ton lycée n'est pas compatible avec cette connexion EduConnect. Utilise la méthode QR code."
        if "Connection failure" in s:
            return ("La connexion EduConnect automatique n'a pas abouti (ton lycée n'utilise sûrement pas ce chemin). "
                    "Utilise la méthode QR code.")
        if "IP address is suspended" in s:
            return "Pronote a bloqué ton adresse IP un moment. Réessaie plus tard."
        if "MFA" in type(e).__name__:
            return "Ton compte demande une double authentification (pas encore gérée)."
        if "QR" in type(e).__name__ or "decrypt" in s.lower():
            return "QR code ou code à 4 chiffres incorrect (ou QR code expiré). Génère-en un nouveau."
        if "Connection" in type(e).__name__ or "Max retries" in s:
            return "Pas de connexion internet, ou lien Pronote incorrect."
        return s[:220] or type(e).__name__

    def open_popup(self):
        url = self._clean_url(self.url.text)
        if "parent.html" in url:
            self.status.text = "Ce lien est celui d'un compte parent : l'app gère seulement les comptes élèves."
            return
        self.status.text = "Ouverture de la fenêtre EduConnect..."
        try:
            self.popup = self.POPUP_CLS(url, self._popup_done, self._popup_error)
            self.popup.open()
        except Exception as e:
            self._popup_error(str(e))

    def _popup_error(self, msg):
        if "annulée" in msg:
            self.status.text = "Connexion annulée."
            return
        self.status.text = "La fenêtre EduConnect n'a pas pu s'ouvrir. Utilise la méthode QR code. [%s]" % msg.replace("ERR ", "")[:170]
        self.mode.text = MODE_QR

    def _popup_done(self, url, cookies, trail=""):
        if not any(cookies.values() if isinstance(cookies, dict) else [cookies]):
            self.status.text = "Aucune connexion détectée. Réessaie, ou utilise la méthode QR code. [%s]" % trail
            return
        self.status.text = "Connexion à Pronote..."
        threading.Thread(target=self._work, args=("web", {"url": url, "cookie": cookies, "trail": trail}),
                         daemon=True).start()

    def connect(self, *_):
        if self.mode.text == MODE_WEB:
            return self.open_popup()
        self.status.text = "Connexion..."
        if self.mode.text == MODE_QR:
            args = ("qr", {"qr": self.qr.text.strip(), "pin": self.pin.text.strip()})
        else:
            ent = "EDUCONNECT" if self.mode.text == MODE_EDU else self.ent.text
            args = ("pwd", {"url": self._clean_url(self.url.text), "user": self.user.text.strip(),
                            "pwd": self.pwd.text, "ent": ent})
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _work(self, kind, p):
        try:
            import pronotepy
            if kind == "qr":
                try:
                    qr = json.loads(p["qr"])
                    qr["jeton"], qr["login"], qr["url"]
                except Exception:
                    raise Exception("Contenu du QR code invalide : colle tout le texte lu dans le QR code.")
                if not (p["pin"].isdigit() and len(p["pin"]) == 4):
                    raise Exception("Le code doit faire exactement 4 chiffres.")
                c = pronotepy.Client.qrcode_login(qr, p["pin"], self._device_uuid())
            elif kind == "token":
                c = pronotepy.Client.token_login(**p)
            elif kind == "web":
                # cookies de la session EduConnect ouverte dans la fenêtre
                jar = cookie_jar(p["cookie"], p["url"])
                c = pronotepy.Client(p["url"], username="webview", password="webview", ent=lambda *a, **k: jar)
            else:
                import pronotepy.ent as ent_mod
                if "parent.html" in p["url"]:
                    raise Exception("Ce lien est celui d'un compte parent : l'app gère seulement les comptes élèves.")
                if p["ent"] == "EDUCONNECT":
                    # Connexion EduConnect via le hub d'Index Education (fonction nommée "bordeaux" dans pronotepy)
                    ent = getattr(ent_mod, "bordeaux", None) or ent_mod.pronote_hubeduconnect(p["url"])
                else:
                    ent = None if p["ent"] == self.NO_ENT else getattr(ent_mod, p["ent"])
                c = pronotepy.Client(p["url"], username=p["user"], password=p["pwd"], ent=ent)
            if not c.logged_in:
                raise Exception("Identifiants refusés")
            if kind in ("pwd", "web"):
                # On échange la connexion contre un jeton : plus besoin de ressaisir (ni de stocker) le mot de passe
                try:
                    pin = "".join(random.choice("123456789") for _ in range(4))
                    c = pronotepy.Client.qrcode_login(c.request_qr_code_data(pin), pin, self._device_uuid())
                except Exception:
                    pass
            if kind in ("qr", "token", "pwd", "web"):
                try:
                    self._save_json("creds.json", c.export_credentials())  # le jeton change à chaque connexion
                except Exception:
                    pass
            grades = [(g.subject.name, g.date.strftime("%d/%m"), f"{g.grade}/{g.out_of}")
                      for g in c.current_period.grades]
            hw = [(h.subject.name, h.description[:80], h.date.strftime("%d/%m"))
                  for h in c.homework(date.today())]
            edt = [(l.subject.name if l.subject else "Cours", l.room or "", l.start.strftime("%H:%M"))
                   for l in c.lessons(date.today())]
            data = (c.info.name, grades, hw, edt)
            Clock.schedule_once(lambda _: self._done(data))
        except Exception as e:
            msg = self._friendly(e)
            if kind == "web":
                msg = ("Pronote n'a pas reconnu la connexion de la fenêtre EduConnect. Utilise la méthode QR code. [%s]"
                       % p.get("trail", ""))
            if kind == "token":
                msg = "Session expirée, génère un nouveau QR code. (" + msg[:80] + ")"
                try:
                    os.remove(self._path("creds.json"))
                except OSError:
                    pass
            Clock.schedule_once(lambda _: setattr(self.status, "text", msg))
            if kind in ("pwd", "web") and "méthode QR code" in msg:  # on bascule sur le QR code, c'est la solution
                Clock.schedule_once(lambda _: setattr(self.mode, "text", MODE_QR))

    def _done(self, data):
        name, grades, hw, edt = data
        self.status.text = ""
        self.pages["home"].set_items([(f"Salut {name}", "Connecté à Pronote", "")])
        self.pages["grades"].set_items(grades)
        self.pages["homework"].set_items(hw)
        self.pages["edt"].set_items(edt)
        self.root_sm.current = "main"


if __name__ == "__main__":
    PronoteApp().run()
