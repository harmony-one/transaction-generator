import math
import random
import multiprocessing
import itertools
import time
from threading import Lock
from multiprocessing.pool import ThreadPool

from pyhmy import cli
from pyhmy import util
import requests

from .common import (
    Loggers,
    get_config,
)
from .account_manager import (
    BatchTransactions,
    get_passphrase,
    send_transaction
)

_implicit_gen_txn_nonce = 10
_is_running = False
_generator_threads = []
_generator_pool = ThreadPool()


def _get_nonce(endpoint, address):
    """
    Internal get nonce to bypass subprocess latency of calling CLI.
    """
    url = endpoint
    payload = "{\"jsonrpc\": \"2.0\", \"method\": \"hmy_getTransactionCount\"," \
              "\"params\": [\"" + address + "\", \"latest\"],\"id\": 1}"
    headers = {
        'Content-Type': 'application/json'
    }
    response = requests.request('POST', url, headers=headers, data=payload, allow_redirects=False, timeout=30)
    return int(util.json_load(response.content)["result"], 16)


def set_batch_amount(amount):
    """
    Sets the batch amount to `count`. The batch amount is the number of transactions
    to send in 1 batch of transactions. This amount can influence the termination speed
    of the generator. Moreover, the bigger the amount, the more transactions sent
    without verifying / syncing the nonce.

    Note that batched transactions are only used when the nonce is *NOT* enforced.

    Default is 10 transactions per batch.

    **Set this amount with caution.**
    """
    global _implicit_gen_txn_nonce
    assert isinstance(amount, int)
    _implicit_gen_txn_nonce = amount


def generate_to_and_from_shard():
    """
    Generate to and from shard randomly follow the config of the transaction generator.
    """
    config = get_config()
    shard_choices = list(range(0, len(config["ENDPOINTS"])))
    src_shard = random.choices(shard_choices, weights=config["SRC_SHARD_WEIGHTS"], k=1)[0]
    snk_shard = random.choices(shard_choices, weights=config["SNK_SHARD_WEIGHTS"], k=1)[0]
    retry_count = 0
    if config["ONLY_CROSS_SHARD"]:
        while src_shard == snk_shard:
            if retry_count > 50:
                Loggers.general.warning("Trying to force 'from' and 'to' shards to be different, "
                                        "are source and sink shard weights correct in config?")
                Loggers.general.write()
            src_shard = random.choices(shard_choices, weights=config["SRC_SHARD_WEIGHTS"], k=1)[0]
            snk_shard = random.choices(shard_choices, weights=config["SNK_SHARD_WEIGHTS"], k=1)[0]
            retry_count += 1
    return src_shard, snk_shard


def stop():
    """
    Stops the transaction generation.

    Note that it waits for the last batch of transactions (or single transaction)
    to send before it terminates.
    """
    global _is_running

    if not _is_running:
        return
    _is_running = False
    for t in _generator_threads:
        t.get()
    _generator_pool.close()
    Loggers.general.info("Stopped transaction generator...")


def start(source_accounts, sink_accounts):
    """
    Starts the transaction generation where for each transaction:
    * The source account is chosen in a cyclic order from an account name list called `source_accounts`
    * The sink / destination account is chosen in a cyclic order from an account name list called `sink_accounts`
    * The to and from shards are chosen at random as defined by the shard weights and options in the config.

    Note that the cyclic order starts at the first element and wraps around once it reaches the last element.

    The transaction generator can force each transaction to have a strictly increasing nonce if the
    option is enabled in the config. If nonce forcing is disabled, it is possible to send multiple transactions
    with the same nonce.
    """
    global _generator_pool, _is_running
    config = get_config()
    endpoints = config["ENDPOINTS"]

    if _is_running:
        return
    _is_running = True
    count_lock, txn_count = Lock(), 0

    def generate_transactions(src_accounts, snk_accounts):
        nonlocal txn_count
        ref_nonce = {n: [[_get_nonce(endpoints[j], cli.get_address(n)), Lock()] for j in range(len(endpoints))]
                     for n in src_accounts}
        batch = BatchTransactions()
        src_accounts_iter = itertools.cycle(src_accounts)
        snk_accounts_iter = itertools.cycle(acc for _ in range(len(src_accounts)) for acc in snk_accounts)
        while _is_running:
            src_name = next(src_accounts_iter)
            src_address = cli.get_address(src_name)
            passphrase = get_passphrase(src_name)
            for _ in range(len(snk_accounts)):
                snk_address = cli.get_address(next(snk_accounts_iter))
                txn_amt = round(random.uniform(config["AMT_PER_TXN"][0], config["AMT_PER_TXN"][1]), 18)
                src_shard, snk_shard = generate_to_and_from_shard()
                if config["ENFORCE_NONCE"]:
                    n, n_lock = ref_nonce[src_name][src_shard]
                    n_lock.acquire()
                    curr_nonce = _get_nonce(endpoints[src_shard], src_address)
                    if curr_nonce < n:
                        n_lock.release()
                        continue
                    if curr_nonce > n:  # sync nonce if too big
                        ref_nonce[src_name][src_shard][0] = curr_nonce
                    ref_nonce[src_name][src_shard][0] += 1
                    n_lock.release()
                if config["MAX_TXN_GEN_COUNT"] is not None:
                    count_lock.acquire()
                    if txn_count >= config["MAX_TXN_GEN_COUNT"]:
                        count_lock.release()
                        batch.send(config["ENDPOINTS"][0])  # p2p broadcasts to all shards for txns
                        return
                    txn_count += 1 if config["ENFORCE_NONCE"] else _implicit_gen_txn_nonce
                    count_lock.release()
                if config["ENFORCE_NONCE"]:
                    send_transaction(src_address, snk_address, src_shard, snk_shard, txn_amt,
                                     passphrase=passphrase, wait=False)
                else:
                    curr_nonce = _get_nonce(endpoints[src_shard], src_address)
                    gen_count = _implicit_gen_txn_nonce
                    if config["MAX_TXN_GEN_COUNT"]:
                        gen_count = min(config["MAX_TXN_GEN_COUNT"] - txn_count, gen_count)
                    for j in range(gen_count):
                        batch.add(src_address, snk_address, src_shard, snk_shard, txn_amt,
                                  nonce=curr_nonce + j, passphrase=passphrase, error_ok=True)
            batch.send(config["ENDPOINTS"][0], chain_id=config["CHAIN_ID"])  # p2p broadcasts to all shards for txns

    Loggers.general.info("Started transaction generator...")
    thread_count = multiprocessing.cpu_count() if not config['MAX_THREAD_COUNT'] else config['MAX_THREAD_COUNT']
    thread_count = min(thread_count, len(source_accounts))
    k = max(len(source_accounts) // thread_count, 1)
    _generator_pool = ThreadPool(processes=thread_count)
    for i in range(thread_count):
        thread_src_accounts = source_accounts[i * k: (i + 1) * k]
        _generator_threads.append(_generator_pool
                                  .apply_async(generate_transactions, (thread_src_accounts, sink_accounts)))
