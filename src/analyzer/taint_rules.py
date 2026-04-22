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
        "request.GET",          # Django
        "request.POST",         # Django
        "request.META",         # Django
        "request.body",         # Django raw body
        "request.FILES",        # Django
        # FastAPI
        "Query(",
        "Body(",
        "Form(",
        "File(",
        "Header(",
        "Cookie(",
        "Path(",
        # Tornado
        "self.get_argument(",
        "self.get_arguments(",
        "self.get_cookie(",
        "self.request.body",
        "self.request.arguments",
        "self.request.headers",
        # aiohttp
        "request.match_info",
        "request.rel_url",
        "request.content",
        # Sanic
        "request.args",  # already covered but Sanic exposes same
        "request.raw_args",
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
        "req.url",
        "req.originalUrl",
        "req.hostname",
        # Koa
        "ctx.request.body",
        "ctx.request.query",
        "ctx.request.headers",
        "ctx.params",
        "ctx.query",
        # Fastify
        "request.body",
        "request.query",
        "request.params",
        "request.headers",
        # NestJS — @Body()/@Query()/@Param() decorators expand to these
        "@Body(",
        "@Query(",
        "@Param(",
        "@Headers(",
        # Hapi
        "request.payload",
        "request.params",
        # Next.js API routes
        "req.nextUrl",
        # Env / side channels
        "process.env",
        # Browser DOM
        "document.location",
        "window.location",
        "location.hash",
        "location.search",
        "document.cookie",
        # postMessage / storage
        "event.data",
        "localStorage.getItem(",
        "sessionStorage.getItem(",
    ],
    "go": [
        # net/http — core
        "r.URL.Query(",
        "r.FormValue(",
        "r.PostFormValue(",
        "r.Body",
        "r.Header.Get(",
        "r.PostForm",
        "r.MultipartForm",
        "r.URL.Path",
        "r.URL.RawQuery",
        # Gin
        "c.Query(",
        "c.DefaultQuery(",
        "c.Param(",
        "c.PostForm(",
        "c.DefaultPostForm(",
        "c.GetHeader(",
        "c.BindJSON(",
        "c.ShouldBindJSON(",
        "c.Bind(",
        "c.ShouldBind(",
        "c.FormFile(",
        # Echo
        "c.QueryParam(",
        "c.FormValue(",
        "c.Param(",
        "c.Request().Header.Get(",
        # Fiber
        "c.Query(",        # overlap with Gin — Fiber uses same
        "c.Params(",
        "c.Body(",
        "c.Cookies(",
        "c.BodyParser(",
        # Chi
        "chi.URLParam(",
    ],
    "java": [
        # Spring MVC
        "@RequestParam",
        "@PathVariable",
        "@RequestBody",
        "@RequestHeader",
        "@CookieValue",
        "request.getParameter(",
        "request.getHeader(",
        "request.getCookies(",
        "request.getQueryString(",
        "request.getInputStream(",
    ],
    "php": [
        "$_GET",
        "$_POST",
        "$_REQUEST",
        "$_COOKIE",
        "$_FILES",
        "$_SERVER",
        "file_get_contents(\"php://input\"",
    ],
    "ruby": [
        "params[",
        "request.params",
        "request.headers",
        "request.cookies",
        "cookies[",
        "session[",
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
    # --- batch 1: categories that SAST really should catch ----------------

    "ssrf": [
        # Python — any HTTP client fed a tainted URL
        ("requests.get(", "high", "Validate target against an allow-list; reject private/loopback ranges"),
        ("requests.post(", "high", "Validate target against an allow-list; reject private/loopback ranges"),
        ("requests.request(", "high", "Validate target against an allow-list"),
        ("httpx.get(", "high", "Validate target against an allow-list"),
        ("httpx.post(", "high", "Validate target against an allow-list"),
        ("urllib.request.urlopen(", "high", "Validate target; forbid file:// scheme"),
        ("urllib2.urlopen(", "high", "Validate target; forbid file:// scheme"),
        ("http.client.HTTPConnection(", "medium", "Validate host against an allow-list"),
        ("aiohttp.ClientSession(", "high", "Validate URL before request; reject private IPs"),
        # JS
        ("axios.get(", "high", "Validate target URL; block private IP ranges"),
        ("axios.post(", "high", "Validate target URL; block private IP ranges"),
        ("axios(", "high", "Validate target URL"),
        ("fetch(", "medium", "Validate URL; block internal services in server-side fetch"),
        ("node-fetch(", "high", "Validate URL before fetch"),
        ("got(", "high", "Validate URL; set timeout + followRedirect:false"),
        ("superagent.get(", "high", "Validate URL before request"),
        # Go
        ("http.Get(", "high", "Validate URL; refuse private IP ranges"),
        ("http.Post(", "high", "Validate URL; refuse private IP ranges"),
        ("client.Do(", "high", "Validate req.URL.Host before Do()"),
        ("http.NewRequest(", "medium", "Validate URL argument before use"),
    ],

    "open_redirect": [
        # Python
        ("redirect(", "medium", "Validate target is an internal path or allow-listed host"),
        ("flask.redirect(", "medium", "Validate target is internal before redirecting"),
        ("HttpResponseRedirect(", "medium", "Validate target URL before redirect"),
        # JS
        ("res.redirect(", "medium", "Validate target; reject absolute URLs pointing off-host"),
        ("response.redirect(", "medium", "Validate target before redirect"),
        ("window.location.replace(", "medium", "Don't source location from user input directly"),
        # Go
        ("http.Redirect(", "medium", "Validate URL before issuing redirect"),
    ],

    "xxe": [
        # Python — the classic XXE enablers
        ("etree.parse(", "high", "Use defusedxml.ElementTree; disable external entities"),
        ("etree.fromstring(", "high", "Use defusedxml; DTDs + external entities must be off"),
        ("xml.sax.parse(", "high", "Use defusedxml.sax; disable external entities"),
        ("xml.dom.minidom.parse(", "high", "Use defusedxml.minidom"),
        ("lxml.etree.parse(", "high", "Pass XMLParser(resolve_entities=False, no_network=True)"),
        ("lxml.etree.fromstring(", "high", "Pass XMLParser(resolve_entities=False, no_network=True)"),
        # JS
        ("libxmljs.parseXml(", "high", "Pass {noent: false, noblanks: true}; reject DTD"),
        ("xml2js.parseString(", "medium", "Disable entity expansion in parser options"),
    ],

    "ldap_injection": [
        # Python
        ("conn.search(", "high", "Escape filter with ldap.filter.escape_filter_chars()"),
        ("conn.search_s(", "high", "Escape filter with ldap.filter.escape_filter_chars()"),
        ("connection.search(", "high", "Escape filter with ldap3.utils.conv.escape_filter_chars()"),
        ("ldap.search(", "high", "Escape filter input; do not interpolate user values"),
        # JS
        ("ldap.search(", "high", "Use ldap-escape before building filters"),
        ("ldapjs.search(", "high", "Escape filter chars in ldap-escape"),
    ],

    "nosql_injection": [
        # Python + mongo
        ("collection.find(", "high", "Do not pass raw dicts from user input as query"),
        ("collection.find_one(", "high", "Validate query shape; reject $-keys from untrusted data"),
        ("collection.update(", "high", "Validate query + update documents before use"),
        ("collection.delete_many(", "high", "Validate query before bulk delete"),
        ("collection.aggregate(", "high", "Sanitize pipeline stages from user input"),
        # JS + mongoose/mongodb
        ("Model.find(", "high", "Reject $-prefixed keys; cast to expected schema first"),
        ("Model.findOne(", "high", "Reject $-prefixed keys; cast to expected schema first"),
        ("Model.updateOne(", "high", "Validate update document shape"),
        ("db.collection(", "medium", "Ensure downstream find/update sanitizes $-keys"),
    ],

    "crlf_injection": [
        # Python — header injection via newlines in user-supplied values
        ("response.headers[", "medium", "Strip CR/LF from header values; validate with regex"),
        ("res.setHeader(", "medium", "Strip CR/LF from header values"),
        ("set_cookie(", "medium", "Validate cookie name/value; strip CR/LF"),
        # Go
        ("w.Header().Set(", "medium", "Strip CR/LF from header values"),
        ("w.Header().Add(", "medium", "Strip CR/LF from header values"),
    ],

    "redos": [
        # User-controlled regex = ReDoS waiting to happen
        ("re.compile(", "medium", "Do not compile regex from user input; use exact match or allow-list"),
        ("re.search(", "low", "Do not search with user-supplied regex patterns"),
        ("new RegExp(", "medium", "Never construct RegExp from user input"),
        ("regexp.Compile(", "medium", "Never compile regex from user input"),
        ("regexp.MustCompile(", "low", "MustCompile panics on bad input; validate source"),
    ],

    "prototype_pollution": [
        # JS — the classic merge / assign with user input
        ("Object.assign(", "high", "Never merge user input into shared prototypes; clone first"),
        ("_.merge(", "high", "Use _.mergeWith + safe customizer; lodash.merge is vulnerable"),
        ("_.defaultsDeep(", "high", "Same concern as merge; switch to a safe cloner"),
        ("Object.setPrototypeOf(", "high", "Never call with user-controlled proto"),
        ("hoek.merge(", "high", "Legacy hoek.merge allows prototype pollution"),
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
    # --- batch 1 ---------------------------------------------------------
    # SSRF — req.* → axios/fetch/got with absolute URL
    (r'(?:req|request)\.(?:body|query|params)\b.*?(?:axios|fetch|got|node-fetch|superagent)\s*[.(]',
     "ssrf", "high", "Validate URL against allow-list; block private IPs"),
    # Open redirect — req → res.redirect
    (r'(?:req|request)\.(?:body|query|params)\b.*?res(?:ponse)?\.redirect\s*\(',
     "open_redirect", "medium", "Validate redirect target is internal or allow-listed"),
    # NoSQL injection — req → Model.find(query)
    (r'(?:req|request)\.(?:body|query|params)\b.*?\.(?:find|findOne|updateOne|deleteMany|aggregate)\s*\(',
     "nosql_injection", "high", "Cast query to schema; reject $-prefixed keys"),
    # CRLF / header injection
    (r'(?:req|request)\.(?:body|query|params|headers)\b.*?(?:setHeader|writeHead)\s*\(',
     "crlf_injection", "medium", "Strip CR/LF from header values"),
    # ReDoS — user input to RegExp constructor
    (r'new\s+RegExp\s*\(\s*(?:req|request)\.',
     "redos", "medium", "Never construct RegExp from user input"),
    # Prototype pollution — user body merged into object
    (r'(?:Object\.assign|_\.merge|_\.defaultsDeep)\s*\([^,]+,\s*(?:req|request)\.',
     "prototype_pollution", "high", "Never merge user input into shared objects"),
]

GO_TAINT_PATTERNS = [
    # r.FormValue/URL.Query → db.Query/Exec
    (r'(?:FormValue|URL\.Query)\b.*?(?:db\.(?:Query|Exec|QueryRow)|\.Query|\.Exec)\s*\(',
     "sql_injection", "high", "Use parameterized queries with $1 placeholders"),
    # Gin/Echo/Fiber sources → db.Query/Exec (common framework surface)
    (r'(?:c\.(?:Query|Param|PostForm|BindJSON|ShouldBindJSON|Bind|FormValue|GetHeader))\b.*?(?:db\.(?:Query|Exec|QueryRow)|\.Query|\.Exec)\s*\(',
     "sql_injection", "high", "Use parameterized queries with $1 placeholders"),
    # r.FormValue → exec.Command
    (r'(?:FormValue|URL\.Query)\b.*?exec\.Command\s*\(',
     "rce", "critical", "Validate and whitelist command arguments"),
    (r'(?:c\.(?:Query|Param|PostForm|BindJSON|FormValue))\b.*?exec\.Command\s*\(',
     "rce", "critical", "Validate and whitelist command arguments"),
    # r.FormValue → os.Open / filepath.Join without validation
    (r'(?:FormValue|URL\.Query)\b.*?(?:os\.Open|filepath\.Join)\s*\(',
     "path_traversal", "high", "Validate and sanitize file paths"),
    (r'(?:c\.(?:Query|Param|PostForm|FormValue))\b.*?(?:os\.Open|filepath\.Join)\s*\(',
     "path_traversal", "high", "Validate and sanitize file paths"),
    # --- batch 1 ---------------------------------------------------------
    # SSRF — user input to http.Get / client.Do
    (r'(?:FormValue|URL\.Query|c\.(?:Query|Param|PostForm|FormValue))\b.*?(?:http\.(?:Get|Post)|client\.Do|http\.NewRequest)\s*\(',
     "ssrf", "high", "Validate URL; reject private/loopback ranges"),
    # Open redirect
    (r'(?:FormValue|URL\.Query|c\.(?:Query|Param|FormValue))\b.*?http\.Redirect\s*\(',
     "open_redirect", "medium", "Validate target host is allow-listed"),
    # CRLF in headers
    (r'(?:FormValue|URL\.Query|c\.(?:Query|Param|FormValue|GetHeader))\b.*?\.Header\(\)\.(?:Set|Add)\s*\(',
     "crlf_injection", "medium", "Strip CR/LF from header values"),
    # ReDoS — user input to regexp.Compile
    (r'regexp\.(?:Compile|MustCompile)\s*\([^)]*\b(?:FormValue|URL\.Query|c\.(?:Query|Param|FormValue))\b',
     "redos", "medium", "Never compile regex from user input"),
    # XML XXE — user input to xml.NewDecoder
    (r'xml\.NewDecoder\s*\([^)]*\b(?:r\.Body|c\.Request\.Body)\b',
     "xxe", "high", "Disable external entities; use encoding/xml with care"),
]
