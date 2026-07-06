"""py2app build script for the Private Agent menu bar app.

Usage:
    python setup.py py2app
"""

from setuptools import setup

APP = ["src/private_agent/menubar.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Private Agent",
        "CFBundleDisplayName": "Private Agent",
        "CFBundleIdentifier": "com.rajansharma.private-agent",
        "CFBundleShortVersionString": "0.2.0",
        "LSUIElement": True,
    },
    "packages": ["private_agent", "rumps", "langchain_apple_foundation_models"],
}

setup(
    app=APP,
    name="Private Agent",
    options={"py2app": OPTIONS},
    data_files=DATA_FILES,
    setup_requires=["py2app"],
)
