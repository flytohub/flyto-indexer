// KNOWN LIMITATION — UNSAFE_INNERHTML fires on any `.innerHTML =`.
// DOMPurify-sanitised or constant-content usage would still trigger.
// Fixing this needs intent-aware analysis (recognise sanitizer wraps /
// string literals); until then it's a published tradeoff, so we keep
// the safe examples as comments rather than active code that would
// produce "noise" in the matrix.
//   el.innerHTML = DOMPurify.sanitize(html)
//   el.innerHTML = '<b>Loading…</b>'

// Logging that's NOT sensitive — should NOT fire LOG_SENSITIVE
console.log('request received')
console.log(`user id=${userId}`)

// Session cookie WITH httpOnly — should NOT fire cookie rules
app.use(session({ cookie: { httpOnly: true, secure: true } }))
