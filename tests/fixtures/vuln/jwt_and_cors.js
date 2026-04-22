// JWT + CORS rules.
const jwt = require('jsonwebtoken')
const cors = require('cors')

// JWT alg 'none' — signing
const token1 = jwt.sign({ user: 'admin' }, null, { algorithm: 'none' }) // JWT_ALG_NONE

// JWT alg 'none' — verifying
const claims = jwt.verify(tok, key, { algorithms: ['none', 'HS256'] }) // JWT_ALG_NONE

// Hardcoded short secret
const t = jwt.sign({ id: 1 }, 'secret') // JWT_HARDCODED_SECRET

// CORS wildcard + credentials (worst combo)
app.use(cors({ origin: '*', credentials: true })) // CORS_WILDCARD_CREDENTIALS_JS

// Open redirect — req.query directly to res.redirect
app.get('/go', (req, res) => {
  res.redirect(req.query.next) // OPEN_REDIRECT_UNVALIDATED_JS
})

// Mass assignment — req.body straight into update
app.patch('/user/:id', (req, res) => {
  User.findOneAndUpdate({ _id: req.params.id }, req.body) // MASS_ASSIGN_UPDATE
})

// Express error leaking
app.use((err, req, res, next) => {
  res.send(err.stack) // EXPRESS_ERROR_HANDLER_MISSING
})
