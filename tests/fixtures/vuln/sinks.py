# Taint sinks — covers ssrf / open_redirect / xxe / ldap / nosql /
# deserialization categories. The taint analyser expects a tainted source
# to reach each sink; here we feed from request.args.

import requests
import httpx
import urllib.request
import yaml
import pickle
from flask import request, redirect
from lxml import etree

def ssrf_requests():
    url = request.args.get("u")
    return requests.get(url).text                   # ssrf

def ssrf_httpx():
    url = request.args.get("u")
    return httpx.get(url).text                      # ssrf

def ssrf_urllib():
    url = request.args.get("u")
    return urllib.request.urlopen(url).read()       # ssrf

def open_redirect():
    return redirect(request.args.get("next"))       # open_redirect

def xxe_lxml():
    data = request.data
    return etree.fromstring(data)                   # xxe

def deserialize_pickle():
    return pickle.loads(request.data)               # deserialization

def deserialize_yaml():
    return yaml.load(request.data)                  # deserialization (UNSAFE_YAML_LOAD too)

def ldap_search():
    import ldap
    q = request.args.get("q")
    return ldap.search(f"(uid={q})")                # ldap_injection

def nosql_find():
    from pymongo import MongoClient
    return MongoClient().db.collection.find(request.json)  # nosql_injection

def redos():
    import re
    pat = request.args.get("p")
    return re.compile(pat)                          # redos
