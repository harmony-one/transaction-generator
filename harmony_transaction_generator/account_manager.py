import os
import shutil
import math
import multiprocessing
import json
import datetime
import random
import copy
from multiprocessing.pool import ThreadPool

import pexpect
from pyhmy import cli
from pyhmy.util import (
    json_load
)

from .common import (
    Loggers,
    get_config,
    import_account_name_prefix,
)

account_balances = {}

_accounts_added = set()
_loaded_passphrase = {}  # keys = acc_names, values = passphrase


class BatchTransactions:
    """
    This object handles sending batched transactions sequentially via the CLI.
    Note that multiple transactions can be sent with 1 instance of the CLI binary.
    """

    @staticmethod
    def _validate_transaction(transaction):
        assert isinstance(transaction, dict)
        for k, v in transaction.items():
            if k == "stop-on-error":
                assert isinstance(v, bool), f"value for {k} must be a boolean"
            else:
                assert isinstance(v, str), f"value for {k} must be a string"

    def __init__(self, size=None):
        """
        Create a batch of transaction that can take up to `size` transactions.
        If no size is provided, there is no limit on the number of transactions to send in one go.
        """
        self.size = size
        self._transactions_buffer = []
        self._file_name = f"/tmp/{import_account_name_prefix}_BATCHED_TXS_{random.randint(0, 1e10)}.json"

    def __repr__(self):
        return f"<BatchTransactions: {self._transactions_buffer}>"

    def __len__(self):
        return len(self._transactions_buffer)

    def __getitem__(self, y):
        return self._transactions_buffer[y]

    def __iter__(self):
        return iter(self._transactions_buffer)

    def __del__(self):
        if os.path.exists(self._file_name):
            os.remove(self._file_name)

    def add(self, from_address, to_address, src_shard, dst_shard, amount,
            gas_price=1, gas_limit=21000, nonce=None, passphrase='', error_ok=True):
        """
        This will add a single transaction `from_address` to `to_address` from shard `src_shard`
        to `dst_shard` for `amount` $ONE with a `gas_price` (default 1) in NANO at a `gas_limit` (default 21000)
        with an optional `nonce` (default uses nonce from blockchain) to the buffer of batched transactions.
        The `passphrase` is used to unlock the keystore file. The `error_ok` flag can be set to true to indicate
        that the added transaction will not block the rest of the batch should it fail to send.
        """
        transaction = {  # Cast most elements to string to follow batched tx file format for CLI
            "from": from_address, "to": to_address, "from-shard": str(src_shard),
            "to-shard": str(dst_shard), "amount": str(amount), "passphrase-string": passphrase,
            "gas-price": str(gas_price), "gas-limit": str(gas_limit), "stop-on-error": not error_ok
        }
        if nonce is not None:
            transaction['nonce'] = str(nonce)
        BatchTransactions._validate_transaction(transaction)
        if self.size and self.size <= len(self._transactions_buffer):
            self._transactions_buffer.pop(0)
        self._transactions_buffer.append(transaction)

    def remove(self, transaction):
        """
        This will remove a `transaction` (assuming it is in proper form) from the buffer of batched transactions
        """
        BatchTransactions._validate_transaction(transaction)
        self._transactions_buffer.remove(transaction)

    def get_transaction_buffer(self):
        """
        This will return a deep copy of the transaction buffer.
        """
        return copy.deepcopy(self._transactions_buffer)

    def send(self, endpoint, wait_for_confirm=None, chain_id="testnet"):
        """
        This will send all transactions in the buffer of transaction **sequentially** using the CLI
        with the provided `endpoint` and `chain_id`. One can force EACH transaction to confirm by
        providing a max `wait_for_confirm` time.

        This will return a list of dictionaries that contain transaction information (and possibly errors)
        of all the transactions sent.
        """
        if wait_for_confirm:
            assert isinstance(wait_for_confirm, (int, float))

        if not self._transactions_buffer:
            return []

        with open(self._file_name, 'w') as f:
            json.dump(self._transactions_buffer, f, indent=4)

        command = f"hmy --node={endpoint} transfer --file {self._file_name} --chain-id {chain_id} "
        command += f"--timeout {wait_for_confirm} " if wait_for_confirm else f"--timeout 0 "
        timeout = None if self.size is None else get_config()["TXN_WAIT_TO_CONFIRM"] * self.size
        response = json_load(cli.single_call(command, error_ok=True, timeout=timeout))

        for txn, sent_txn in zip(self._transactions_buffer, response):
            info = {  # Cast elements to fit transaction logger convention.
                'from': txn['from'], 'to': txn['to'],
                'from-shard': int(txn['from-shard']), 'to-shard': int(txn['to-shard']),
                'amount': float(txn['amount']),
                'txn-fee': round(float(txn['gas-price']) * 1e-9 * float(txn['gas-limit']), 18),
                'nonce': None, 'error': None, 'hash': None, 'send-time-utc': None,
            }
            if "nonce" in txn.keys():
                info['nonce'] = int(txn['nonce'])
            if "transaction-hash" in sent_txn.keys():
                info['hash'] = sent_txn['transaction-hash']
            if "errors" in sent_txn.keys():
                info['error'] = ', '.join(e for e in sent_txn['errors'])
            if "time-signed-utc" in sent_txn.keys():
                info['send-time-utc'] = sent_txn['time-signed-utc']
            info['batched-extra-info'] = sent_txn  # Keep track of returned info just in case.
            Loggers.transaction.info(json.dumps(info))

        self._transactions_buffer.clear()
        return response


