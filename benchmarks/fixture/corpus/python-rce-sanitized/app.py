import shlex
import subprocess


def run_command(request):
    argv = shlex.split(request.args.get("command"))
    subprocess.run(argv, shell=False, check=True)
