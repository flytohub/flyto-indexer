app.post("/settings", (req, res) => {
  const settings = Object.assign({}, req.body)
  res.json(settings)
})
