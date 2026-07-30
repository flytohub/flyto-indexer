package main

import (
	"os/exec"

	"github.com/gin-gonic/gin"
)

func run(c *gin.Context) {
	command := c.Query("command")
	exec.Command("sh", "-c", command)
}
