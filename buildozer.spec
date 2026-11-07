[app]
title = Pronote
package.name = pronoteapp
package.domain = org.canichou
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1
requirements = python3,kivy==2.3.0,pronotepy,autoslot,requests,urllib3,idna,certifi,charset-normalizer,pycryptodome,beautifulsoup4,soupsieve,typing_extensions,pytz
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
