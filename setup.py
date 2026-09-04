"""py2app configuration — builds Trail Coach.app"""

from setuptools import setup

APP = ["app.py"]
DATA_FILES = [
    ("frontend", ["frontend/index.html"]),
    ("frontend/css", ["frontend/css/style.css"]),
    ("frontend/js", ["frontend/js/app.js"]),
]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["flask", "anthropic", "garminconnect", "dotenv", "webview"],
    "includes": ["api.garmin", "api.chat"],
    "iconfile": "assets/icon.icns",
    "plist": {
        "CFBundleName": "Trail Coach",
        "CFBundleDisplayName": "Trail Coach",
        "CFBundleIdentifier": "com.example.trailcoach",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0",
        "NSHumanReadableCopyright": "© 2026 Trail Coach contributors",
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
