# -*- coding: utf-8 -*-
import json
from graphenecommon.aio.asset import Asset as GrapheneAsset

from bitsharesbase import operations
from bitsharesbase.asset_permissions import (
    asset_permissions,
    force_flag,
    test_permissions,
    todict,
)

from .instance import BlockchainInstance
from ..asset import Asset as SyncAsset


@BlockchainInstance.inject
class Asset(GrapheneAsset, SyncAsset):
    async def __init__(self, *args, **kwargs):
        await super().__init__(*args, **kwargs)

        # Permissions and flags
        self["permissions"] = todict(self["options"].get("issuer_permissions"))
        self["flags"] = todict(self["options"].get("flags"))
        try:
            self["description"] = json.loads(self["options"]["description"])
        except Exception:
            self["description"] = self["options"]["description"]

    def define_classes(self):
        self.type_id = 3

    @property
    async def max_market_fee(self):

        from .amount import Amount

        return await Amount(
            {"amount": self["options"]["max_market_fee"], "asset_id": self["id"]},
            blockchain_instance=self.blockchain,
        )

    @property
    async def feeds(self):
        from .price import PriceFeed

        await self.ensure_full()
        if not self.is_bitasset:
            return
        r = []
        for feed in self["bitasset_data"]["feeds"]:
            r.append(await PriceFeed(feed, blockchain_instance=self.blockchain))
        return r

    @property
    async def feed(self):
        from .price import PriceFeed

        assert self.is_bitasset
        await self.ensure_full()
        return await PriceFeed(
            self["bitasset_data"]["current_feed"], blockchain_instance=self.blockchain
        )

    @property
    async def calls(self):
        return await self.get_call_orders(10)

    async def get_call_orders(self, limit=100):
        from .price import Price
        from .account import Account
        from .amount import Amount

        assert limit <= 100
        assert self.is_bitasset
        await self.ensure_full()
        r = list()
        bitasset = self["bitasset_data"]
        settlement_price = await Price(
            bitasset["current_feed"]["settlement_price"],
            blockchain_instance=self.blockchain,
        )
        ret = await self.blockchain.rpc.get_call_orders(self["id"], limit)
        for call in ret[:limit]:
            call_price = await Price(
                call["call_price"], blockchain_instance=self.blockchain
            )
            collateral_amount = await Amount(
                {
                    "amount": call["collateral"],
                    "asset_id": call["call_price"]["base"]["asset_id"],
                },
                blockchain_instance=self.blockchain,
            )
            debt_amount = await Amount(
                {
                    "amount": call["debt"],
                    "asset_id": call["call_price"]["quote"]["asset_id"],
                },
                blockchain_instance=self.blockchain,
            )
            r.append(
                {
                    "account": await Account(
                        call["borrower"], lazy=True, blockchain_instance=self.blockchain
                    ),
                    "collateral": collateral_amount,
                    "debt": debt_amount,
                    "call_price": call_price,
                    "settlement_price": settlement_price,
                    "ratio": (
                        float(collateral_amount)
                        / float(debt_amount)
                        * float(settlement_price)
                    ),
                }
            )
        return r

    @property
    async def settlements(self):
        return await self.get_settle_orders(10)

    async def get_settle_orders(self, limit=100):
        from .account import Account
        from .amount import Amount
        from .utils import formatTimeString

        assert limit <= 100
        assert self.is_bitasset
        r = list()
        ret = await self.blockchain.rpc.get_settle_orders(self["id"], limit)
        for settle in ret[:limit]:
            r.append(
                {
                    "account": await Account(
                        settle["owner"], lazy=True, blockchain_instance=self.blockchain
                    ),
                    "amount": await Amount(
                        settle["balance"], blockchain_instance=self.blockchain
                    ),
                    "date": formatTimeString(settle["settlement_date"]),
                }
            )
        return r

    async def halt(self):
        """ Halt this asset from being moved or traded
        """
        from .account import Account

        nullaccount = await Account(
            "null-account",  # We set the null-account
            blockchain_instance=self.blockchain,
        )
        flags = {"white_list": True, "transfer_restricted": True}
        options = self["options"]
        test_permissions(options["issuer_permissions"], flags)
        flags_int = force_flag(options["flags"], flags)
        options.update(
            {
                "flags": flags_int,
                "whitelist_authorities": [nullaccount["id"]],
                "blacklist_authorities": [],
                "whitelist_markets": [self["id"]],
                "blacklist_markets": [],
            }
        )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return await self.blockchain.finalizeOp(op, self["issuer"], "active")

    async def release(
        self,
        whitelist_authorities=[],
        blacklist_authorities=[],
        whitelist_markets=[],
        blacklist_markets=[],
    ):
        """ Release this asset and allow unrestricted transfer, trading,
            etc.

            :param list whitelist_authorities: List of accounts that
                serve as whitelist authorities
            :param list blacklist_authorities: List of accounts that
                serve as blacklist authorities
            :param list whitelist_markets: List of assets to allow
                trading with
            :param list blacklist_markets: List of assets to prevent
                trading with
        """
        from .account import Account

        flags = {"white_list": False, "transfer_restricted": False}
        options = self["options"]
        test_permissions(options["issuer_permissions"], flags)
        flags_int = force_flag(options["flags"], flags)
        options.update(
            {
                "flags": flags_int,
                "whitelist_authorities": [
                    await Account(a, blockchain_instance=self.blockchain)["id"]
                    for a in whitelist_authorities
                ],
                "blacklist_authorities": [
                    await Account(a, blockchain_instance=self.blockchain)["id"]
                    for a in blacklist_authorities
                ],
                "whitelist_markets": [await Asset(a)["id"] for a in whitelist_markets],
                "blacklist_markets": [await Asset(a)["id"] for a in blacklist_markets],
            }
        )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return await self.blockchain.finalizeOp(op, self["issuer"], "active")

    async def setoptions(self, flags):
        """ Enable a certain flag.

            Flags:

             * charge_market_fee
             * white_list
             * override_authority
             * transfer_restricted
             * disable_force_settle
             * global_settle
             * disable_confidential
             * witness_fed_asset
             * committee_fed_asset

            :param dict flag: dictionary of flags and boolean
        """
        op = super().setoptions(flags, return_op=True)
        return await self.blockchain.finalizeOp(op, self["issuer"], "active")

    async def enableflag(self, flag):
        """ Enable a certain flag.

            :param str flag: Flag name
        """
        return await self.setoptions({flag: True})

    async def disableflag(self, flag):
        """ Enable a certain flag.

            :param str flag: Flag name
        """
        return await self.setoptions({flag: False})

    async def seize(self, *args):
        """ Seize amount from an account and send to another

            ... note:: This requires the ``override_authority`` to be
                       set for this asset!

            :param bitshares.account.Account from_account: From this account
            :param bitshares.account.Account to_account: To this account
            :param bitshares.amount.Amount amount: Amount to seize
        """
        op = super().seize(*args, return_op=True)
        return await self.blockchain.finalizeOp(op, self["issuer"], "active")

    def add_authorities(self, type, authorities=[]):
        """ Add authorities to an assets white/black list

            :param str type: ``blacklist`` or ``whitelist``
            :param list authorities: List of authorities (Accounts)
        """
        assert type in ["blacklist", "whitelist"]
        assert isinstance(authorities, (list, set))
        from .account import Account

        options = self["options"]
        if type == "whitelist":
            options["whitelist_authorities"].extend(
                [
                    Account(a, blockchain_instance=self.blockchain)["id"]
                    for a in authorities
                ]
            )
        if type == "blacklist":
            options["blacklist_authorities"].extend(
                [
                    Account(a, blockchain_instance=self.blockchain)["id"]
                    for a in authorities
                ]
            )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def remove_authorities(self, type, authorities=[]):
        """ Remove authorities from an assets white/black list

            :param str type: ``blacklist`` or ``whitelist``
            :param list authorities: List of authorities (Accounts)
        """
        assert type in ["blacklist", "whitelist"]
        assert isinstance(authorities, (list, set))
        from .account import Account

        options = self["options"]
        if type == "whitelist":
            for a in authorities:
                options["whitelist_authorities"].remove(
                    Account(a, blockchain_instance=self.blockchain)["id"]
                )
        if type == "blacklist":
            for a in authorities:
                options["blacklist_authorities"].remove(
                    Account(a, blockchain_instance=self.blockchain)["id"]
                )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def add_markets(self, type, authorities=[], force_enable=True):
        """ Add markets to an assets white/black list

            :param str type: ``blacklist`` or ``whitelist``
            :param list markets: List of markets (assets)
            :param bool force_enable: Force enable ``white_list`` flag
        """
        assert type in ["blacklist", "whitelist"]
        assert isinstance(authorities, (list, set))

        options = self["options"]
        if force_enable:
            test_permissions(options["issuer_permissions"], {"white_list": True})
            flags_int = force_flag(options["flags"], {"white_list": True})
            options.update({"flags": flags_int})
        else:
            assert test_permissions(
                options["flags"], ["white_list"]
            ), "whitelist feature not enabled"

        if type == "whitelist":
            options["whitelist_markets"].extend(
                [
                    Asset(a, blockchain_instance=self.blockchain)["id"]
                    for a in authorities
                ]
            )
        if type == "blacklist":
            options["blacklist_markets"].extend(
                [
                    Asset(a, blockchain_instance=self.blockchain)["id"]
                    for a in authorities
                ]
            )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def remove_markets(self, type, authorities=[]):
        """ Remove markets from an assets white/black list

            :param str type: ``blacklist`` or ``whitelist``
            :param list markets: List of markets (assets)
        """
        assert type in ["blacklist", "whitelist"]
        assert isinstance(authorities, (list, set))

        options = self["options"]
        if type == "whitelist":
            for a in authorities:
                options["whitelist_markets"].remove(
                    Asset(a, blockchain_instance=self.blockchain)["id"]
                )
        if type == "blacklist":
            for a in authorities:
                options["blacklist_markets"].remove(
                    Asset(a, blockchain_instance=self.blockchain)["id"]
                )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def set_market_fee(self, percentage_fee, max_market_fee):
        """ Set trading percentage fee

            :param float percentage_fee: Percentage of fee
            :param bitshares.amount.Amount max_market_fee: Max Fee

        """
        assert percentage_fee <= 100 and percentage_fee > 0
        flags = {"charge_market_fee": percentage_fee > 0}
        options = self["options"]
        test_permissions(options["issuer_permissions"], flags)
        flags_int = force_flag(options["flags"], flags)
        options.update(
            {
                "flags": flags_int,
                "market_fee_percent": percentage_fee * 100,
                "max_market_fee": int(max_market_fee),
            }
        )
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_options": options,
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def update_feed_producers(self, producers):
        """ Update bitasset feed producers

            :param list producers: List of accounts that are allowed to produce
                 a feed
        """
        assert self.is_bitasset, "Asset needs to be a bitasset/market pegged asset"
        from .account import Account

        op = operations.Asset_update_feed_producers(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_feed_producers": [
                    Account(a, blockchain_instance=self.blockchain)["id"]
                    for a in producers
                ],
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active")

    def change_issuer(self, new_issuer, **kwargs):
        """ Change asset issuer (needs signing with owner key!)

            :param str type: ``blacklist`` or ``whitelist``
            :param list authorities: List of authorities (Accounts)
        """
        from .account import Account

        new_issuer = Account(new_issuer, blockchain_instance=self.blockchain)
        op = operations.Asset_update_issuer(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": self["issuer"],
                "asset_to_update": self["id"],
                "new_issuer": new_issuer["id"],
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "owner", **kwargs)

    def issue(self, amount, to, memo=None, **kwargs):
        """ Issue new shares of an asset

            :param float amount: Amount to issue
            :param str to: Recipient
            :param str memo: (optional) Memo message
        """
        from .memo import Memo
        from .account import Account

        to = Account(to, blockchain_instance=self.blockchain)
        account = Account(self["issuer"], blockchain_instance=self.blockchain)
        memoObj = Memo(
            from_account=account, to_account=to, blockchain_instance=self.blockchain
        )

        # append operation
        op = operations.Asset_issue(
            **{
                "fee": {
                    "amount": 0,
                    "asset_id": "1.3.0",
                },  # Will be filled in automatically
                "issuer": account["id"],  # the Issuer account
                "asset_to_issue": {
                    "amount": int(amount * 10 ** self["precision"]),
                    "asset_id": self["id"],
                },
                "issue_to_account": to["id"],
                "memo": memoObj.encrypt(memo),
                "extensions": [],
            }
        )
        return self.blockchain.finalizeOp(op, self["issuer"], "active", **kwargs)