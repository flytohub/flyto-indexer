package main

import "net/http"

func leave(w http.ResponseWriter, r *http.Request) {
	http.Redirect(w, r, r.FormValue("next"), http.StatusFound)
}
