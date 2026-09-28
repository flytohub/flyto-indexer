app.get("/user", async (req, res) => { const rows = await db.query("SELECT * FROM users WHERE id=" + req.query.id); res.json(rows) })
