# TLS + debug rules.
import requests
import ssl
import urllib3

# TLS verify disabled — 3 ways
r1 = requests.get("https://internal", verify=False)  # TLS_VERIFY_DISABLED_PY
ctx = ssl._create_unverified_context()               # TLS_VERIFY_DISABLED_PY
urllib3.disable_warnings()  # irrelevant — not a rule target

# Django debug on
DEBUG = True  # DEBUG_ENABLED_DJANGO

# Flask debug mode
from flask import Flask
app = Flask(__name__)
if __name__ == "__main__":
    app.run(debug=True)  # DEBUG_ENABLED_FLASK

# Subprocess env expansion
import os
os.system("cat ${LOG_FILE}")  # PY_SUBPROCESS_SHELL_EXPANSION
