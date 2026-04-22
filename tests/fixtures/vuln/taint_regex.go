// Single-line source → sink fixtures exercising GO_TAINT_PATTERNS.
package main

import (
	"encoding/xml"
	"net/http"
	"regexp"
	"github.com/gin-gonic/gin"
)

// SSRF via Gin source
func ssrf(c *gin.Context) { http.Get(c.Query("u")) }

// Open redirect
func openRed(c *gin.Context, w http.ResponseWriter, r *http.Request) {
	http.Redirect(w, r, c.Query("next"), http.StatusFound)
}

// CRLF — Gin source into header
func crlf(c *gin.Context, w http.ResponseWriter) { w.Header().Set("X-User", c.Query("name")) }

// ReDoS — regex compile with user input
func redos(c *gin.Context) { regexp.Compile(c.Query("pat")) }

// SQLi — Gin source into db.Query
func sqli(c *gin.Context, db *DB) { db.Query("SELECT * FROM t WHERE id=" + c.Query("id")) }

// XXE — user body into xml.NewDecoder
func xxe(r *http.Request) { xml.NewDecoder(r.Body).Decode(nil) }
