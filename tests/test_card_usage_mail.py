from scripts.card_usage_mail import (
    CardUsageMail,
    handle_card_usage_mail,
    is_ana_pay_charge_notification,
)

ANA_PAY_BODY = """水出　和宏 様
カード名称　：　ＡＮＡＪＣＢＳＦＣゴールドカード

いつもＡＮＡＪＣＢＳＦＣゴールドカードをご利用いただきありがとうございます。
JCBカードのご利用がありましたのでご連絡します。

【ご利用日時(日本時間)】　2026/06/17 08:40
【ご利用金額】　10,000円
【ご利用先】　エイエヌエ－　ペイ
"""


def _body_with_merchant(merchant: str) -> str:
    return ANA_PAY_BODY.replace("エイエヌエ－　ペイ", merchant)


def test_ana_pay_charge_is_forwarded_without_notion_registration(caplog) -> None:
    notion_calls = []
    forwarded = []

    with caplog.at_level("INFO"):
        result = handle_card_usage_mail(
            CardUsageMail(subject="JCB利用通知", body=ANA_PAY_BODY),
            register_to_notion=lambda mail: notion_calls.append(mail),
            forward_email=lambda mail: forwarded.append(mail),
        )

    assert result is None
    assert notion_calls == []
    assert len(forwarded) == 1
    assert (
        "ANA Pay charge detected. Skip Notion registration and forward email."
        in caplog.text
    )


def test_normal_merchant_is_registered_to_notion() -> None:
    notion_calls = []
    forwarded = []
    mail = CardUsageMail(subject="JCB利用通知", body=_body_with_merchant("通常店舗"))

    result = handle_card_usage_mail(
        mail,
        register_to_notion=lambda mail: notion_calls.append(mail) or "registered",
        forward_email=lambda mail: forwarded.append(mail),
    )

    assert result == "registered"
    assert notion_calls == [mail]
    assert forwarded == []


def test_ana_pay_notation_variants_are_detected() -> None:
    for merchant in ["ANA Pay", "ANAPAY", "エイエヌエー ペイ", "ｴｲｴﾇｴｰﾍﾟｲ"]:
        assert is_ana_pay_charge_notification(_body_with_merchant(merchant))


def test_ana_pay_detection_does_not_call_notion_function() -> None:
    def must_not_register(mail: CardUsageMail) -> None:
        raise AssertionError("Notion registration must not be called")

    forwarded = []
    handle_card_usage_mail(
        CardUsageMail(subject="JCB利用通知", body=_body_with_merchant("ANAPAY")),
        register_to_notion=must_not_register,
        forward_email=lambda mail: forwarded.append(mail),
    )

    assert len(forwarded) == 1
