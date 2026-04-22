// Rule coverage — each tagged line expects the named rule to fire.

function lookup(db, userId) {
  return db.query(`SELECT * FROM users WHERE id = ${userId}`) // SQL_JS_TEMPLATE
}

function render(el, data) {
  el.innerHTML = data // UNSAFE_INNERHTML
}

function Comp({ bio }) {
  return (<div dangerouslySetInnerHTML={{ __html: bio }} />) // UNSAFE_DANGEROUSLY_SET
}

// Explicit bad cookie flags (positive indicator)
app.use(session({ cookie: { httpOnly: false } })) // COOKIE_HTTPONLY_FALSE
app.use(session({ cookie: { secure: false } })) // COOKIE_SECURE_FALSE

// SameSite: 'None' without any guard
app.use(session({ cookie: { SameSite: 'None' } })) // COOKIE_SAMESITE_NONE

console.log(`user token=${token} password=${password}`) // LOG_SENSITIVE
