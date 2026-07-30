import os
import shlex


def run_command(request):
    command = shlex.quote(request.args.get("command"))
    os.system(command)
