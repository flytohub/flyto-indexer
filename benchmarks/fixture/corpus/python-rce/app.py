import os


def run_command(request):
    command = request.args.get("command")
    os.system(command)
