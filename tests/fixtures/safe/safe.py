# Safe versions — same concepts written correctly. Should produce ZERO
# findings. Any hit here is a false positive worth inspecting.

import hashlib
import secrets
import os
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2


def strong_hash(data):
    return hashlib.sha256(data).hexdigest()


def secure_token():
    return secrets.token_urlsafe(32)


def strong_aes(key, plaintext):
    iv = os.urandom(16)
    cipher = AES.new(key, AES.MODE_GCM, iv)
    return cipher.encrypt_and_digest(plaintext)


def good_kdf(password, salt):
    return PBKDF2(password, salt, 32, 200_000, 'sha256')


# requests with TLS verification on (the default)
import requests
r = requests.get("https://api.example.com")


# Django DEBUG off via env
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


# Flask prod launch
from flask import Flask
app = Flask(__name__)
if __name__ == "__main__":
    app.run(host="0.0.0.0")


# Parameterized SQL (no taint flow)
def safe_query(cur, user_id):
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))


# bcrypt with good rounds
import bcrypt
hashed = bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=12))
