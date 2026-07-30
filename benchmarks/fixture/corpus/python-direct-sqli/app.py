def handle_request(request):
    user_id = request.args.get("id")
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")  # noqa: F821
