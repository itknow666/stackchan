"""Install/remove a current-user logon shortcut. Never runs the assistant now."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "remove"))
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "local-config.json")
    args = parser.parse_args()
    if os.name != "nt":
        parser.exit(2, "Startup installation requires Windows.\n")
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/DaniaPcAssistant.vbs"
    if args.operation == "remove":
        startup.unlink(missing_ok=True)
        print("Current-user startup entry removed; stop any running assistant separately.")
        return
    config = args.config.resolve()
    if not config.is_file():
        parser.exit(2, "Create your private configuration first.\n")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        parser.exit(2, "Use the Windows Python virtual environment.\n")
    command = subprocess.list2cmdline([str(pythonw), str(Path(__file__).with_name("run.py").resolve()),
                                      "--config", str(config)])
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text('Set sh = CreateObject("WScript.Shell")\nsh.Run "' + command.replace('"', '""') +
                       '", 0, False\n', encoding="utf-16")
    print("Current-user logon startup installed. The assistant has not been started.")


if __name__ == "__main__":
    main()
