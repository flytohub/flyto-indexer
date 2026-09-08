def lookup(request, collection):
    name = request.args.get("name")
    return collection.find({"name": name})
