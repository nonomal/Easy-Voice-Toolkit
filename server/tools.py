import os
import PyEasyUtils as EasyUtils
from typing import Union, Optional

##############################################################################################################################

def mkPyCommand(fileDir, *commands):
    return [
        'cd "%s"' % fileDir,
        'python -c "%s"' % ';'.join(EasyUtils.toIterable(commands))
    ]

##############################################################################################################################

class AudioProcessor:
    def __init__(self, toolDir):
        self.toolDir = toolDir

    async def processAudio(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from AudioProcessor.process import Audio_Processing',
                f'AudioConvertandSlice = Audio_Processing({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
                'AudioConvertandSlice.processAudio()',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    def terminate(self):
        for subprocess in self.spm.subprocesses:
            subprocess.terminate()


class VPR:
    def __init__(self, toolDir):
        self.toolDir = toolDir

    async def infer(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from VPR.infer import Voice_Contrasting',
                f'AudioContrastInference = Voice_Contrasting({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
                'AudioContrastInference.getModel()',
                'AudioContrastInference.inference()',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    def terminate(self):
        for subprocess in self.spm.subprocesses:
            subprocess.terminate()


class Whisper:
    def __init__(self, toolDir):
        self.toolDir = toolDir

    async def infer(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from Whisper.transcribe import Voice_Transcribing',
                f'WAVtoSRT = Voice_Transcribing({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
                'WAVtoSRT.transcribe()',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    def terminate(self):
        for subprocess in self.spm.subprocesses:
            subprocess.terminate()


class GPT_SoVITS:
    def __init__(self, toolDir):
        self.toolDir = toolDir

    async def preprocess(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from GPT_SoVITS.preprocess import Dataset_Creating',
                f'SRTtoCSVandSplitAudio = Dataset_Creating({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
                'SRTtoCSVandSplitAudio.run()',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    async def train(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from GPT_SoVITS.train import train',
                f'train({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    async def infer_webui(self,
        **kwargs
    ):
        self.spm = EasyUtils.asyncSubprocessManager(shell = True)
        await self.spm.create(
            args = mkPyCommand(
                self.toolDir,
                'from GPT_SoVITS.infer_webui import infer',
                f'infer({",".join(f"{k}={v!r}" for k, v in kwargs.items())})',
            ),
            env = os.environ
        )
        subprocessMonitor = self.spm.monitor()
        async for outputLine, errorLine in subprocessMonitor:
            yield outputLine.decode(self.spm.encoding, errors = 'replace')

    def terminate(self):
        for subprocess in self.spm.subprocesses:
            subprocess.terminate()

##############################################################################################################################