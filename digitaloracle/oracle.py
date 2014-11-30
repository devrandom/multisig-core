from __future__ import print_function

__author__ = 'sserrano'

from pycoin.ecdsa import generator_secp256k1
from pycoin.serialize import b2h, stream_to_bytes
from pycoin.key.BIP32Node import BIP32Node
from pycoin.tx.pay_to import ScriptMultisig
from pycoin.tx.script.tools import *
from pycoin.tx.script import der
import json
import requests
import uuid


class Error(Exception):
    pass


class OracleError(Error):
    pass


class Oracle(object):
    def __init__(self, keys, tx_db, manager=None):
        """
        Create an Oracle object
        :param keys: non-oracle deterministic keys
        :param tx_db: lookup database for transactions - see pycoin.services.get_tx_db()
        :param manager: the manager identifier for this wallet (only used on creation for now)
        """
        self.keys = keys
        self.manager = manager
        self.public_keys = [str(key.wallet_key(as_private=False)) for key in self.keys]
        self.wallet_agent = 'digitaloracle-pycoin-0.01'
        self.oracle_keys = None
        self.tx_db = tx_db

    def sign(self, tx, input_chain_paths, output_chain_paths):
        """
        Have the Oracle sign the transaction
        :param tx: the transaction to be signed
        :param input_chain_paths: the derivation path for each input, or None if the input does not need to be signed
        :param output_chain_paths: the derivation path for each change output, or None if the output is not change
        """
        replace_dummy(tx)  # TODO copy
        # Have the Oracle sign the tx
        chain_paths = []
        input_scripts = []
        input_txs = []
        for i, inp in enumerate(tx.txs_in):
            input_tx = self.tx_db.get(inp.previous_hash)
            if input_tx is None:
                raise Error("could not look up tx for %s" % (b2h(inp.previous_hash)))
            input_txs.append(input_tx)
            if input_chain_paths[i]:
                input_scripts.append(self.script(input_chain_paths[i]).script())
                chain_paths.append(input_chain_paths[i])
            else:
                input_scripts.append(None)
                chain_paths.append(None)

        req = {
            "walletAgent": self.wallet_agent,
            "transaction": {
                "bytes": b2h(stream_to_bytes(tx.stream)),
                "inputScripts": [(b2h(script) if script else None) for script in input_scripts],
                "inputTransactions": [b2h(stream_to_bytes(tx.stream)) for tx in input_txs],
                "chainPaths": chain_paths,
                "outputChainPaths": output_chain_paths,
                "masterKeys": self.public_keys,
            }
        }
        body = json.dumps(req)
        url = self.url() + "/transactions"
        print(body)
        response = requests.post(url, body, headers={'content-type': 'application/json'})
        result = response.json()
        if response.status_code == 200 and result.get('result', None) == 'success':
            self.oracle_keys = [BIP32Node.from_hwif(s) for s in result['keys']['default']]
        elif response.status_code == 200 or response.status_code == 400:
            raise OracleError(response.content)
        else:
            raise Error("Unknown response " + response.status_code)

    def url(self):
        account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:digitaloracle.co:%s" % (self.public_keys[0])))
        url = 'https://s.digitaloracle.co/keychains/' + account_id
        return url

    def get(self):
        """Retrieve the oracle public key"""
        url = self.url()
        response = requests.get(url)
        result = response.json()
        if response.status_code == 200 and result.get('result', None) == 'success':
            self.oracle_keys = [BIP32Node.from_hwif(s) for s in result['keys']['default']]
        elif response.status_code == 200 or response.status_code == 400:
            raise OracleError(response.content)
        else:
            raise Error("Unknown response " + response.status_code)

    def create(self, email=None, phone=None):
        """
        Create an Oracle keychain on server and retrieve the oracle public key
        :param email: the email contact
        :return:
        """
        r = {'walletAgent': self.wallet_agent, 'rulesetId': 'default'}
        if self.manager:
            r['managerUsername'] = self.manager
        r['pii'] = {}
        calls = []
        if email:
            r['pii']['email'] = email
            calls.append('email')
        if email:
            r['pii']['phone'] = phone
            calls.append('phone')
        r['parameters'] = {
            "levels": [
                {"asset": "BTC", "period": 60, "value": 0.001},
                {"delay": 0, "calls": calls}
            ]
        }
        r['keys'] = self.public_keys
        body = json.dumps(r)
        url = self.url()
        response = requests.post(url, body, headers={'content-type': 'application/json'})

        result = response.json()
        if response.status_code == 200 and result.get('result', None) == 'success':
            self.oracle_keys = [BIP32Node.from_hwif(s) for s in result['keys']['default']]
        elif response.status_code == 400 and result.get('error', None) == 'already exists':
            raise OracleError("already exists")
        elif response.status_code == 200 or response.status_code == 400:
            raise OracleError(response.content)
        else:
            raise Error("Unknown response " + response.status_code)

    def all_keys(self):
        if self.oracle_keys:
            return self.keys + self.oracle_keys
        else:
            raise OracleError("oracle_keys not initialized - get, create, or set the property")

    def script(self, path):
        """Get the redeem script for the path"""
        subkeys = [key.subkey_for_path(path or "") for key in self.all_keys()]
        secs = [key.sec() for key in subkeys]
        secs.sort()
        script = ScriptMultisig(2, secs)
        return script


def dummy_signature(sig_type):
    order = generator_secp256k1.order()
    r, s = order - 1, order // 2
    return der.sigencode_der(r, s) + bytes_from_int(sig_type)


def replace_dummy(tx):
    """replace dummy signatures with OP_0 for digitaloracle compatibility"""
    dummy = b2h(dummy_signature(1))
    for inp in tx.txs_in:
        ops1 = []
        for op in opcode_list(inp.script):
            if op == dummy:
                op = 'OP_0'
            ops1.append(op)
        inp.script = compile(' '.join(ops1))