def create_accounts(count, name_prefix="generated"):
    """
    Create `count` accounts where all account-names/wallet-names have the prefix `name_prefix`.
    """
    config = get_config()
    assert count > 0
    benchmarking_accounts = []

    def create(start_i, end_i):
        local_accounts = []
        for j in range(start_i, end_i):
            acc_name = f"{import_account_name_prefix}{name_prefix}_{j}"
            create_account(acc_name)
            addr = cli.get_address(acc_name)
            while addr is None:  # Just added accounts, need to ensure we can fetch from keystore, TODO must improve.
                addr = cli.get_address(acc_name)
            Loggers.general.info(f"Created account: {addr} ({acc_name})")
            local_accounts.append(acc_name)
        return local_accounts

    max_threads = multiprocessing.cpu_count() if not config['MAX_THREAD_COUNT'] else config['MAX_THREAD_COUNT']
    max_threads = min(count, max_threads)
    steps = int(math.ceil(count / max_threads))
    if count < 2:
        benchmarking_accounts = create(0, count)
    else:
        threads = []
        pool = ThreadPool(processes=max_threads)
        for i in range(max_threads):
            threads.append(pool.apply_async(create, (i * steps, min(count, (i + 1) * steps))))
        for t in threads:
            benchmarking_accounts.extend(t.get())
        pool.close()
        pool.join()

    return benchmarking_accounts


def get_balances(account_name):
    """
    This gets the balances for the address associated with the `account_name`
    (aka wallet-name) in the CLI's keystore.
    """
    config = get_config()
    address = cli.get_address(account_name)
    if not address:
        return {}
    response = cli.single_call(f"hmy balances {address} --node={config['ENDPOINTS'][0]}", timeout=60)
    balances = eval(response)  # There is a chance that the CLI returns a malformed json array.
    info = {'address': address, 'balances': balances, 'time-utc': str(datetime.datetime.utcnow())}
    Loggers.balance.info(json.dumps(info))
    account_balances[account_name] = balances
    return balances


def process_passphrase(proc, passphrase):
    """
    This will enter the `passphrase` interactively given the pexpect child program, `proc`.
    """
    proc.expect("Enter passphrase:\r\n")
    proc.sendline(passphrase)


