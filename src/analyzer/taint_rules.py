"""
Default taint analysis rules — sources, sinks, and sanitizers by language.
"""

# Sources: where untrusted data enters the application
SOURCES = {
    "python": [
        # Flask / Django / generic WSGI
        "request.args",
        "request.form",
        "request.json",
        "request.data",
        "request.cookies",
        "request.headers",
        "request.values",
        "request.files",
        # FastAPI
        "Query(",
        "Body(",
        "Form(",
        "File(",
        # stdlib
        "sys.argv",
        "os.environ",
        "input(",
    ],
    "javascript": [
        # Express / Koa / generic
        "req.body",
        "req.query",
        "req.params",
        "req.headers",
        "req.cookies",
        "process.env",
        # Browser DOM
        "document.location",
        "window.location",
        "location.hash",
        "location.search",
    ],
    "go": [
        "r.URL.Query(",
        "r.FormValue(",
        "r.Body",
        "r.Header.Get(",
        "r.PostForm",
        "r.MultipartForm",
    ],
}

# Sinks: dangerous functions that should not receive tainted data
# Each entry: (pattern, severity, recommendation)
SINKS = {
    "sql_injection": [
        ("cursor.execute", "critical", "Use parameterized query: cursor.execute(sql, params)"),
        ("db.execute", "critical", "Use parameterized query: db.execute(sql, params)"),
        ("session.execute", "critical", "Use parameterized query with bound parameters"),
        ("engine.execute", "critical", "Use parameterized query with bound parameters"),
        ("connection.execute", "critical", "Use parameterized query with bound parameters"),
        (".raw(", "critical", "Use parameterized query instead of raw SQL"),
        (".raw_sql(", "critical", "Use parameterized query instead of raw SQL"),
        ("RawSQL(", "critical", "Use parameterized query instead of raw SQL"),
        # Go
        ("db.Exec(", "critical", "Use parameterized query with $1 placeholders"),
        ("db.Query(", "critical", "Use parameterized query with $1 placeholders"),
        ("db.QueryRow(", "critical", "Use parameterized query with $1 placeholders"),
    ],
    "rce": [
        ("eval(", "critical", "Avoid eval() with user input; use ast.literal_eval() for data"),
        ("exec(", "critical", "Never exec() user-controlled strings"),
        ("os.system(", "critical", "Use subprocess.run() with a list of args, no shell=True"),
        ("os.popen(", "critical", "Use subprocess.run() with a list of args"),
        ("commands.getoutput(", "critical", "Use subprocess.run() with a list of args"),
        ("subprocess.run", "high", "Do not pass shell=True with user input; use arg list"),
        ("subprocess.call", "high", "Do not pass shell=True with user input; use arg list"),
        ("subprocess.Popen", "high", "Do not pass shell=True with user input; use arg list"),
        # Go
        ("exec.Command(", "high", "Validate and whitelist command arguments"),
    ],
    "xss": [
        ("render_template_string(", "high", "Use render_template() with auto-escaping"),
        ("Markup(", "medium", "Ensure input is sanitized before wrapping in Markup()"),
        (".innerHTML", "high", "Use textContent or sanitize with DOMPurify"),
        ("document.write(", "high", "Use textContent or DOM manipulation instead"),
        ("v-html", "medium", "Sanitize input before using v-html directive"),
    ],
    "path_traversal": [
        ("open(", "high", "Validate and sanitize file paths; use os.path.realpath()"),
        ("send_file(", "high", "Use safe_join() or validate paths against a whitelist"),
        ("os.path.join(", "medium", "Validate path components; use os.path.realpath() to resolve"),
        ("shutil.copy(", "medium", "Validate source and destination paths"),
        ("shutil.move(", "medium", "Validate source and destination paths"),
    ],
    "deserialization": [
        ("pickle.loads(", "critical", "Never unpickle untrusted data; use JSON instead"),
        ("pickle.load(", "critical", "Never unpickle untrusted data; use JSON instead"),
        ("yaml.load(", "high", "Use yaml.safe_load() instead of yaml.load()"),
        ("yaml.unsafe_load(", "critical", "Use yaml.safe_load() instead"),
        ("marshal.loads(", "critical", "Never unmarshal untrusted data"),
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
    ("shlex.split(", ["rce"]),
    # Path traversal sanitizer
    ("os.path.basename(", ["path_traversal"]),
    ("os.path.realpath(", ["path_traversal"]),
    ("secure_filename(", ["path_traversal"]),
    # SQL parameterization indicators (heuristic — these appear in safe patterns)
    ("parameterized", ["sql_injection"]),
    # Generic sanitization keywords (substring match)
    ("sanitize(", ["*"]),
    ("clean(", ["xss"]),
    ("validate(", ["*"]),
]

# Regex patterns for JS/TS/Go taint flows (fallback for non-Python)
JS_TAINT_PATTERNS = [
    # req.body/query/params → SQL query (either order on the line)
    (r'(?:req|request)\.(?:body|query|params)\b.*?(?:query|execute)\s*\(',
     "sql_injection", "high", "Use parameterized queries"),
    (r'(?:query|execute)\s*\(.*?(?:req|request)\.(?:body|query|params)\b',
     "sql_injection", "high", "Use parameterized queries"),
    # location/document → innerHTML / document.write
    (r'(?:document\.location|window\.location|location\.(?:hash|search)).*?innerHTML',
     "xss", "high", "Use textContent or sanitize with DOMPurify"),
    (r'(?:document\.location|window\.location|location\.(?:hash|search)).*?document\.write\s*\(',
     "xss", "high", "Use textContent instead of document.write"),
    # req → exec/eval
    (r'(?:req|request)\.(?:body|query|params)\b.*?\b(?:eval|exec)\s*\(',
     "rce", "critical", "Never eval/exec user input"),
    # req → child_process / exec
    (r'(?:req|request)\.(?:body|query|params)\b.*?\bexec(?:Sync)?\s*\(',
     "rce", "critical", "Never pass user input to child_process.exec"),
    # req.cookies → response (session fixation)
    (r'(?:req|request)\.cookies\b.*?(?:query|execute)\s*\(',
     "sql_injection", "high", "Never use raw cookie values in SQL"),
]

GO_TAINT_PATTERNS = [
    # r.FormValue/URL.Query → db.Query/Exec
    (r'(?:FormValue|URL\.Query)\b.*?(?:db\.(?:Query|Exec|QueryRow)|\.Query|\.Exec)\s*\(',
     "sql_injection", "high", "Use parameterized queries with $1 placeholders"),
    # r.FormValue → exec.Command
    (r'(?:FormValue|URL\.Query)\b.*?exec\.Command\s*\(',
     "rce", "critical", "Validate and whitelist command arguments"),
    # r.FormValue → os.Open / filepath.Join without validation
    (r'(?:FormValue|URL\.Query)\b.*?(?:os\.Open|filepath\.Join)\s*\(',
     "path_traversal", "high", "Validate and sanitize file paths"),
]
