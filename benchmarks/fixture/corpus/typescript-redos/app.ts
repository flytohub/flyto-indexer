app.get("/find", (req, res) => { const pattern = new RegExp(req.query.pattern); res.send(String(pattern)) })
