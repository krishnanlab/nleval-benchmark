import warnings
from math import ceil

import hydra
import lightning.pytorch as pl
import nleval
import numpy as np
from nleval.dataset_pyg import OpenBiomedNetBench
from omegaconf import DictConfig, OmegaConf

from obnbench.data_module import DataModule
from obnbench.model import ModelModule
from obnbench.preprocess import precompute_features, infer_dimensions
from obnbench.utils import get_num_workers


def setup_configs(cfg: DictConfig):
    # Resolve workers
    cfg.num_workers = get_num_workers(cfg.num_workers)

    # Combine name with name_tag
    if cfg.name_tag is not None:
        if cfg.name is None:
            raise ValueError(
                f"cfg.name_tag can only be set ({cfg.name_tag}) when "
                "cfg.name is set.",
            )
        cfg.name = f"{cfg.name}-{cfg.name_tag}"


def setup_loggers(cfg: DictConfig):
    # Set local logger level
    nleval.logger.setLevel(cfg.log_level)

    # Set up Lightning loggers
    loggers = []
    if cfg.wandb.use:
        wandb_logger = pl.loggers.WandbLogger(
            name=cfg.wandb.name,
            project=cfg.wandb.project,
            entity=cfg.wandb.entity,
            save_dir=cfg.paths.runtime_dir,
            group=cfg.group,
        )
        loggers.append(wandb_logger)
    if cfg.save_results:
        csv_logger = pl.loggers.CSVLogger(
            save_dir=cfg.paths.result_dir,
            name=cfg.name,
            version=f"run_{cfg.seed}",
        )
        loggers.append(csv_logger)
    if not loggers:
        raise ValueError(
            "At least one of the following must be set to true to properly "
            "set up the logger: cfg.save_results, cfg.wandb.use",
        )

    return loggers


def setup_callbacks(cfg: DictConfig):
    lr_monitor = pl.callbacks.LearningRateMonitor(
        logging_interval="epoch",
        log_momentum=True,
    )
    ckpt = pl.callbacks.ModelCheckpoint(
        dirpath=None,  # use default set by Trainer's default_root_dir
        monitor=f"val/{cfg.metric.best}",
        verbose=True,
        save_last=True,
        save_top_k=5,
        mode=cfg.metric.obj,
        every_n_epochs=cfg.trainer.eval_interval,
    )
    early_stopping = pl.callbacks.EarlyStopping(
        monitor=f"val/{cfg.metric.best}",
        verbose=False,
        patience=ceil(cfg.trainer.early_stopping_patience / cfg.trainer.eval_interval),
        mode=cfg.metric.obj,
        check_finite=True,
    )
    return [lr_monitor, ckpt, early_stopping]


def _patch_fix_scale_edge_weights(dataset, g):
    if (edge_weight := dataset._data.edge_weight) is not None:
        if (min_edge_weight := edge_weight.min().item()) < 0:
            raise ValueError(
                "Negative edge weights not supported yet, "
                f"{min_edge_weight=}",
            )

        if (max_edge_weight := edge_weight.max().item()) > 1:
            warnings.warn(
                f"Max edge weight = {max_edge_weight:.2f}. Rsaling edge "
                "weights to [0, 1].\nThis will be patched in the near future.",
                RuntimeWarning,
                stacklevel=2,
            )

            dataset._data.edge_weight /= max_edge_weight
            for edge_weight_dict in g._edge_data:
                for idx in edge_weight_dict:
                    edge_weight_dict[idx] /= max_edge_weight


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig):
    setup_configs(cfg)
    loggers = setup_loggers(cfg)
    pl.seed_everything(cfg.seed)
    nleval.logger.info(f"Run configs:\n{OmegaConf.to_yaml(cfg, resolve=True)}")

    # Load data
    data_dir = cfg.paths.dataset_dir
    gene_list = np.loadtxt(cfg.paths.gene_list_path, dtype=str).tolist()
    dataset = OpenBiomedNetBench(
        data_dir,
        cfg.dataset.network,
        cfg.dataset.label,
        selected_genes=gene_list,
    )
    nleval.logger.info(f"Dataset loaded: {dataset}\n{dataset._data}")

    # Preprocessing
    g = getattr(nleval.data, cfg.dataset.network)(data_dir, log_level="WARNING")
    _patch_fix_scale_edge_weights(dataset, g)
    precompute_features(cfg, dataset, g)
    infer_dimensions(cfg, dataset)
    nleval.logger.info(f"Processed data:\n{dataset._data}")

    # Set up model
    model = ModelModule(cfg, node_ids=dataset.node_ids, task_ids=dataset.task_ids)
    nleval.logger.info(f"Model constructed:\n{model}")

    # Set up data module and trainer
    data = DataModule(dataset, num_workers=cfg.num_workers, pin_memory=True)
    callbacks = setup_callbacks(cfg)
    trainer = pl.Trainer(
        accelerator=cfg.trainer.accelerator,
        devices=cfg.trainer.devices,
        max_epochs=cfg.trainer.max_epochs,
        check_val_every_n_epoch=cfg.trainer.eval_interval,
        fast_dev_run=cfg.trainer.fast_dev_run,
        gradient_clip_val=cfg.trainer.gradient_clip_val,
        logger=loggers,
        callbacks=callbacks,
        enable_progress_bar=True,
        log_every_n_steps=1,  # full-batch training
    )

    # Train and evaluate model
    with warnings.catch_warnings():
        warnings.simplefilter("once")

        if not cfg.trainer.inference_only:
            trainer.fit(model, datamodule=data)
            ckpt = "best"
        else:
            ckpt = None

        trainer.test(model, datamodule=data, verbose=True, ckpt_path=ckpt)
        model.log_final_results()


if __name__ == "__main__":
    main()
