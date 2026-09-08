def trace(response):
    response.headers["X-Trace-ID"] = "static"
    return response
