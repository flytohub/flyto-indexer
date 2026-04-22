// Safe JS/TS — should produce ZERO findings.
const crypto = require('crypto')
const bcrypt = require('bcrypt')
const cors = require('cors')

function strongHash(data) {
  return crypto.createHash('sha256').update(data).digest('hex')
}

function secureToken() {
  return crypto.randomBytes(32).toString('hex')
}

// Good bcrypt
const hashed = bcrypt.hashSync('password', 12)

// Locked-down CORS
app.use(cors({ origin: ['https://app.example.com'], credentials: true }))

// Session cookie with all flags
app.use(session({
  cookie: { httpOnly: true, secure: true, sameSite: 'Strict' },
}))

// TLS verification on (default)
const https = require('https')
const agent = new https.Agent({ rejectUnauthorized: true })

// JWT with proper secret + no 'none'
const jwt = require('jsonwebtoken')
const token = jwt.sign({ id: 1 }, process.env.JWT_SECRET, { algorithm: 'HS256' })
const claims = jwt.verify(tok, process.env.JWT_SECRET, { algorithms: ['HS256'] })

// No redirect to user input
app.get('/go', (req, res) => {
  const ok = new Set(['/home', '/account'])
  const t = req.query.next
  res.redirect(ok.has(t) ? t : '/')
})

// No mass assignment — explicit fields
app.patch('/user/:id', (req, res) => {
  const { displayName, email } = req.body
  User.findOneAndUpdate({ _id: req.params.id }, { displayName, email })
})

// Error handler doesn't leak
app.use((err, req, res, next) => {
  res.status(500).send('internal error')
})
