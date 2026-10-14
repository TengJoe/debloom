#!/usr/bin/python3
"""WebKitGTK desktop frontend. No HTTP server and no in-app password entry."""
from __future__ import annotations

import argparse
import atexit
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, WebKit

from backend import AuthorizationCancelled, InstallerError, PackageSession
from appearance import AppearancePreferences, MODES
from localization import LanguagePreferences, translate
from updates import check_for_update, UpdateError
from version import VERSION

ROOT = Path(__file__).resolve().parent


class InstallerApp(Gtk.Application):
    def __init__(self, filenames=None, smoke_dir=None, smoke_width=1040):
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if smoke_dir:
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id="local.deb.Installer", flags=flags)
        self.session = PackageSession()
        atexit.register(self.session.cleanup)
        self.window = None
        self.view = None
        self.loaded = False
        self.busy = False
        self.phase = "empty"
        self.message = "选择一个或多个下载好的 DEB 文件。"
        self.pending_files = filenames or []
        self.smoke_dir = Path(smoke_dir) if smoke_dir else None
        self.appearance = AppearancePreferences(self.smoke_dir / "appearance.json" if self.smoke_dir else None)
        self.language = LanguagePreferences(self.smoke_dir / 'language.json' if self.smoke_dir else None)
        Adw.init()
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_system_appearance)
        self.apply_appearance()
        self.smoke_snapshots = []
        self.frontend_checks = {}
        self.appearance_checks = {}
        self.locale_checks = {}
        self.update_busy = False
        self.update_state = {'type':'updates','status':'idle','current':VERSION}
        self.smoke_width = smoke_width
        self.smoke_timeout = None
        self.exit_status = 0
        self.connect("activate", self.on_activate)
        self.connect("open", self.on_open)

    def on_activate(self, _app):
        if self.window is None:
            self.window = Gtk.ApplicationWindow(application=self, title=self.tr('Debloom'))
            self.window.set_default_size(self.smoke_width if self.smoke_dir else 1040,
                                         640 if self.smoke_dir and self.smoke_width == 800 else 800)
            self.window.set_size_request(800, 640)
            self.window.connect("close-request", self.on_close)
            manager = WebKit.UserContentManager()
            manager.register_script_message_handler("app", None)
            manager.connect("script-message-received::app", self.on_message)
            self.view = WebKit.WebView(user_content_manager=manager)
            settings = self.view.get_settings()
            settings.set_allow_file_access_from_file_urls(False)
            settings.set_allow_universal_access_from_file_urls(False)
            settings.set_enable_developer_extras(False)
            self.view.connect("decide-policy", self.on_policy)
            drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drop.connect("drop", self.on_drop)
            self.view.add_controller(drop)
            self.window.set_child(self.view)
            html = (ROOT / "index.html").read_text()
            effective = "dark" if self.style_manager.get_dark() else "light"
            html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-theme="' + effective + '">', 1)
            csp = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">'
            html = html.replace("<head>", "<head>" + csp, 1)
            self.view.load_html(html, "about:blank")
            if self.smoke_dir:
                self.smoke_dir.mkdir(parents=True, exist_ok=True)
                self.smoke_timeout = GLib.timeout_add_seconds(60, self.smoke_failed)
        self.window.present()

    def on_open(self, _app, files, _count, _hint):
        paths = [file.get_path() for file in files]
        paths = [path for path in paths if path]
        if self.busy:
            self.message = "当前任务尚未结束，请完成后再选择其他包。"
            self.send_state()
        elif self.loaded:
            self.load_files(paths)
        else:
            self.pending_files = paths
        self.on_activate(self)

    def on_policy(self, _view, decision, kind):
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri != "about:blank":
                decision.ignore()
                return True
        if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def on_close(self, _window):
        if self.busy:
            self.message = "任务正在进行，请等待结果后关闭窗口。"
            self.send_state()
            return True
        self.session.cleanup()
        return False

    def event(self, data):
        if self.loaded and self.view:
            target = {'appearance': 'Appearance', 'locale': 'Locale', 'updates':'Updates'}.get(data.get('type'), 'App')
            js = "window." + target + " && window." + target + ".receive(" + json.dumps(data, ensure_ascii=True) + ");"
            self.view.evaluate_javascript(js, -1, None, None, None, None, None)

    def send_state(self):
        self.event({"type": "state", "phase": self.phase, "busy": self.busy,
                    "packages": self.session.package_data(), "planText": self.tr(self.session.plan_text),
                    "message": self.tr(self.message), "canInstall": self.session.can_install})

    def tr(self, text):
        return translate(text, self.language.mode)

    def send_locale(self, error=''):
        self.event({'type':'locale', 'language':self.language.mode, 'error':self.tr(error)})

    def set_language(self, language):
        if language not in self.language.modes:
            return
        try:
            self.language.save(language)
        except OSError:
            self.send_locale('语言设置未能保存，请检查配置目录权限。')
            return
        if self.window:
            self.window.set_title(self.tr('Debloom'))
        self.send_locale()
        self.send_state()

    def check_updates(self):
        if self.update_busy:
            return
        self.update_busy = True
        self.update_state = {'type':'updates','status':'checking','current':VERSION}
        self.event(self.update_state)
        def worker():
            try:
                state = {'type':'updates', **check_for_update()}
            except UpdateError as error:
                state = {'type':'updates','status':'error','current':VERSION,'error':str(error)}
            GLib.idle_add(self.updates_finished,state)
        threading.Thread(target=worker,daemon=True).start()

    def updates_finished(self,state):
        self.update_busy = False
        self.update_state = state
        self.event(state)
        return False

    def log(self, text):
        GLib.idle_add(self.event, {"type": "log", "text": text})

    def apply_appearance(self):
        schemes = {"system": Adw.ColorScheme.DEFAULT,
                   "light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}
        self.style_manager.set_color_scheme(schemes[self.appearance.mode])

    def send_appearance(self, error=""):
        self.event({"type": "appearance", "mode": self.appearance.mode,
                    "effective": "dark" if self.style_manager.get_dark() else "light", "error": self.tr(error)})

    def on_system_appearance(self, _manager, _property):
        self.send_appearance()

    def set_appearance(self, mode):
        if mode not in MODES:
            return
        try:
            self.appearance.save(mode)
        except OSError:
            self.send_appearance("外观设置未能保存，请检查配置目录权限。")
            return
        self.apply_appearance()
        self.send_appearance()

    def on_message(self, _manager, value):
        try:
            data = json.loads(value.to_string())
        except (ValueError, AttributeError):
            return
        if not isinstance(data, dict):
            return
        action = data.get("action")
        if action == "ready":
            if self.loaded:
                return
            self.loaded = True
            self.send_locale()
            self.send_state()
            self.send_appearance()
            self.event(self.update_state)
            if self.smoke_dir:
                GLib.timeout_add(600, self.smoke_empty)
            elif self.pending_files:
                self.load_files(self.pending_files)
                self.pending_files = []
            return
        if action == "appearance":
            self.set_appearance(data.get("mode"))
            return
        if action == 'locale':
            self.set_language(data.get('language'))
            return
        if action == 'check-updates':
            self.check_updates()
            return
        if action == 'open-release':
            if self.update_state.get('status') == 'available':
                try:
                    Gio.AppInfo.launch_default_for_uri(self.update_state['url'],None)
                except GLib.Error:
                    self.event({'type':'updates','status':'error','current':VERSION,'error':'无法打开浏览器，请手动访问项目发布页。'})
            return
        if self.busy:
            return
        if action == "choose":
            self.choose()
        elif action == "reset":
            self.session.cleanup()
            self.phase, self.message = "empty", "选择一个或多个下载好的 DEB 文件。"
            self.event({"type": "clear-log"})
            self.send_state()
        elif action == "check" and self.session.paths:
            self.check()
        elif action == "install" and self.phase == "checked" and self.session.can_install:
            if self.smoke_dir:
                return  # Smoke mode can never invoke pkexec.
            self.install()

    def choose(self):
        dialog = Gtk.FileDialog(title=self.tr('选择 DEB 软件包'))
        file_filter = Gtk.FileFilter()
        file_filter.set_name(self.tr('DEB 软件包'))
        file_filter.add_pattern("*.deb")
        file_filter.add_pattern("*.DEB")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        try:
            downloads = subprocess.run(["/usr/bin/xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True)
            folder = downloads.stdout.strip() if downloads.returncode == 0 else ''
        except FileNotFoundError:
            folder = ''
        if not folder:
            folder = str(next((p for p in [Path.home()/'Downloads', Path.home()/'下载'] if p.is_dir()),Path.home()))
        dialog.set_initial_folder(Gio.File.new_for_path(folder))
        dialog.open_multiple(self.window, None, self.chosen)

    def chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error as error:
            if error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED) or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.CANCELLED):
                return
            self.phase, self.message = "error", "文件选择器未能完成，请重试。"
            self.log(str(error) + "\n")
            self.send_state()
            return
        paths = [file.get_path() for file in files]
        self.load_files([path for path in paths if path])

    def on_drop(self, _target, value, _x, _y):
        if self.busy or not isinstance(value, Gdk.FileList):
            return False
        paths = [file.get_path() for file in value.get_files()]
        paths = [path for path in paths if path]
        if not paths:
            return False
        self.load_files(paths)
        return True

    def task(self, phase, message, work, finished):
        if self.busy:
            return
        self.phase, self.message, self.busy = phase, message, True
        self.send_state()

        def worker():
            try:
                work()
            except Exception as error:
                GLib.idle_add(self.failed, error)
            else:
                GLib.idle_add(self.finished, finished)
        threading.Thread(target=worker, daemon=False).start()

    def finished(self, callback):
        self.busy = False
        try:
            callback()
        except Exception as error:
            self.failed(error)
            return
        self.send_state()
        if self.smoke_dir:
            if self.phase == "ready":
                # Exercise the real HTML button -> JS bridge -> Python action path.
                self.view.evaluate_javascript('document.getElementById("btn-primary").click();', -1,
                                              None, None, None, None, None)
            elif self.phase == "checked":
                GLib.timeout_add(600, self.smoke_checked)

    def failed(self, error):
        self.busy = False
        self.phase = "cancelled" if isinstance(error, AuthorizationCancelled) else "error"
        self.message = str(error) if isinstance(error, (InstallerError, OSError)) else "操作未能完成，请查看详细日志。"
        self.log(str(error) + "\n")
        self.send_state()
        if self.smoke_dir:
            self.smoke_failed()

    def load_files(self, paths):
        self.event({"type": "clear-log"})
        def done():
            self.phase, self.message = "ready", "软件包已就绪，请先检查安装计划。"
        self.task("reading", "正在读取软件包……", lambda: self.session.load(paths), done)

    def check(self):
        self.event({"type": "clear-log"})
        def done():
            self.phase = "checked"
            self.message = ("检查完成。请核对下方计划，确认后安装。" if self.session.can_install
                            else "所选版本已安装且无需更改，不必重复安装。")
        self.task("checking", "正在检查依赖和安装计划……", lambda: self.session.check(self.log), done)

    def install(self):
        self.event({"type": "clear-log"})
        def started():
            GLib.idle_add(self.installing)
        def done():
            self.phase, self.message = "success", "安装完成，已核对软件包版本和配置状态。"
            # Keep metadata for display; remove staged copies as soon as APT is done.
            if self.session.temp:
                self.session.temp.cleanup()
                self.session.temp = None
            self.session.paths = []
        self.task("authorizing", "正在核对计划，随后弹出系统授权窗口……",
                  lambda: self.session.install(self.log, started), done)

    def installing(self):
        self.phase, self.message = "installing", "正在安装软件包，可以展开日志查看详情。"
        self.send_state()

    def smoke_empty(self):
        self.capture_theme("light", "light.png", lambda:
            self.capture_theme("dark", "dark.png", lambda:
                self.capture_theme("system", "system.png", self.smoke_load)))
        return False

    def capture_theme(self, mode, filename, callback):
        deadline = time.monotonic() + 8
        script = '(() => {const s=document.getElementById("appearance-select"); s.value=' + json.dumps(mode) + ';s.dispatchEvent(new Event("change",{bubbles:true}));})();'
        self.view.evaluate_javascript(script,-1,None,None,None,None,None)
        def check():
            script = '''JSON.stringify({selected:document.getElementById("appearance-select").value,
                effective:document.documentElement.dataset.theme,
                background:getComputedStyle(document.body).backgroundColor,
                horizontal_overflow:document.documentElement.scrollWidth>window.innerWidth,
                width:window.innerWidth, height:window.innerHeight,
                options:[...document.getElementById("appearance-select").options].map(o=>o.value)})'''
            def received(view, result):
                try:
                    data = json.loads(view.evaluate_javascript_finish(result).to_string())
                    effective = "dark" if self.style_manager.get_dark() else "light"
                    if data["selected"] != mode or data["effective"] != effective or data["horizontal_overflow"]:
                        if time.monotonic() < deadline and not data['horizontal_overflow']:
                            GLib.timeout_add(100,check)
                            return
                        raise RuntimeError("appearance state/layout mismatch: " + str(data))
                    if self.appearance.mode != mode or AppearancePreferences(self.appearance.path).mode != mode:
                        raise RuntimeError("appearance preference was not saved")
                    if set(data["options"]) != set(MODES):
                        raise RuntimeError("appearance options missing")
                    self.appearance_checks[filename] = data
                except Exception as error:
                    print(str(error),file=sys.stderr)
                    self.smoke_failed()
                    return
                self.snapshot(filename,callback)
            self.view.evaluate_javascript(script,-1,None,None,None,received)
            return False
        GLib.timeout_add(450,check)

    def smoke_load(self):
        script = r'''(() => {
            const dangerous = '<img src=x onerror="window.__injected=1">';
            const base = {type:'state', phase:'checked', busy:false, canInstall:true,
                packages:[{name:dangerous, filename:dangerous, description:dangerous,
                version:'1.0', architecture:'all', size:1048576}], planText:dangerous, message:dangerous};
            window.App.receive(base);
            const checks = {
                external_text_is_literal: document.body.innerText.includes(dangerous),
                no_html_injection: !document.querySelector('img[src=x]') && !window.__injected,
                no_password_field: !document.querySelector('input[type=password]'),
                checked_install_enabled: !document.getElementById('btn-primary').disabled,
                size_is_readable: document.body.innerText.includes('1 MiB')
            };
            window.App.receive({...base, canInstall:false});
            checks.no_change_disables_install = document.getElementById('btn-primary').disabled;
            window.App.receive({...base, busy:true});
            checks.busy_disables_actions = ['btn-choose-empty','btn-primary','btn-secondary'].every(id=>document.getElementById(id).disabled);
            document.getElementById('btn-settings').click();
            checks.settings_remain_accessible_while_busy = document.getElementById('settings-dialog').open;
            document.getElementById('settings-close').click();
            window.App.receive({...base, phase:'error', packages:[], canInstall:false});
            checks.invalid_package_allows_reselect = !document.getElementById('btn-primary').disabled && document.getElementById('btn-primary').onclick !== null;
            checks.error_expands_log = document.getElementById('log-details').open;
            window.App.receive({type:'log', text:'x'.repeat(160000)});
            checks.log_is_bounded = document.getElementById('log-output').textContent.length === 150000;
            window.App.receive({type:'clear-log'});
            checks.log_can_clear = document.getElementById('log-output').textContent === '';
            document.getElementById('log-details').open = false;
            return JSON.stringify(checks);
        })();'''
        def verified(view, result):
            try:
                self.frontend_checks = json.loads(view.evaluate_javascript_finish(result).to_string())
                if not all(self.frontend_checks.values()):
                    raise RuntimeError(str(self.frontend_checks))
            except Exception as error:
                print(f"frontend checks failed: {error}", file=sys.stderr)
                self.smoke_failed()
                return
            self.event({"type": "clear-log"})
            self.send_state()
            self.smoke_load_files()
        self.view.evaluate_javascript(script, -1, None, None, None, verified)

    def smoke_load_files(self):
        if self.pending_files:
            paths, self.pending_files = self.pending_files, []
            self.load_files(paths)
        else:
            self.smoke_finish()

    def smoke_checked(self):
        self.capture_theme("light","checked-light.png", lambda:
            self.capture_theme("dark","checked-dark.png", lambda:
                self.capture_theme("system","checked-system.png", self.smoke_locales)))
        return False

    def smoke_locales(self):
        self.capture_locale('en', 'checked-en.png', lambda:
            self.capture_theme('light', 'checked-en-light.png', lambda:
                self.capture_theme('dark', 'checked-en-dark.png', lambda:
                    self.capture_locale('zh', 'checked-zh.png', lambda:
                        self.capture_theme('light', 'checked-zh-light.png', lambda:
                            self.capture_theme('dark', 'checked-zh-dark.png', self.smoke_settings))))))

    def smoke_settings(self):
        self.view.evaluate_javascript('document.getElementById("btn-settings").click();',-1,None,None,None,None,None)
        GLib.timeout_add(500, lambda: self.settings_snapshot())

    def settings_snapshot(self):
        self.snapshot('settings-zh.png',lambda:
            self.capture_locale('en','settings-en.png',self.smoke_finish))
        return False

    def capture_locale(self, language, filename, callback):
        deadline = time.monotonic() + 8
        script = 'const s=document.getElementById("language-select");s.value='+json.dumps(language)+';s.dispatchEvent(new Event("change",{bubbles:true}));'
        self.view.evaluate_javascript('(()=>{'+script+'})();',-1,None,None,None,None,None)
        def check():
            script = '''JSON.stringify({selected:document.getElementById('language-select').value,
                lang:document.documentElement.lang,primary:document.getElementById('btn-primary').textContent,
                title:document.querySelector('h1').textContent,
                horizontal_overflow:document.documentElement.scrollWidth>innerWidth})'''
            def received(view,result):
                try:
                    data=json.loads(view.evaluate_javascript_finish(result).to_string())
                    expected={'zh':'确认并安装','en':'Confirm & install'} if self.session.can_install else {'zh':'无需重复安装','en':'Already installed'}
                    if data['primary']!=expected[language] or data['lang']!=('zh-CN' if language=='zh' else 'en'):
                        if time.monotonic()<deadline:
                            GLib.timeout_add(100,check)
                            return
                        raise RuntimeError('Language did not reach rendered UI: '+str(data))
                    if data['horizontal_overflow'] or data['selected']!=language or self.language.mode!=language or LanguagePreferences(self.language.path).mode!=language:
                        raise RuntimeError('Language persistence/layout mismatch: '+str(data))
                    self.locale_checks[filename]=data
                    self.snapshot(filename,callback)
                except Exception as error:
                    print(str(error),file=sys.stderr)
                    self.smoke_failed()
            self.view.evaluate_javascript(script,-1,None,None,None,received)
            return False
        GLib.timeout_add(450,check)

    def snapshot(self, filename, callback):
        def ready(view, result):
            try:
                texture = view.get_snapshot_finish(result)
                if not texture.save_to_png(str(self.smoke_dir / filename)):
                    raise RuntimeError("截图保存失败")
                self.smoke_snapshots.append(filename)
            except Exception as error:
                print(f"snapshot failed: {error}", file=sys.stderr)
                self.smoke_failed()
                return
            callback()
        self.view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE,
                               None, ready)

    def smoke_finish(self):
        # Record actual WebKit bridge and backend state, not just a successful process exit.
        report = {"bridge_ready": self.loaded, "phase": self.phase,
                  "packages": self.session.package_data(), "canInstall": self.session.can_install,
                  "snapshots": self.smoke_snapshots, "frontend_checks": self.frontend_checks,
                  "appearance_checks":self.appearance_checks,"locale_checks":self.locale_checks,
                  "check_triggered_by_html_button": self.phase == "checked", "real_install": False}
        (self.smoke_dir / "native-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if self.smoke_timeout:
            GLib.source_remove(self.smoke_timeout)
        self.session.cleanup()
        self.quit()

    def smoke_failed(self):
        self.exit_status = 1
        print("native frontend smoke test failed or timed out", file=sys.stderr)
        if self.smoke_dir and self.smoke_dir.exists():
            failure = {"status":"failed","exit_status":1,"phase":self.phase,"busy":self.busy,
                "message":self.message,"bridge_ready":self.loaded,"frontend_checks":self.frontend_checks,
                "packages":self.session.package_data(),"snapshots":self.smoke_snapshots,
                "check_triggered_by_html_button":False,"real_install":False}
            encoded = json.dumps(failure,ensure_ascii=False,indent=2)
            (self.smoke_dir / "failure.json").write_text(encoded)
            # A previous successful report in the same output folder must not look current.
            (self.smoke_dir / "native-smoke.json").write_text(encoded)
        self.session.cleanup()
        self.quit()
        return False


def main():
    parser = argparse.ArgumentParser(description="Debloom graphical installer")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-width", type=int, choices=(800,1040), default=1040, help=argparse.SUPPRESS)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    app = InstallerApp(args.files, args.smoke_test, args.smoke_width)
    # Normal file arguments must reach GApplication, including an already-running instance.
    argv = [sys.argv[0]] if args.smoke_test else [sys.argv[0], *[str(Path(p).absolute()) for p in args.files]]
    return app.run(argv) or app.exit_status


if __name__ == "__main__":
    raise SystemExit(main())
