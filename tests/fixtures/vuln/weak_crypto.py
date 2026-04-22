# Fixtures for weak_crypto rules — every line here SHOULD fire a rule.
# Keep each vuln on its own line + no fixes so counts are predictable.

import hashlib
import random
import secrets
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2


def weak_md5(data):
    return hashlib.md5(data).hexdigest()  # WEAK_HASH_MD5


def weak_sha1(data):
    return hashlib.sha1(data).hexdigest()  # WEAK_HASH_SHA1


def insecure_random_token():
    return str(random.random())  # INSECURE_RANDOM_PY


def insecure_random_choice(items):
    return random.choice(items)  # INSECURE_RANDOM_PY


def bad_aes_ecb(key, plaintext):
    cipher = AES.new(key, AES.MODE_ECB)  # WEAK_AES_ECB
    return cipher.encrypt(plaintext)


def hardcoded_iv(key, plaintext):
    iv = bytes(16)  # HARDCODED_IV_ZERO
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(plaintext)


def weak_kdf(password, salt):
    return PBKDF2(password, salt, 32, 1000, 'md5')  # WEAK_KDF_MD5
