from __future__ import print_function
import io
import json

import multisigcore
from multisigcore.providers import BatchService
from pycoin import encoding
from pycoin.key.BIP32Node import BIP32Node
from pycoin.scripts.tx import DEFAULT_VERSION
from pycoin.serialize import h2b
from pycoin.services import providers
from pycoin.tx import Tx, TxOut, TxIn
from pycoin.tx.TxOut import standard_tx_out_script
from pycoin.tx.pay_to import ScriptMultisig, ScriptPayToScript


__author__ = 'devrandom'

LOOKAHEAD = 20
DUST = 546


class InsufficientBalanceException(ValueError):
    def __init__(self, balance):
        self.balance = balance


class AccountKey(BIP32Node):
    @classmethod
    def from_key(cls, key):
        return cls.from_hwif(key)

    def leaf(self, n, change=False):
        return self.leaf_for_path("%s/%s" % (1 if change else 0, n))

    def leaf_for_path(self, path):
        return self.subkey_for_path(path)


class MasterKey(BIP32Node):
    """Master key (m or M)"""
    @classmethod
    def from_seed(cls, master_secret, netcode='BTC'):
        return cls.from_master_secret(master_secret, netcode=netcode)

    @classmethod
    def from_seed_hex(cls, master_secret_hex, netcode='BTC'):
        return cls.from_seed(h2b(master_secret_hex), netcode)

    @classmethod
    def from_key(cls, key):
        return cls.from_hwif(key)

    def account_for_path(self, path):
        return AccountKey.from_key(self.subkey_for_path(path).hwif(as_private=True))

    def electrum_account(self, n):
        return self.account_for_path("0H/%s" % (n,))

    def bip32_account(self, n):
        return self.account_for_path("%sH" % (n,))

    def bip44_account(self, n, purpose=0, coin=0):
        return self.account_for_path("%sH/%sH/%sH" % (purpose, coin, n))


class ElectrumMasterKey(BIP32Node):
    """Electrum 'Master' key (m/0' or M/0').  Normally used for external Electrum keychains that are
    participating in a multisig relationship"""
    @classmethod
    def from_key(cls, key):
        return cls.from_hwif(key)

    def account_for_path(self, path):
        return AccountKey.from_key(self.subkey_for_path(path).hwif())

    def electrum_account(self, n):
        return self.account_for_path("%s" % (n,))

TX_FEE_PER_THOUSAND_BYTES = 1000


