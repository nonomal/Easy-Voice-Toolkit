'''
Edited
'''

from typing import Optional
from datetime import datetime
import os
import re
import json
import argparse
import platform
import logging
logging.getLogger('numba').setLevel(logging.WARNING)
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
import torch.multiprocessing as mp
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.cuda.amp import autocast, GradScaler
torch.backends.cudnn.benchmark = True

import Tool_VoiceTrainer.vits.text as text
import Tool_VoiceTrainer.vits.Utils as utils
from .vits.Data_Utils import (
    TextAudioSpeakerLoader,
    TextAudioSpeakerCollate,
    DistributedBucketSampler
)
from .vits.Models import (
    SynthesizerTrn,
    MultiPeriodDiscriminator
)
from .vits.Mel_Processing import (
    mel_spectrogram_torch,
    spec_to_mel_torch
)
from .vits.Commons import (
    slice_segments,
    clip_grad_value_
)
from .vits.Losses import (
    generator_loss,
    discriminator_loss,
    feature_loss,
    kl_loss
)
from .vits.text.symbols import symbols


global_step = 0


class Preprocessing:
    '''
    Preprocess
    '''
    def __init__(self,
        FileList_Path_Validation: str,
        FileList_Path_Training: str,
        Language: str = 'mandarin_english',
        Config_Path_Load: Optional[str] = None,
        Config_Dir_Save: str = './',
        Set_Eval_Interval: int = 1000,
        Set_Epochs: int = 10000,
        Set_Batch_Size: int = 16,
        Set_FP16_Run: bool = True,
        Set_Speakers: Optional[list] = ["SpeakerName"]
    ):
        self.FileList_Path_Validation = FileList_Path_Validation
        self.FileList_Path_Training = FileList_Path_Training
        self.Language = Language
        self.Config_Dir_Save = Config_Dir_Save
        self.Set_Eval_Interval = Set_Eval_Interval
        self.Set_Epochs = Set_Epochs
        self.Set_Batch_Size = Set_Batch_Size
        self.Set_FP16_Run = Set_FP16_Run
        self.Set_Speakers = Set_Speakers

        self.Config_Path_Load = Config_Path_Load if Config_Path_Load != None else os.path.normpath(os.path.join(os.path.dirname(__file__), './configs', (self.Language + '_base.json')))
        self.Config_Path_Edited = os.path.normpath(os.path.join(Config_Dir_Save, (self.Language + f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json")))
        self.Out_Extension = "cleaned"

    def Configurator(self):
        '''
        Edit JSON file
        '''
        def Get_Speakers(Text_Path_Training, Text_Path_Validation):
            Speakers = []
            for Text_Path in [Text_Path_Training, Text_Path_Validation]:
                with open(file = Text_Path, mode = 'r', encoding = 'utf-8') as File:
                    Lines = File.readlines()
                for _, Line in enumerate(Lines):
                    Line_Path = Line.split('|')[0]
                    Speaker = re.split(r'[\[\]]', re.split(r'[/\\\\]', Line_Path)[-1])[1]
                    Speakers.append(Speaker) if Speaker not in Speakers else None
            return Speakers

        def Write_Config_Data(Config_Path_Load, Config_Dir_Save):
            os.makedirs(Config_Dir_Save, exist_ok = True)
            with open(Config_Path_Load, 'rb') as File_Old:
                Params = json.load(File_Old)
            try:
                Params_Old = Params
                Params_Old["train"]["eval_interval"]    = self.Set_Eval_Interval
                Params_Old["train"]["epochs"]           = self.Set_Epochs
                Params_Old["train"]["batch_size"]       = self.Set_Batch_Size
                Params_Old["train"]["fp16_run"]         = self.Set_FP16_Run
                Params_Old["data"]["training_files"]    = os.path.normpath(self.FileList_Path_Training + "." + self.Out_Extension)
                Params_Old["data"]["validation_files"]  = os.path.normpath(self.FileList_Path_Validation + "." + self.Out_Extension)
                Params_Old["data"]["text_cleaners"]     = [(self.Language + "_cleaners").lower()]
                Params_Old["data"]["n_speakers"]        = len(Get_Speakers(self.FileList_Path_Training, self.FileList_Path_Validation)) if self.Set_Speakers == None else len(self.Set_Speakers)
                Params_Old["speakers"]                  = Get_Speakers(self.FileList_Path_Training, self.FileList_Path_Validation) if self.Set_Speakers == None else self.Set_Speakers
                Params_New = Params_Old
            except:
                raise Exception("Please check if params exist")
            with open(self.Config_Path_Edited, 'w') as File_New:
                json.dump(Params_New, File_New, indent = 4)
            print(f"Config created in {Config_Dir_Save}")

        Write_Config_Data(self.Config_Path_Load, self.Config_Dir_Save)

    def Cleaner(self):
        '''
        Convert natural language text to symbols
        '''
        Parser = argparse.ArgumentParser()
        Parser.add_argument("--Out_Extension",    type = str,                       default = self.Out_Extension)
        Parser.add_argument("--Path_Index",       type = int,                       default = 0)
        Parser.add_argument("--Text_Index",       type = int,                       default = 2)
        Parser.add_argument("--FileLists",        type = list,     nargs = "+",     default = [self.FileList_Path_Validation, self.FileList_Path_Training])
        Parser.add_argument("--Text_Cleaners",    type = str,      nargs = "+",     default = [self.Language + "_cleaners"])
        Args = Parser.parse_args(args = [])

        for FileList in Args.FileLists:
            print("START:", FileList)
            
            Path_SID_Text = utils.load_audiopaths_sid_text(FileList)
            for i in range(len(Path_SID_Text)):
                Path_SID_Text[i][Args.Text_Index] = text._clean_text(Path_SID_Text[i][Args.Text_Index], Args.Text_Cleaners)

            Filelist_Cleaned = FileList + "." + Args.Out_Extension
            with open(Filelist_Cleaned, "w", encoding = "utf-8") as f:
                f.writelines(["|".join(x) + "\n" for x in Path_SID_Text])


class Training:
    '''
    Train
    '''
    def __init__(self,
        Num_Workers: int = 4,
        Find_Unused_Parameters: bool = False,
        Model_Path_Pretrained_G: Optional[str] = None,
        Model_Path_Pretrained_D: Optional[str] = None
    ):
        self.Num_Workers = Num_Workers
        self.Find_Unused_Parameters = Find_Unused_Parameters
        self.Model_Path_Pretrained_G = Model_Path_Pretrained_G
        self.Model_Path_Pretrained_D = Model_Path_Pretrained_D

        self.CheckIfContinue = True

    def evaluate(self, hps, generator, eval_loader, writer_eval):
        generator.eval()
        with torch.no_grad():
            for batch_idx, (x, x_lengths, spec, spec_lengths, y, y_lengths, speakers) in enumerate(eval_loader):
                x, x_lengths = x.cuda(0), x_lengths.cuda(0)
                spec, spec_lengths = spec.cuda(0), spec_lengths.cuda(0)
                y, y_lengths = y.cuda(0), y_lengths.cuda(0)
                speakers = speakers.cuda(0)

                # remove else
                x = x[:1]
                x_lengths = x_lengths[:1]
                spec = spec[:1]
                spec_lengths = spec_lengths[:1]
                y = y[:1]
                y_lengths = y_lengths[:1]
                speakers = speakers[:1]
                break
            y_hat, attn, mask, *_ = generator.module.infer(x, x_lengths, speakers, max_len=1000)
            y_hat_lengths = mask.sum([1,2]).long() * hps.data.hop_length

            mel = spec_to_mel_torch(
                spec,
                hps.data.filter_length,
                hps.data.n_mel_channels,
                hps.data.sampling_rate,
                hps.data.mel_fmin,
                hps.data.mel_fmax
            )
            y_hat_mel = mel_spectrogram_torch(
                y_hat.squeeze(1).float(),
                hps.data.filter_length,
                hps.data.n_mel_channels,
                hps.data.sampling_rate,
                hps.data.hop_length,
                hps.data.win_length,
                hps.data.mel_fmin,
                hps.data.mel_fmax
            )
        image_dict = {
            "gen/mel": utils.plot_spectrogram_to_numpy(y_hat_mel[0].cpu().numpy())
        }
        audio_dict = {
            "gen/audio": y_hat[0,:,:y_hat_lengths[0]]
        }
        if global_step == 0:
            image_dict.update({"gt/mel": utils.plot_spectrogram_to_numpy(mel[0].cpu().numpy())})
            audio_dict.update({"gt/audio": y[0,:,:y_lengths[0]]})

        utils.summarize(
            writer=writer_eval,
            global_step=global_step,
            images=image_dict,
            audios=audio_dict,
            audio_sampling_rate=hps.data.sampling_rate
        )
        generator.train()

    def train_and_evaluate(self, rank, epoch, hps, nets, optims, schedulers, scaler, loaders, logger, writers):
        net_g, net_d = nets
        optim_g, optim_d = optims
        #scheduler_g, scheduler_d = schedulers
        train_loader, eval_loader = loaders
        if writers is not None:
            writer, writer_eval = writers

        train_loader.batch_sampler.set_epoch(epoch)
        global global_step

        net_g.train()
        net_d.train()
        
        for batch_idx, (x, x_lengths, spec, spec_lengths, y, y_lengths, speakers) in enumerate(train_loader):
            x, x_lengths = x.cuda(rank, non_blocking=True), x_lengths.cuda(rank, non_blocking=True)
            spec, spec_lengths = spec.cuda(rank, non_blocking=True), spec_lengths.cuda(rank, non_blocking=True)
            y, y_lengths = y.cuda(rank, non_blocking=True), y_lengths.cuda(rank, non_blocking=True)
            speakers = speakers.cuda(rank, non_blocking=True)

            with autocast(enabled=hps.train.fp16_run):
                y_hat, l_length, attn, ids_slice, x_mask, z_mask,\
                (z, z_p, m_p, logs_p, m_q, logs_q) = net_g(x, x_lengths, spec, spec_lengths, speakers)

                mel = spec_to_mel_torch(
                    spec,
                    hps.data.filter_length,
                    hps.data.n_mel_channels,
                    hps.data.sampling_rate,
                    hps.data.mel_fmin,
                    hps.data.mel_fmax)
                y_mel = slice_segments(mel, ids_slice, hps.train.segment_size // hps.data.hop_length)
                y_hat_mel = mel_spectrogram_torch(
                    y_hat.squeeze(1),
                    hps.data.filter_length,
                    hps.data.n_mel_channels,
                    hps.data.sampling_rate,
                    hps.data.hop_length,
                    hps.data.win_length,
                    hps.data.mel_fmin,
                    hps.data.mel_fmax)

                y = slice_segments(y, ids_slice * hps.data.hop_length, hps.train.segment_size) # slice

                # Discriminator
                y_d_hat_r, y_d_hat_g, _, _ = net_d(y, y_hat.detach())
                with autocast(enabled=False):
                    loss_disc, losses_disc_r, losses_disc_g = discriminator_loss(y_d_hat_r, y_d_hat_g)
                    loss_disc_all = loss_disc
            optim_d.zero_grad()
            scaler.scale(loss_disc_all).backward()
            scaler.unscale_(optim_d)
            grad_norm_d = clip_grad_value_(net_d.parameters(), None)
            scaler.step(optim_d)

            with autocast(enabled=hps.train.fp16_run):
                # Generator
                y_d_hat_r, y_d_hat_g, fmap_r, fmap_g = net_d(y, y_hat)
                with autocast(enabled=False):
                    loss_dur = torch.sum(l_length.float())
                    loss_mel = F.l1_loss(y_mel, y_hat_mel) * hps.train.c_mel
                    loss_kl = kl_loss(z_p, logs_q, m_p, logs_p, z_mask) * hps.train.c_kl

                    loss_fm = feature_loss(fmap_r, fmap_g)
                    loss_gen, losses_gen = generator_loss(y_d_hat_g)
                    loss_gen_all = loss_gen + loss_fm + loss_mel + loss_dur + loss_kl
            optim_g.zero_grad()
            scaler.scale(loss_gen_all).backward()
            scaler.unscale_(optim_g)
            grad_norm_g = clip_grad_value_(net_g.parameters(), None)
            scaler.step(optim_g)
            scaler.update()

            if rank==0:
                if global_step % hps.train.log_interval == 0:
                    lr = optim_g.param_groups[0]['lr']
                    losses = [loss_disc, loss_gen, loss_fm, loss_mel, loss_dur, loss_kl]
                    logger.info('Train Epoch: {} [{:.0f}%]'.format(
                        epoch,
                        100. * batch_idx / len(train_loader)))
                    logger.info([x.item() for x in losses] + [global_step, lr])

                    scalar_dict = {"loss/g/total": loss_gen_all, "loss/d/total": loss_disc_all, "learning_rate": lr, "grad_norm_d": grad_norm_d, "grad_norm_g": grad_norm_g}
                    scalar_dict.update({"loss/g/fm": loss_fm, "loss/g/mel": loss_mel, "loss/g/dur": loss_dur, "loss/g/kl": loss_kl})

                    scalar_dict.update({"loss/g/{}".format(i): v for i, v in enumerate(losses_gen)})
                    scalar_dict.update({"loss/d_r/{}".format(i): v for i, v in enumerate(losses_disc_r)})
                    scalar_dict.update({"loss/d_g/{}".format(i): v for i, v in enumerate(losses_disc_g)})
                    image_dict = {
                        "slice/mel_org": utils.plot_spectrogram_to_numpy(y_mel[0].data.cpu().numpy()),
                        "slice/mel_gen": utils.plot_spectrogram_to_numpy(y_hat_mel[0].data.cpu().numpy()),
                        "all/mel": utils.plot_spectrogram_to_numpy(mel[0].data.cpu().numpy()),
                        "all/attn": utils.plot_alignment_to_numpy(attn[0,0].data.cpu().numpy())
                    }
                    utils.summarize(
                        writer=writer,
                        global_step=global_step,
                        images=image_dict,
                        scalars=scalar_dict)

                if global_step % hps.train.eval_interval == 0:
                    self.evaluate(hps, net_g, eval_loader, writer_eval)
                    utils.save_checkpoint(net_g, optim_g, hps.train.learning_rate, epoch, os.path.normpath(os.path.join(hps.model_dir, "G_{}.pth".format(global_step))))
                    utils.save_checkpoint(net_d, optim_d, hps.train.learning_rate, epoch, os.path.normpath(os.path.join(hps.model_dir, "D_{}.pth".format(global_step))))
                    old_g=os.path.normpath(os.path.join(hps.model_dir, "G_{}.pth".format(global_step-2000)))
                    old_d=os.path.normpath(os.path.join(hps.model_dir, "D_{}.pth".format(global_step-2000)))
                    if os.path.exists(old_g):
                        os.remove(old_g)
                    if os.path.exists(old_d):
                        os.remove(old_d)
            global_step += 1

        if rank == 0:
            logger.info('====> Epoch: {}'.format(epoch))

    def run(self, rank, n_gpus, hps):
        global global_step
        if rank == 0:
            logger = utils.get_logger(hps.model_dir)
            logger.info(hps)
            #utils.check_git_hash(hps.model_dir)
            writer = SummaryWriter(log_dir=hps.model_dir)
            writer_eval = SummaryWriter(log_dir=os.path.normpath(os.path.join(hps.model_dir, "eval")))

        dist.init_process_group(
            backend = 'gloo' if platform.system() == 'Windows' else 'nccl', # Windows不支持NCCL backend，故使用GLOO
            init_method = 'env://',
            world_size = n_gpus,
            rank = rank
        )

        torch.manual_seed(hps.train.seed)
        torch.cuda.set_device(rank)

        train_dataset = TextAudioSpeakerLoader(hps.data.training_files, hps.data)
        train_sampler = DistributedBucketSampler(
            train_dataset,
            hps.train.batch_size,
            [32,300,400,500,600,700,800,900,1000],
            num_replicas=n_gpus,
            rank=rank,
            shuffle=True)
        collate_fn = TextAudioSpeakerCollate()
        train_loader = DataLoader(
            train_dataset,
            num_workers=self.Num_Workers,
            shuffle=False,
            pin_memory=True,
            collate_fn=collate_fn,
            batch_sampler=train_sampler)
        if rank == 0:
            eval_dataset = TextAudioSpeakerLoader(hps.data.validation_files, hps.data)
            eval_loader = DataLoader(eval_dataset, num_workers=0, shuffle=False,
                batch_size=hps.train.batch_size, pin_memory=True,
                drop_last=False, collate_fn=collate_fn)

        net_g = SynthesizerTrn(
            len(symbols),
            hps.data.filter_length // 2 + 1,
            hps.train.segment_size // hps.data.hop_length,
            n_speakers=hps.data.n_speakers,
            **hps.model).cuda(rank)
        net_d = MultiPeriodDiscriminator(hps.model.use_spectral_norm).cuda(rank)
        optim_g = torch.optim.AdamW(
            net_g.parameters(),
            hps.train.learning_rate,
            betas=hps.train.betas,
            eps=hps.train.eps)
        optim_d = torch.optim.AdamW(
            net_d.parameters(),
            hps.train.learning_rate,
            betas=hps.train.betas,
            eps=hps.train.eps)
        net_g = DDP(net_g, device_ids = [rank], find_unused_parameters = self.Find_Unused_Parameters)
        net_d = DDP(net_d, device_ids = [rank], find_unused_parameters = self.Find_Unused_Parameters)

        try:
            if self.CheckIfContinue == True:
                if None not in (self.Model_Path_Pretrained_G, self.Model_Path_Pretrained_D):
                    _, _, _, epoch_str = utils.load_checkpoint(self.Model_Path_Pretrained_G, net_g, optim_g)
                    _, _, _, epoch_str = utils.load_checkpoint(self.Model_Path_Pretrained_D, net_d, optim_d)
                    print("Loaded from pretrained models")
                else:
                    _, _, _, epoch_str = utils.load_checkpoint(utils.latest_checkpoint_path(hps.model_dir, "G_*.pth"), net_g, optim_g)
                    _, _, _, epoch_str = utils.load_checkpoint(utils.latest_checkpoint_path(hps.model_dir, "D_*.pth"), net_d, optim_d)
                    print("Loaded from latest checkpoint")
                self.CheckIfContinue = False
            else:
                _, _, _, epoch_str = utils.load_checkpoint(utils.latest_checkpoint_path(hps.model_dir, "G_*.pth"), net_g, optim_g)
                _, _, _, epoch_str = utils.load_checkpoint(utils.latest_checkpoint_path(hps.model_dir, "D_*.pth"), net_d, optim_d)

            global_step = (epoch_str - 1) * len(train_loader) # > 0
            print(f"Continue from step {global_step}")

        except:
            epoch_str = 1
            global_step = 0
            print(f"Start from step 0")

        scheduler_g = torch.optim.lr_scheduler.ExponentialLR(optim_g, gamma=hps.train.lr_decay, last_epoch=epoch_str-2)
        scheduler_d = torch.optim.lr_scheduler.ExponentialLR(optim_d, gamma=hps.train.lr_decay, last_epoch=epoch_str-2)

        scaler = GradScaler(enabled=hps.train.fp16_run)

        for epoch in range(epoch_str, hps.train.epochs + 1):
            if rank==0:
                self.train_and_evaluate(rank, epoch, hps, [net_g, net_d], [optim_g, optim_d], [scheduler_g, scheduler_d], scaler, [train_loader, eval_loader], logger, [writer, writer_eval])
            else:
                self.train_and_evaluate(rank, epoch, hps, [net_g, net_d], [optim_g, optim_d], [scheduler_g, scheduler_d], scaler, [train_loader, None], None, None)
            scheduler_g.step()
            scheduler_d.step()


class Voice_Training(Preprocessing, Training):
    '''
    1. Preprocess
    2. Train
    '''
    def __init__(self,
        FileList_Path_Validation: str,
        FileList_Path_Training: str,
        Language: str = 'mandarin_english',
        Config_Path_Load: Optional[str] = None,
        Config_Dir_Save: str = './',
        Set_Eval_Interval: int = 1000,
        Set_Epochs: int = 10000,
        Set_Batch_Size: int = 16,
        Set_FP16_Run: bool = True,
        Set_Speakers: str = ["SpeakerName"],
        Num_Workers: int = 4,
        Find_Unused_Parameters: bool = False,
        Model_Path_Pretrained_G: Optional[str] = None,
        Model_Path_Pretrained_D: Optional[str] = None,
        Model_Dir_Save: str = './'
    ):
        Preprocessing.__init__(self, FileList_Path_Validation, FileList_Path_Training, Language, Config_Path_Load, Config_Dir_Save, Set_Eval_Interval, Set_Epochs, Set_Batch_Size, Set_FP16_Run, Set_Speakers)
        Training.__init__(self, Num_Workers, Find_Unused_Parameters, Model_Path_Pretrained_G, Model_Path_Pretrained_D)
        self.Model_Dir_Save = Model_Dir_Save

    def Preprocessing_and_Training(self):
        # Preprocess
        self.Configurator()
        self.Cleaner()

        # Train
        """Assume Single Node Multi GPUs Training Only"""
        assert torch.cuda.is_available(), "CPU training is not allowed."

        n_gpus = torch.cuda.device_count()
        os.environ['MASTER_ADDR'] = 'localhost'
        os.environ['MASTER_PORT'] = '8000'

        hps = utils.get_hparams(
            Config_Path = self.Config_Path_Edited,
            Model_Dir = self.Model_Dir_Save
        )
        mp.spawn(super().run, args = (n_gpus, hps,), nprocs = n_gpus)