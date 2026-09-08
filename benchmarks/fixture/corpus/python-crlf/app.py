def trace(request, response):
    incoming = request.headers.get("X-Trace-ID")
    response.headers["X-Trace-ID"] = incoming
    return response
