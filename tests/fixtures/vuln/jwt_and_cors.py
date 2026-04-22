# JWT + CORS (Python side) + TLS (Go-ish indirectly) rules.
import jwt
from flask import Flask
from flask_cors import CORS

# JWT alg none + verify False
claims = jwt.decode(token, key, algorithms=['none'])  # JWT_ALG_NONE
unsafe = jwt.decode(token, verify=False)              # JWT_VERIFY_FALSE

# CORS origins=*
app = Flask(__name__)
CORS(app, origins='*')  # CORS_ORIGIN_ANY_FLASK
