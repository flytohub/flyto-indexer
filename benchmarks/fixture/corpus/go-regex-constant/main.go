package main

import "regexp"

var slugPattern = regexp.MustCompile(`^[a-z0-9-]+$`)
