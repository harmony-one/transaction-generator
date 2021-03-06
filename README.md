# Transaction Generator Python library

**This is for python 3.6+ only**

A transaction generator library for the Harmony Blockchain. It utilizes the [CLI](https://github.com/harmony-one/go-sdk) 
to generate transactions.

Documentation can be found [here](https://harmony-transaction-generator.s3-us-west-2.amazonaws.com/doc/index.html) and example scripts
that use the transaction generator can be found in the root of this directory with the suffix `_example.py`

*Note that this only works on MacOS or Linux. It could possibly work on WSL, but this is untested.*

## Install

You can install the library from pypi using the following command:
```
python3 -m pip install harmony-transaction-generator
```

You will require the latest version of `pyhmy` (which may not install if you already have it). You can get the latest with the following command:
```
python3 -m pip install pyhmy --upgrade
```


## Development Installation

Clone this repo and run:
```
make install
```

## Examples

Example script when running for devnet can be seen [here](https://gist.github.com/Daniel-VDM/220f6736ff9270bc9535d5df55be106d)

You can run `localnet_example.py` if you have a localnet running. To start a localnet, reference the main harmony repo
to set up your ev environment, then run `test/debug.sh` in the main repo.
> The localnet is an easy way to develop your scripts without relying or using real network currency. 

## Important note about funding keys
One must have keys in the keystore that have funds on ALL shards (if one wishes to funds accounts on all shards).
Note that one account does NOT have to have funds on all shard, but across all accounts in the keystore, there should be funds on all shards
> This is because the library only does same shard transfers when funding to be conservative.

## How it works
The docs can be found [here](https://harmony-transaction-generator.s3-us-west-2.amazonaws.com/doc/index.html).

Take a look at the `localnet_example.py` for some idea of how it works.
Essentially, it follows a source / sink model for transaction generation (explained more in the `start` function
[here](https://harmony-transaction-generator.s3-us-west-2.amazonaws.com/doc/generator.html)). Moreover, the annotations of the config in `localnet_example.py`
should give a better idea of how to use the library.

A video demoing library can be seen [here](https://www.youtube.com/watch?v=rTp9wZn1EqE&feature=youtu.be)

## Analysis
The `analysis` module of this library has a `verify_transaction` function that looks at the transaction logs and checks
if said transactions appear on the blockchain. Refer to [here](https://harmony-transaction-generator.s3-us-west-2.amazonaws.com/doc/analysis.html) for mode details
on how it works.

The library logs everything it can, so one can read the log files to analyze it. The filepath/filename can be accessed with
the `filename` attribute of the logger, for example `tx_gen.Loggers.transaction.filename`.
> Note that all times in the logs are in UTC.

## Caveat
**The package has NOT been thoroughly tested so please report bugs and/or PR fixes if you find any, thank you!** 

## Troubleshooting Errors While Testing
#### Possible fixes
* Backup and clear keystore at `~/.hmy_cli/account-keys`
* Update pyhmy to latest version, can be checked [here](https://pypi.org/project/pyhmy/).
> Check version with `python3 -m pip list | grep pyhmy` 
* If `current balance is not enough for requested transfer`, try increasing the 'INIT_SRC_ACC_BAL_PER_SHARD' value in the config file
