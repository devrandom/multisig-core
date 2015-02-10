import io

from pycoin.serialize import h2b
from pycoin.tx import Tx
from pycoin.tx.pay_to import build_p2sh_lookup
from pycoin.tx.tx_utils import LazySecretExponentDB

__author__ = 'devrandom'

import unittest
from digitaloracle import Oracle
from pycoin.key.BIP32Node import BIP32Node

TEST_PATH = "0/0/1"

class OracleTest(unittest.TestCase):
    def setUp(self):
        self.wallet_private_key = BIP32Node.from_master_secret("aaa-2015-02-10".encode('utf8'))
        print(self.wallet_private_key.hwif())
        self.recover_key = BIP32Node.from_hwif("xpub661MyMwAqRbcGmRK6wKJrfMXoenZ86PMUfBWNvmmp5c51PyyzjY7yJL9venRUYqmSqNo7iGqHbVWkTVYzY2drw57vr45iHxV7NsAqF4ZWg5")
        self.wallet_key = BIP32Node.from_hwif("xpub661MyMwAqRbcFqtR38s6kVQudQxKpHzNJyWEXmz2TnuDoR8FpZR7EuL158B5QDaYvxCfp3LAEa8VwdtxNgKHNha4JKqGrqkzBGboJFwgyrR")
        self.oracle_key = BIP32Node.from_hwif("xpub68rQ8y4gfKeqG3sxQQE7uNwjnjcTiEZDQCrr2witfS3VrZ3QkeR2XuiQWUpdQRUVShcyVzjX2ZvDWHS2SZcZJXaGC7HybSPVMDXErbRRHwn")
        self.tx_db = dict()
        self.input_tx = Tx.tx_from_hex("0100000001d7e5d290d1363f9a3a1ee992d729f5e2f6938539e1eb6fd98ddd32f5211b66b8010000006a473044022043ac09592090ec32e75fe104aa97e87d31852d23ee17595659ea82e9e177822b0220727a37d1f93a088a99f907f924b92f2938b3a1e5093af32ee854382275fe06c1012103070454c3e8fea7c8e7e4a9c4d4a15e7e3088a0555e2ed303ec25d0f9bb0a75a6ffffffff02e09304000000000017a9141bbf6712630dd01fab4e70ac91a06925d138f27387d2906406000000001976a9149fe455808b8f32c84f4c96db7865cfb2475bffbc88ac00000000")
        self.tx_db[self.input_tx.hash()] = self.input_tx
        self.oracle = Oracle(keys=[self.wallet_key, self.recover_key], tx_db=self.tx_db)
        self.oracle.oracle_keys = [self.oracle_key]

    def test_payto(self):
        payto = self.oracle.payto(TEST_PATH)
        self.assertEqual("34DjTcNWGReJV4xx7R1AWK7FTz3xMwMcjA", payto.address())

    def test_sign(self):
        f = io.BytesIO(h2b("01000000019cb9e92cd3f91087852382150f19b5d99259be47106d860055d1afb8110022250000000000ffffffff01d06c04000000000017a914f155ba65bdb30930da320ec51a0d6c913dfce06b8700000000e09304000000000017a9141bbf6712630dd01fab4e70ac91a06925d138f27387"))

        unsigned = Tx.parse(f)
        unsigned.parse_unspents(f)
        script = self.oracle.script(TEST_PATH)
        lookup = build_p2sh_lookup([script.script()])
        child_key = self.wallet_private_key.subkey_for_path(TEST_PATH)
        db = LazySecretExponentDB([child_key.wif()], {})
        # FIXME hack to work around broken p2sh signing in pycoin
        unsigned.unspents[0].script = script.script()
        unsigned.sign(db, p2sh_lookup=lookup)
        req = self.oracle.create_oracle_request([TEST_PATH], [], None, unsigned)
        self.assertEqual("01000000019cb9e92cd3f91087852382150f19b5d99259be47106d860055d1afb81100222500000000b500473044022042b1b79675985a46e021c056708420f0bade9cdc4b336b55c53d0f22488f34e40220795cbd8291f083ea32eb29e8ace895852823611927b9ba7e94a333f022f5dd4301004c69522102fa0e06db47e8924274c670503238db30367d11ccaca00d385ac370fed93578d2210379014532a465b19fcf1ead9921488274821fd58178542b2aa54007bcc5a29d34210381c235ee18d9e85e3b28200200df3a2276c6b9473f18946ef8740ccaebfa4b1e53aeffffffff01d06c04000000000017a914f155ba65bdb30930da320ec51a0d6c913dfce06b8700000000",
                         req['transaction']['bytes'])
