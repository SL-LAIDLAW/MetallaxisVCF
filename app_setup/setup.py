"""
This is a setup.py script generated by py2applet

Usage:
    python setup.py py2app
"""

from setuptools import setup

APP = ['../metallaxis/__main__.py']
APP_NAME = "Metallaxis"
DATA_FILES = ['*.ui', 'annotation/*']

OPTIONS = {
    'iconfile': '/Users/seanlaidlaw/Documents/dev/BCD/Projet/Metallaxis/icons/MyIcon.icns',
    'plist': {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleGetInfoString': "A GUI VCF viewer",
        'CFBundleIdentifier': "com.metallaxis.osx.metallaxis",
        'CFBundleVersion': "0.0.9",
        'CFBundleShortVersionString': "0.0.9",
        'NSHumanReadableCopyright': u"Copyright © 2018, Sean LAIDLAW, Licenced under GPLv3, Some Rights Reserved"
    }
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
