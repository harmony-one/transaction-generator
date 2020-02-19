#!/usr/bin/env python3

import time
import sys
import logging
import datetime
import os
from multiprocessing.pool import ThreadPool

import harmony_transaction_generator as tx_gen
from harmony_transaction_generator import account_manager
from harmony_transaction_generator import analysis
import pyhmy
from pyhmy import cli
from pyhmy import util

import_keys = [
    "0336e9be71c31d71d086d9f0887d13cb6701bc45d11b70bb7c14200c9feebe22",
    "6213bea7aef783463e67ed0c476a2915339de01f30658e7bb88ef5861e64b5e5",
    "952cd39c0f8a12d6effd10a694591348025ced573e576e85907af457585bbd63",
    "873be090538a214fda813f900ea3e775c725f40ffb1e27803d5d55f0d329fe11",
    "f2e56cc743a9a4cccf7feb52ff9740917ba6e864b7d178a404dee4cd843d72e1",
    "2e975b050c43b2c5eaa5c1b0d60bec770bec4f6c4cef8868b8ff38dfae92898d",
    "23ceaf469b8f9ade25568d4a83261d644ef6207a1e65cd738356704148d65043",
    "e122d41822d93d7789e15de29569ce9dfc6adf4ff0ec80dd4a3d53b7246e553e"
]
verbose = True

tx_gen.set_config({
    "AMT_PER_TXN": [1e-9, 1e-6],  # The random range for each transaction in the transaction-generation
    "NUM_SRC_ACC": 64,  # The number of possible source accounts for all transactions
    "NUM_SNK_ACC": 1,  # The number of possible destination / sink accounts for all transaction
    "MAX_TXN_GEN_COUNT": None,  # The upper bound of the number generated transaction, regardless of if `stop` is called
    "ONLY_CROSS_SHARD": False,  # If true, forces source and destination shards to be different
    "ENFORCE_NONCE": False,  # If true, will only generate transactions with a valid nonce
    "ESTIMATED_GAS_PER_TXN": 1e-3,  # The estimated gas, hardcoded
    "INIT_SRC_ACC_BAL_PER_SHARD": 5,  # The initial balance for EVERY source account
    "TXN_WAIT_TO_CONFIRM": 60,  # The timeout when a transaction is sent (only used in setup related functions)
    "MAX_THREAD_COUNT": os.cpu_count() - 2,  # Max thread is recommended to be less than your v-core count
    "ENDPOINTS": [  # Endpoints for all transaction, index i = shard i
        "https://api.s0.b2.hmny.io",
        "https://api.s1.b2.hmny.io",
    ],
    "SRC_SHARD_WEIGHTS": [  # Adjust the likelihood that shard i (i = index) gets chosen to be the source shard
        1,                  # Bigger number = higher likelihood of shard i begin chosen
        1,                  # 0 = 0% chance of being chosen
    ],
    "SNK_SHARD_WEIGHTS": [  # Adjust the likelihood that shard i (i = index) gets chosen to be the source shard
        1,
        1,
    ],
    "CHAIN_ID": "testnet",  # The chain id for all transaction, should be devnet if not localnet.
    "REFUND_ACCOUNT": "one1j9hwh7vqz94dsk06q4h9hznr4wlr3x5zup6wz3",  # All refunds will be sent to this address
})


def setup():
    assert hasattr(pyhmy, "__version__")
    assert pyhmy.__version__.major == 20, "wrong pyhmy version"
    assert pyhmy.__version__.minor == 1, "wrong pyhmy version"
    assert pyhmy.__version__.micro >= 14, "wrong pyhmy version, update please"
    env = cli.download("./bin/hmy", replace=False)
    cli.environment.update(env)
    cli.set_binary("./bin/hmy")


def log_writer(interval):
    while True:
        tx_gen.write_all_logs()
        time.sleep(interval)


def load_from_priv_keys():
    for i, private_key in enumerate(import_keys):
        acc_name = f"LOAD_NET_IMPORTED_PRIVATE_{i}"
        cli.remove_account(acc_name)
        cli.single_call(f"hmy keys import-private-key {private_key} {acc_name}")
        tx_gen.account_balances[acc_name] = tx_gen.get_balances(acc_name)  # Manually update the tx_gen acc bal


if __name__ == "__main__":
    setup()
    if verbose:
        tx_gen.Loggers.general.logger.addHandler(logging.StreamHandler(sys.stdout))
        tx_gen.Loggers.balance.logger.addHandler(logging.StreamHandler(sys.stdout))
        tx_gen.Loggers.transaction.logger.addHandler(logging.StreamHandler(sys.stdout))
        tx_gen.Loggers.report.logger.addHandler(logging.StreamHandler(sys.stdout))

    log_writer_pool = ThreadPool()
    log_writer_pool.apply_async(log_writer, (5,))

    config = tx_gen.get_config()
    # account_manager.load_accounts("./faucet_key", passphrase="", fast_load=True)
    load_from_priv_keys()
    source_accounts = tx_gen.create_accounts(config["NUM_SRC_ACC"], "src_acc")
    sink_accounts = tx_gen.create_accounts(config["NUM_SNK_ACC"], "snk_acc")
    tx_gen.fund_accounts(source_accounts)

    tx_gen_pool = ThreadPool()
    start_time = datetime.datetime.utcnow()
    tx_gen.set_batch_amount(30)  # Each thread will generate at least 30 txs, regardless of when stop is called.
    tx_gen_pool.apply_async(lambda: tx_gen.start(source_accounts, sink_accounts))
    time.sleep(3600)
    # If one wishes to only generate txs within a time window, enforce the nonce, or set the batch amount to ~5-10.
    tx_gen.stop()
    end_time = datetime.datetime.utcnow()
    # tx_gen.return_balances(source_accounts)
    # tx_gen.return_balances(sink_accounts)
    # tx_gen.remove_accounts(source_accounts, backup=False)
    # tx_gen.remove_accounts(sink_accounts, backup=False)
    time.sleep(60)
    print(tx_gen.Loggers.transaction.filename, start_time, end_time)
    report = analysis.verify_transactions(tx_gen.Loggers.transaction.filename, start_time, end_time)
    assert report["received-transaction-report"]["successful-transactions-total"] > 1
    assert report["received-transaction-report"]["failed-transactions-total"] == 0
