app.get("/proxy", async (req, res) => { const upstream = await axios.get(req.query.url); res.send(upstream.data) })
