app.get("/leave", (req, res) => {
  res.redirect(req.query.target)
})
