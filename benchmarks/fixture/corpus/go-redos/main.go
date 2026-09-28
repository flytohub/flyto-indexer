package main

import (
	"net/http"
	"regexp"
)

func find(r *http.Request) {
	regexp.MustCompile(r.FormValue("pattern"))
}