def recommended_fee_for_tx(tx):
    """
    Return the recommended transaction fee in satoshis.
    This is a grossly simplified version of this function.
    TODO: improve to consider TxOut sizes.
      - whether the transaction contains "dust"
      - whether any outputs are less than 0.001
    """
    # FIXME review
    s = io.BytesIO()
    tx.stream(s)
    tx_byte_count = len(s.getvalue())
    tx_fee = TX_FEE_PER_THOUSAND_BYTES * ((999+tx_byte_count)//1000)
    return tx_fee


class AccountTxIn(TxIn):
    def __init__(self, path, previous_hash, previous_index, script=b'', sequence=4294967295):
        super(AccountTxIn, self).__init__(previous_hash, previous_index, script, sequence)
        self.path = path


class AccountTxOut(TxOut):
    def __init__(self, path, coin_value, script):
        super(AccountTxOut, self).__init__(coin_value, script)
        self.path = path


class AccountTx(Tx):
    def __init__(self, txs_in, txs_out, version, unspents):
        super(AccountTx, self).__init__(txs_in=txs_in, txs_out=txs_out, version=version, unspents=unspents)

    def input_chain_paths(self):
        return [tin.path for tin in self.txs_in]

    def output_chain_paths(self):
        return [(tout.path if isinstance(tout, AccountTxOut) else None) for tout in self.txs_out]


class Account(object):
    __slots__ = ['netcode', 'lookahead', 'address_map', '_provider', '_cache']

    def __init__(self, netcode='BTC', cache=None):
        """
        :param netcode: network code
        :param str cache: the JSON formatted cache - see the cache property
        """
        object.__setattr__(self, 'netcode', netcode)
        object.__setattr__(self, 'lookahead', LOOKAHEAD)
        self._provider = providers
        self.address_map = None

        def decode_key(dct):
            if 'hwif' in dct:
                return BIP32Node.from_hwif(dct['hwif'])
            return dct
        if cache:
            self._cache = json.loads(cache, object_hook=decode_key)
        else:
            self._cache = {'keys': {}, 'issued': {'0': 1, '1': 1}}

    def set_lookahead(self, lookahead):
        """Set the lookahead for looking for spendables"""
        object.__setattr__(self, 'lookahead', lookahead)

    @property
    def cache(self):
        """A JSON encoded cache.
        Save this cache in a database in order to speed up public key and address derivation in the future.
        Also stores the number of issued keys on the internal (change) and external (receive) subchains.
        """
        def encode_key(obj):
            if isinstance(obj, BIP32Node):
                return {'hwif': obj.hwif()}
            raise TypeError()
        return json.dumps(self._cache, default=encode_key)

    def address(self, n, change=False):
        """
        The address of leaf key n in either the public subchain or the change subchain
        :param int n: leaf key number, starts at zero
        :param change: whether we want the change subchain
        :return: the address string
        :rtype: str
        """
        raise NotImplementedError()

    @property
    def num_ext_keys(self):
        """The number of issued receive keys"""
        return self._cache['issued']['0']

    @property
    def num_int_keys(self):
        """The number of issued change keys"""
        return self._cache['issued']['1']

    def addresses(self, do_lookahead=False):
        """
        A list of all generated addresses
        :param do_lookahead: whether to look ahead beyond our last issued address
        :return: list of addresses
        :rtype: list[str]
        """
        lookahead = self.lookahead if do_lookahead else 0
        addresses = [self.address(n, False) for n in range(0, self.num_ext_keys + lookahead)]
        addresses.extend([self.address(n, True) for n in range(0, self.num_int_keys + lookahead)])
        return addresses

    def make_address_map(self, do_lookahead=False):
        """
        A map of addresses to derivation path
        :param do_lookahead: whether to look ahead beyond our last issued address
        :return: map of addresses to sub-paths
        :rtype: dict[str, str]
        """
        lookahead = self.lookahead if do_lookahead else 0
        address_map = {self.address(n, False): "0/%d"%(n,) for n in range(0, self.num_ext_keys + lookahead)}
        address_map.update({self.address(n, True): "1/%d"%(n,) for n in range(0, self.num_int_keys + lookahead)})
        return address_map

    def spendables(self):
        """
        A list of Spendables - unspent transaction outputs
        :return: dict of spendables for our addresses
        :rtype: dict[str, Spendable]
        """
        self.address_map = self.make_address_map(True)
        spendables = None
        if isinstance(self._provider, BatchService):
            provider = self._provider
            """:type: BatchService"""
            spendables = provider.spendables_for_addresses(self.address_map.keys())
        else:
            spendables = {}
            for addr in self.address_map.keys():
                spends = self._provider.spendables_for_address(addr)
                if spends:
                    spendables[addr] = spends

        return spendables

    def balance(self):
        """Total balance in spendables for our keys"""
        spendables = self.spendables()
        total = reduce(lambda x,y: x+y, [s.coin_value for sublist in spendables.values() for s in sublist], 0)
        return total

    def tx(self, payables):
        """
        Construct a transaction with available spendables
        :param list[(str, int)] payables: tuple of address and amount
        :return Tx or None: the transaction or None if not enough balance
        """
        all_spendables = [(s, addr) for (addr, sublist) in self.spendables().items() for s in sublist]

        send_amount = 0
        for address, coin_value in payables:
            send_amount += coin_value

        txs_out = []
        for address, coin_value in payables:
            script = standard_tx_out_script(address)
            txs_out.append(TxOut(coin_value, script))

        total = 0
        txs_in = []
        spendables = []
        while total < send_amount and all_spendables:
            (spend, addr) = all_spendables.pop(0)
            spendables.append(spend)
            txs_in.append(AccountTxIn(self.path_for(addr), spend.tx_hash, spend.tx_out_index, script=b''))
            total += spend.coin_value

        tx_for_fee = Tx(txs_in=txs_in, txs_out=txs_out, version=DEFAULT_VERSION, unspents=spendables)
        fee = recommended_fee_for_tx(tx_for_fee)

        while total < send_amount + fee and all_spendables:
            (spend, addr) = all_spendables.pop(0)
            spendables.append(spend)
            txs_in.append(AccountTxIn(self.path_for_check(addr), spend.tx_hash, spend.tx_out_index, script=b''))
            total += spend.coin_value

        if total > send_amount + fee + DUST:
            addr = self.current_change_address()
            script = standard_tx_out_script(addr)
            txs_out.append(AccountTxOut(self.path_for_check(addr), total - send_amount - fee, script))
        elif total < send_amount + fee:
            raise InsufficientBalanceException(total)

        # check total >= amount + fee
        tx = AccountTx(txs_in=txs_in, txs_out=txs_out, version=DEFAULT_VERSION, unspents=spendables)
        return tx

    def sign(self, tx):
        """Sign a previously constructed transaction
        :type tx: Tx
        """
        keys = self.keys_for_tx(tx)

        multisigcore.local_sign(tx, self.collect_redeem_scripts(tx), keys)

    def current_address(self):
        """
        The last issued address.
        :rtype: str
        """
        return self.address(self.num_ext_keys - 1)

    def current_change_address(self):
        """
        The last issued change address.
        :rtype: str
        """
        return self.address(self.num_int_keys - 1, True)

    def path_for(self, addr):
        """
        :type addr: str
        :return: sub-path (e.g. "0/123" or "1/456")
        :rtype: str
        """
        return self.address_map[addr]

    def path_for_check(self, addr):
        """
        :type addr: str
        :return: sub-path (e.g. "0/123" or "1/456")
        :rtype: str
        :raise: if we haven't issued this address
        """
        path = self.address_map[addr]
        if path is None:
            raise ValueError("unknown address %s"%(addr,))
        return path

    def keys_for_tx(self, tx):
        """
        A list of private keys, matching each input
        :type tx: Tx
        :return: list[pycoin.key.Key]
        """
        raise NotImplementedError()

    def collect_redeem_scripts(self, tx):
        """
        :type tx: Tx
        :rtype: dict or None
        """
        return None


class SimpleAccount(Account):
    __slots__ = ['_key']

    def __init__(self, key, cache=None):
        """
        :type key: AccountKey
        :param str cache: JSON formatted cache
        """
        super(SimpleAccount, self).__init__(key._netcode, cache)
        self._key = key

    def address(self, n, change=False):
        subchain_index = '1' if change else '0'
        path = "%s/%s" % (subchain_index, n)
        if path not in self._cache['keys']:
            self._cache['keys'][path] = self._key.subkey_for_path(path + ".pub")
        return self._cache['keys'][path].address()

    def keys_for_tx(self, tx):
        result = []
        for tin in tx.txs_in:
            if tin and tin.path:
                # we only cache public keys - derive the private key
                result.append(self._key.subkey_for_path(tin.path))
            else:
                result.append(None)
        return result


class MultisigAccount(Account):
    def __init__(self, keys, num_sigs=None, sort=True, complete=True, netcode='BTC', cache=None):
        """
        Create a multisig account with multiple participating keys

        :param keys: non-oracle deterministic keys
        :type keys: list[BIP32Node]
        :param num_sigs: number of required signatures
        :param complete: whether we need additional keys to complete the configuration of this account
        """
        super(MultisigAccount, self).__init__(netcode, cache)
        self._keys = keys
        self._local_key = next(iter([key for key in keys if key.is_private()]), None)  # first private key
        self._public_keys = [str(key.wallet_key(as_private=False)) for key in self._keys]
        self._num_sigs = num_sigs if num_sigs else len(keys) - (1 if complete else 0)
        self._complete = complete
        self._sort = sort

    @property
    def complete(self):
        return self._complete

    @property
    def public_keys(self):
        return self._public_keys

    @property
    def keys(self):
        return self._keys

    def add_key(self, key):
        if self._complete:
            raise Exception("account already complete")
        self._keys.append(key)
        self._public_keys.append(key.wallet_key(as_private=False))

    def add_keys(self, keys):
        for key in keys:
            self.add_key(key)

    def set_complete(self):
        if self._complete:
            raise Exception("account already complete")
        self._complete = True

    def leaf_script(self, n, change=False):
        return self.script_for_path("%s/%s" % (1 if change else 0, n))

    def leaf_payto(self, n, change=False):
        return self.payto_for_path("%s/%s" % (1 if change else 0, n))

    def address(self, n, change=False):
        return self.leaf_payto(n, change).address(self.netcode)

    def script_for_path(self, path):
        """Get the redeem script for the path.  The multisig format is (n-1) of n, but can be overridden.

        :param: path: the derivation path
        :type: path: str
        :return: the script
        :rtype: ScriptMultisig
        """
        if not self._complete:
            raise Exception("account not complete")
        if path not in self._cache['keys']:
            self._cache['keys'][path] =\
                [key.subkey_for_path(path + ".pub") for key in self.keys]

        subkeys = self._cache['keys'][path]
        secs = [key.sec() for key in subkeys]
        if self._sort:
            secs.sort()
        script = ScriptMultisig(self._num_sigs, secs)
        return script

    def payto_for_path(self, path):
        """Get the payto script for the path.  See also :meth:`.script`

        :param: path: the derivation path
        :type: path: str
        :return: the script
        :rtype: LeafPayTo
        """
        script = self.script_for_path(path)
        payto = LeafPayTo(hash160=encoding.hash160(script.script()), path=path)
        return payto

    def keys_for_tx(self, tx):
        if self._local_key is None:
            raise ValueError("no private key supplied - can't sign")
        result = []
        for tin in tx.txs_in:
            if tin and tin.path:
                # we only cache public keys - derive the private key
                result.append(self._local_key.subkey_for_path(tin.path))
            else:
                result.append(None)
        return result

    def collect_redeem_scripts(self, tx):
        raw_scripts = []
        for tin in tx.txs_in:
            if tin and tin.path:
                raw_scripts.append(self.script_for_path(tin.path))
        return raw_scripts


class LeafPayTo(ScriptPayToScript):
    def __init__(self, hash160, path):
        super(LeafPayTo, self).__init__(hash160)
        self.path = path
