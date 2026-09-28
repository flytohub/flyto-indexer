app.get("/user", async (req, res) => { const rows = await db.query("SELECT * FROM users WHERE id=$1", [req.query.id]); res.json(rows) })
