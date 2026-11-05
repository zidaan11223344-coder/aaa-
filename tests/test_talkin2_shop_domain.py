import json
import uuid

import pytest

from talkin2_shop_domain import (
    DebitRejected,
    PurchaseRejected,
    ShopDomain,
    empty_state,
)


USER = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
ROOM = str(uuid.UUID("22222222-2222-4222-8222-222222222222"))


def make_shop(*, now=None, debit=None, refund=None, save=None, state=None):
    clock_value = [1_000.0 if now is None else now]
    ledger = {"debits": [], "refunds": []}

    def default_debit(user_id, amount):
        ledger["debits"].append((user_id, amount))
        return True

    def default_refund(user_id, amount):
        ledger["refunds"].append((user_id, amount))
        return True

    shop = ShopDomain(
        state=state,
        clock=lambda: clock_value[0],
        debit=debit or default_debit,
        refund=refund or default_refund,
        save=save,
    )
    return shop, clock_value, ledger


def test_temporary_protection_expires_and_can_be_bought_again_without_stacking():
    shop, clock, ledger = make_shop()
    first = shop.purchase(USER, 1)
    assert shop.active("user_bomb", user_id=USER)
    assert shop.remaining("user_bomb", user_id=USER) == 3600
    assert len(first["id"]) == 36
    assert uuid.UUID(first["id"])

    with pytest.raises(PurchaseRejected, match="already active"):
        shop.purchase(USER, 1)
    assert len(ledger["debits"]) == 1
    assert shop.state["users"][USER]["user_bomb"]["expires_at"] == 4600

    clock[0] += 3601
    assert not shop.active("user_bomb", user_id=USER)
    assert shop.remaining("user_bomb", user_id=USER) == 0
    second = shop.purchase(USER, 1)
    assert second["id"] != first["id"]
    assert shop.active("user_bomb", user_id=USER)
    assert shop.state["users"][USER]["user_bomb"]["expires_at"] == 8201


def test_room_protection_is_scoped_to_room_uuid_and_user_identity_is_stable():
    shop, _, _ = make_shop()
    grant = shop.purchase(USER, 2, room_id=ROOM)
    assert shop.active("room_bomb", room_id=ROOM)
    assert not shop.active("room_bomb", room_id=str(uuid.uuid4()))
    assert shop.state["rooms"][ROOM]["room_bomb"]["user_id"] == USER
    assert grant["room_id"] == ROOM


def test_welcome_unlock_is_permanent_and_duplicate_purchase_is_idempotently_rejected():
    shop, _, ledger = make_shop()
    first = shop.purchase(USER, 5)
    assert shop.active("custom_welcome", user_id=USER)
    assert not shop.active("image_welcome", user_id=USER)
    assert shop.state["users"][USER]["grants"]["custom_welcome"]["id"] == first["id"]

    with pytest.raises(PurchaseRejected, match="already unlocked"):
        shop.purchase(USER, 5)
    assert len(ledger["debits"]) == 1

    shop2 = ShopDomain(state=json.loads(json.dumps(shop.state)), clock=lambda: 99_999)
    assert shop2.active("custom_welcome", user_id=USER)


def test_debit_rejection_does_not_change_entitlements_or_save():
    saved = []
    shop, _, ledger = make_shop(debit=lambda *_args: False, save=lambda state: saved.append(state))
    with pytest.raises(DebitRejected):
        shop.purchase(USER, 1)
    assert shop.state == empty_state()
    assert not saved
    assert not ledger["refunds"]


def test_save_rejection_refunds_debit_and_does_not_publish_state():
    debits, refunds = [], []
    shop, _, _ = make_shop(
        debit=lambda uid, amount: debits.append((uid, amount)) or True,
        refund=lambda uid, amount: refunds.append((uid, amount)) or True,
        save=lambda _state: False,
    )
    with pytest.raises(PurchaseRejected, match="refunded"):
        shop.purchase(USER, 6)
    assert debits == [(USER, 12_000_000)]
    assert refunds == [(USER, 12_000_000)]
    assert shop.state == empty_state()


def test_save_exception_refunds_and_purchase_record_is_json_serializable():
    refunds = []
    shop, _, _ = make_shop(
        refund=lambda uid, amount: refunds.append((uid, amount)) or True,
        save=lambda _state: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(PurchaseRejected, match="refunded"):
        shop.purchase(USER, 3)
    assert refunds == [(USER, 8_000_000)]
    assert json.dumps(shop.state)


def test_invalid_identity_and_room_purchase_require_valid_uuid():
    shop, _, _ = make_shop()
    with pytest.raises(Exception):
        shop.purchase("not-a-uuid", 1)
    with pytest.raises(Exception):
        shop.purchase(USER, 2)
    with pytest.raises(Exception):
        shop.purchase(USER, 2, room_id="not-a-uuid")
