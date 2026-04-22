// Single-line source → sink fixtures to exercise the JS regex fallback
// in analyzer/taint_rules.py (JS_TAINT_PATTERNS).

// SSRF
app.get('/p', async (req, res) => { const r = await axios.get(req.query.u); res.send(r.data) })

// Open redirect
app.get('/g', (req, res) => { res.redirect(req.query.next) })

// NoSQL injection
app.post('/s', async (req, res) => { const u = await Model.find(req.body); res.json(u) })

// CRLF / header injection
app.post('/h', (req, res) => { res.setHeader('X-User', req.body.name) })

// ReDoS — constructing RegExp from user input
app.get('/m', (req, res) => { const re = new RegExp(req.query.pat) })

// Prototype pollution
app.post('/merge', (req, res) => { Object.assign({}, req.body) })
