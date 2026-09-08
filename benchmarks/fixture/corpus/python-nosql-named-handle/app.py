def lookup(request, db):
    name = request.args.get("name")
    return db.customers.find({"name": name})
