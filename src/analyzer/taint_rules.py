"""
Default taint analysis rules — sources, sinks, and sanitizers by language.
"""

# Sources: where untrusted data enters the application
SOURCES = {
    "python": [
        "request.args",
        "request.form",
        "request.json",
        "request.data",
        "request.cookies",
        "request.headers",
        "request.values",
        "request.files",
        "sys.argv",
        "os.environ",
        "input()",
    ],
    "javascript": [
        "req.body",
        "req.query",
        "req.params",
        "req.headers",
        "document.location",
        "window.location",
        "location.hash",
        "location.search",
    ],
    "go": [
        "r.URL.Query()",
        "r.FormValue(",
        "r.Body",
        "r.Header.Get(",
    ],
}

# Sinks: dangerous functions that should not receive tainted data
# Each entry: (pattern, vuln_type, severity, recommendation)
SINKS = {
    "sql_injection": [
        ("cursor.execute", "high", "Use parameterized query: cursor.execute(sql, params)"),
        ("db.execute", "high", "Use parameterized query: db.execute(sql, params)"),
        ("session.execute", "high", "Use parameterized query with bound parameters"),
        ("engine.execute", "high", "Use parameterized query with bound parameters"),
        ("connection.execute", "high", "Use parameterized query with bound parameters"),
    ],
    "rce": [
        ("eval(", "critical", "Avoid eval() with user input; use ast.literal_eval() for data"),
        ("exec(", "critical", "Never exec() user-controlled strings"),
        ("os.system(", "critical", "Use subprocess.run() with a list of args, no shell=True"),
        ("os.popen(", "critical", "Use subprocess.run() with a list of args"),
        ("subprocess.run", "high", "Do not pass shell=True with user input; use arg list"),
        ("subprocess.call", "high", "Do not pass shell=True with user input; use arg list"),
        ("subprocess.Popen", "high", "Do not pass shell=True with user input; use arg list"),
    ],
    "xss": [
        ("render_template_string(", "high", "Use render_template() with auto-escaping"),
        ("Markup(", "medium", "Ensure input is sanitized before wrapping in Markup()"),
        (".innerHTML", "high", "Use textContent or sanitize with DOMPurify"),
        ("v-html", "medium", "Sanitize input before using v-html directive"),
    ],
    "path_traversal": [
        ("open(", "high", "Validate and sanitize file paths; use os.path.realpath()"),
        ("send_file(", "high", "Use safe_join() or validate paths against a whitelist"),
    ],
    "deserialization": [
        ("pickle.loads(", "critical", "Never unpickle untrusted data; use JSON instead"),
        ("pickle.load(", "critical", "Never unpickle untrusted data; use JSON instead"),
        ("yaml.load(", "high", "Use yaml.safe_load() instead of yaml.load()"),
        ("yaml.unsafe_load(", "critical", "Use yaml.safe_load() instead"),
    ],
}

# Sanitizers: functions that neutralize taint
# Each entry: (pattern, list of vuln_types it cleanses — ["*"] means all)
SANITIZERS = [
    # Type coercion — safe for all categories
    ("int(", ["*"]),
    ("float(", ["*"]),
    ("bool(", ["*"]),
    ("str(int(", ["*"]),
    # XSS sanitizers
    ("escape(", ["xss"]),
    ("html.escape(", ["xss"]),
    ("markupsafe.escape(", ["xss"]),
    ("bleach.clean(", ["xss"]),
    ("DOMPurify.sanitize(", ["xss"]),
    # RCE sanitizer
    ("shlex.quote(", ["rce"]),
    # Path traversal sanitizer
    ("os.path.basename(", ["path_traversal"]),
    ("secure_filename(", ["path_traversal"]),
]

# Regex patterns for JS/TS/Go taint flows (fallback for non-Python)
JS_TAINT_PATTERNS = [
    # req.body/query/params → SQL query (either order on the line)
    (r'(?:req|request)\.(?:body|query|params)\b.*?(?:query|execute)\s*\(',
     "sql_injection", "high", "Use parameterized queries"),
    (r'(?:query|execute)\s*\(.*?(?:req|request)\.(?:body|query|params)\b',
     "sql_injection", "high", "Use parameterized queries"),
    # location/document → innerHTML
    (r'(?:document\.location|window\.location|location\.(?:hash|search)).*?innerHTML',
     "xss", "high", "Use textContent or sanitize with DOMPurify"),
    # req → exec/eval
    (r'(?:req|request)\.(?:body|query|params)\b.*?\b(?:eval|exec)\s*\(',
     "rce", "critical", "Never eval/exec user input"),
]

GO_TAINT_PATTERNS = [
    # r.FormValue/URL.Query → db.Query/Exec
    (r'(?:FormValue|URL\.Query)\b.*?(?:db\.(?:Query|Exec)|\.Query|\.Exec)\s*\(',
     "sql_injection", "high", "Use parameterized queries with $1 placeholders"),
    # r.FormValue → exec.Command
    (r'(?:FormValue|URL\.Query)\b.*?exec\.Command\s*\(',
     "rce", "critical", "Validate and whitelist command arguments"),
]
