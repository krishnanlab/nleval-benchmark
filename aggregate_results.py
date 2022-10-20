import argparse
import logging
import os
import os.path as osp
from glob import glob
from pprint import pformat
from typing import List, Tuple

import pandas as pd
from tqdm import tqdm
from nleval.util.logger import get_logger

import config


def parse_args() -> Tuple[argparse.Namespace, logging.Logger]:
    global logger

    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--mode", required=True, choices=["main", "hp_tune"],
                        help="'main' and 'hp' for main and hyperparameter tuning experiments.")
    parser.add_argument("-p", "--results_path", default="auto",
                        help="Path to the results directory, infer from 'mode' if set to 'auto'.")
    parser.add_argument("-n", "--dry_run", action="store_true",
                        help="Agggregate and print results, but do not save to disk.")
    parser.add_argument("-o", "--output_path", default="aggregated_results/")
    parser.add_argument("-v", "--log_level", type=str, default="INFO")
    parser.add_argument("--methods", type=str, nargs="+", default=config.ALL_METHODS,
                        help="List of methods to consider when aggregating results.")

    # Parse arguments from command line and set up logger
    args = parser.parse_args()
    logger = get_logger(None, log_level=args.log_level)
    logger.info(f"Settings:\n{pformat(vars(args))}")

    return args


def _agg_main_results(
    results_path: str,
    target_methods: List[str],
) -> pd.DataFrame:
    df_lst = []
    target_methods_lower = list(map(str.lower, target_methods))
    for path in tqdm(glob(osp.join(results_path, "*.json"))):
        terms = osp.splitext(osp.split(path)[1])[0].split("_")  # network, label, method, runid
        if terms[2] not in target_methods_lower:
            logger.warning(f"Skipping {terms[2]}: {path}")

        df_lst.append(pd.read_json(path))
        df_lst[-1][["network", "label", "method", "runid"]] = terms
    return pd.concat(df_lst)


def _agg_hp_results(
    results_path: str,
    target_methods: List[str],
    target_file: str = "score.json",
) -> pd.DataFrame:
    df_lst = []
    for dir_, _, files in tqdm(list(os.walk(results_path))):
        if target_file in files:
            terms = dir_.split(osp.sep)[-4:]  # method, settings, dataset (netowkr-label), runid
            if terms[0] not in target_methods:
                logger.debug(f"Skipping {terms[0]}: {dir_}")
                continue

            path = osp.join(dir_, target_file)
            df_lst.append(pd.read_json(path))
            df_lst[-1][["method", "settings", "dataset", "runid"]] = terms
    return pd.concat(df_lst)


def main():
    args = parse_args()
    dry_run = args.dry_run
    methods = args.methods
    mode = args.mode
    output_path = args.output_path
    results_path = args.results_path

    # Get results path
    if results_path == "auto":
        results_path = "results" if mode == "main" else "hp_tune_results"
    logger.info(f"Raw results path to aggregate from: {results_path}")

    # Get aggregation function
    agg_func = _agg_main_results if mode == "main" else _agg_hp_results

    # Aggregate results
    logger.info(f"Start aggregating results for methods: {methods}")
    results_df = agg_func(results_path, methods)
    logger.info(f"Aggregated results:\n{results_df}")

    # Save or print results
    os.makedirs(output_path, exist_ok=True)
    path = osp.join(output_path, f"{mode}_results.csv")
    if dry_run:
        logger.info(f"Results will be saved to {path}")
    else:
        # Save single precision
        results_df.astype("float32", errors="ignore").to_csv(path, index=False)
        logger.info(f"Results saved to {path}")


if __name__ == "__main__":
    main()
