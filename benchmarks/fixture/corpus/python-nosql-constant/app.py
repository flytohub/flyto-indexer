def lookup(collection):
    return collection.find({"name": "static"})
