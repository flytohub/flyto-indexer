package main

import "net/http"

func health() {
	http.Get("https://example.invalid/health")
}
