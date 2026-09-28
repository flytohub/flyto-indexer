package main

import (
	"database/sql"
	"net/http"
)

func lookup(db *sql.DB, r *http.Request) {
	db.Query("SELECT * FROM users WHERE id=$1", r.FormValue("id"))
}