def load_accounts(keystore_path, passphrase, name_prefix="import", fast_load=False):
    """
    Load accounts from `keystore_path`. Note that the directory must contain keystore files only,
    and **NOT** directory of wallets/account-names containing keystore files. The `passphrase` for
    **ALL** keystore files but also be provided. One can provide an optional `name_prefix` for the
    account-name of each imported keystore file. One can specify `fast_load` to blindly copy over
    files to the CLI's keystore instead of using the "import-ks" command.

    It will return a list of accounts names that were added.
    """
    config = get_config()
    assert os.path.exists(keystore_path)
    keystore_path = os.path.realpath(keystore_path)
    key_paths = os.listdir(keystore_path)
    accounts_added = []

    def load(start, end):
        for j, file_name in enumerate(key_paths[start: end]):
            # STRONG assumption about imported key-files.
            if file_name.endswith(".key") or not file_name.startswith("."):
                file_path = f"{keystore_path}/{file_name}"
                account_name = f"{import_account_name_prefix}{name_prefix}{j + start}"
                if not cli.get_address(account_name):
                    cli.remove_account(account_name)  # Just in-case there is a folder with nothing in it.
                    Loggers.general.info(f"Adding key file: ({j + start}) {file_name}")
                    if fast_load:
                        keystore_acc_dir = f"{cli.get_account_keystore_path()}/{account_name}"
                        os.makedirs(keystore_acc_dir, exist_ok=True)
                        shutil.copy(file_path, f"{keystore_acc_dir}/{file_name}")
                    else:
                        cli.single_call(f"hmy keys import-ks {file_path} {account_name} "
                                        f"--passphrase={passphrase}")
                _loaded_passphrase[account_name] = passphrase
                accounts_added.append(account_name)
                _accounts_added.add(account_name)
                account_balances[account_name] = get_balances(account_name)

    max_threads = multiprocessing.cpu_count() if not config['MAX_THREAD_COUNT'] else config['MAX_THREAD_COUNT']
    max_threads = min(max_threads, len(key_paths))
    steps = int(math.ceil(len(key_paths) / max_threads))

    threads = []
    pool = ThreadPool(processes=max_threads)
    for i in range(max_threads):
        threads.append(pool.apply_async(load, (i * steps, (i + 1) * steps)))
    for t in threads:
        t.get()
    pool.close()
    pool.join()
    return accounts_added


def create_account(account_name, exist_ok=True):
    """
    This will create a single account with `account_name`. One can choose to continue
    if the account exists by setting `exist_ok` to true.
    """
    # TODO: add caching
    try:
        cli.single_call(f"hmy keys add {account_name}")
    except RuntimeError as e:
        if not exist_ok:
            raise e
    get_balances(account_name)
    _accounts_added.add(account_name)
    _loaded_passphrase[account_name] = ''  # Default passphrase used by the CLI.
    return account_name


def get_passphrase(account_name):
    """
    This returns the passphrase associated with the `account_name` (aka wallet-name)
    in the CLI's keystore **IF** it was loaded or created using this account manager.
    Otherwise it will return the CLI's default passphrase of an empty string.
    """
    pw = _loaded_passphrase.get(account_name, None)
    if pw is None:
        Loggers.general.warning(
            f"Passphrase unknown for {account_name}, using default passphrase.")
        pw = ''  # Default passphrase for CLI
    return pw


def remove_accounts(accounts, backup=False):
    """
    This will remove all `accounts`, where `accounts` is an iterable of accounts name.
    One can specify `backup` if one wishes to log the private keys of all removed accounts.
    """
    for acc in accounts:
        address = cli.get_address(acc)
        private_key = ""
        if backup:
            try:
                private_key = cli.single_call(f"hmy keys export-private-key {address}").strip()
            except RuntimeError:
                Loggers.general.error(f"{address} ({acc}) was not imported via CLI, cannot backup")
                private_key = "NOT-IMPORTED-USING-CLI"
        cli.remove_account(acc)
        if acc in _accounts_added:
            _accounts_added.remove(acc)
        if acc in _loaded_passphrase:
            del _loaded_passphrase[acc]
        removed_account = {"address": address, "private-key": private_key}
        Loggers.general.info(f"Removed Account: {removed_account}")


