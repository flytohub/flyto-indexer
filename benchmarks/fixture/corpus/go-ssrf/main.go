package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

func proxy(c *gin.Context) {
	http.Get(c.Query("url"))
}
