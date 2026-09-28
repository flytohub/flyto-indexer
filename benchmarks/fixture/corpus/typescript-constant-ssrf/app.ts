app.get("/proxy", async (_req, res) => { const upstream = await axios.get("https://example.invalid/health"); res.send(upstream.data) })