def send_transaction(from_address, to_address, src_shard, dst_shard, amount,
                     gas_price=1, gas_limit=21000, nonce=None, passphrase='', wait=True,
                     retry=False, max_tries=5):
    """
    This will send a **single** transaction `from_address` to `to_address` from shard `src_shard`
    to `dst_shard` for `amount` $ONE with a `gas_price` (default 1) in NANO at a `gas_limit` (default 21000)
    using `nonce`. The `passphrase` is used to unlock the keystore file. One can choose to `wait`
    for the transaction to confirm before returning. One can choose to `retry` up to `max_tries` times
    if the transaction fails to send.

    It will return the "transaction-hash" once the transaction is sent.
    """
    config = get_config()
    assert cli.check_address(from_address), "source address must be in the CLI's keystore."
    attempt_count = 0
    command = f"hmy --node={config['ENDPOINTS'][src_shard]} transfer " \
              f"--from={from_address} --to={to_address} " \
              f"--from-shard={src_shard} --to-shard={dst_shard} " \
              f"--amount={amount} --chain-id={config['CHAIN_ID']} " \
              f"--gas-price {gas_price} --gas-limit {gas_limit} --passphrase "
    command += f"--timeout {config['TXN_WAIT_TO_CONFIRM']} " if wait else f"--timeout 0 "
    if nonce:
        command += f"--nonce {nonce} "
    info = {
        'from': from_address, 'to': to_address,
        'from-shard': src_shard, 'to-shard': dst_shard,
        'amount': amount, 'send-time-utc': str(datetime.datetime.utcnow()),
        'txn-fee': round(gas_price * 1e-9 * gas_limit, 18), 'nonce': nonce,
        'error': None, 'hash': None
    }
    while True:
        try:
            proc = cli.expect_call(command, timeout=config["TXN_WAIT_TO_CONFIRM"])
            process_passphrase(proc, passphrase)
            response = proc.read()
            info['hash'] = json_load(response)["transaction-hash"]
            Loggers.transaction.info(json.dumps(info))
            return info['hash']
        except (RuntimeError, json.JSONDecodeError, pexpect.exceptions.TIMEOUT) as e:
            if not retry or attempt_count >= max_tries:
                info['error'] = str(e)
                Loggers.transaction.error(json.dumps(info))
                Loggers.transaction.write()
                return None
            attempt_count += 1
            Loggers.general.warning(f"[Trying Again] Failure sending from {from_address} (s{src_shard}) "
                                    f"to {to_address} (s{dst_shard})\n"
                                    f"\tError: {e}")


def return_balances(accounts, wait=False):
    """
    The will return the balance of all accounts in `accounts` to the address specified in
    the config where `accounts` is an iterable of account-names/wallet-names.
    One can choose to `wait` for each transaction to succeed.

    This will return a list of "transaction-hashs" once all the transactions is sent.
    """
    config = get_config()
    Loggers.general.info("Refunding accounts...")
    txn_hashes = []
    account_addresses = []
    for account in accounts:
        for shard_index in range(len(config['ENDPOINTS'])):
            balances = get_balances(account)  # There is a chance that you don't get all balances (b/c of latency)
            if shard_index < len(balances):
                amount = balances[shard_index]["amount"]
                amount -= config["ESTIMATED_GAS_PER_TXN"]
                if amount > config['ESTIMATED_GAS_PER_TXN']:
                    from_address = cli.get_address(account)
                    to_address = config['REFUND_ACCOUNT']
                    account_addresses.append(from_address)
                    passphrase = get_passphrase(account)
                    txn_hash = send_transaction(from_address, to_address, shard_index, shard_index, amount,
                                                passphrase=passphrase, wait=wait)
                    txn_hashes.append({"shard": shard_index, "hash": txn_hash})
    Loggers.general.info(f"Refund transaction hashes: {txn_hashes}")
    return txn_hashes


def reset(safe=True):
    """
    Reset the account manager to its initial state. One can choose to do a `safe` reset
    to ensure all accounts have been refunded and all removed keys have their private keys
    logged.
    """
    accounts_added = list(_accounts_added)
    return_balances(accounts_added, wait=safe)
    remove_accounts(accounts_added, backup=safe)
    account_balances.clear()
    _accounts_added.clear()
    _loaded_passphrase.clear()
