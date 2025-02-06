# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path
from subprocess import Popen

##############################################################################################################################

# Get current directory
currentDir = Path(sys.argv[0]).parent.as_posix()


isCompiled = False


def run(
    coreDir: str = ...,
    manifestPath: str = ...,
    requirementsPath: str = ...,
    dependencyDir: str = ...,
    modelDir: str = ...,
    outputDir: str = ...,
    profileDir: str = ...
):
    resourceDir = Path(sys._MEIPASS).as_posix() if getattr(sys, 'frozen', None) else currentDir
    clientDir = Path(f'{resourceDir}{os.sep}{"EVT_GUI" if not isCompiled else "EVT"}').as_posix()
    clientFile = Path(f'{clientDir}{os.sep}{f"src{os.sep}Main.py" if not isCompiled else "Main.exe"}').as_posix()
    clientCommand = f'{"python" if not isCompiled else ""} "{clientFile}" --core "{coreDir}" --manifest "{manifestPath}" --requirements "{requirementsPath}" --dependencies "{dependencyDir}" --models "{modelDir}" --output "{outputDir}" --profile "{profileDir}"'
    Popen(clientCommand.strip(), shell = True)

##############################################################################################################################

if __name__ == "__main__":
    run(
        coreDir = Path(currentDir).joinpath('EVT_Core').as_posix(),
        manifestPath = Path(currentDir).joinpath('manifest.json').as_posix(),
        requirementsPath = Path(currentDir).joinpath('requirements.txt').as_posix(),
        dependencyDir = Path(currentDir).as_posix(),
        modelDir = Path(currentDir).joinpath('Models').as_posix(),
        outputDir = Path(currentDir).as_posix(),
        profileDir = Path(currentDir).as_posix()
    )

##############################################################################################################################