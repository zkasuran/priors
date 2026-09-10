from priors import onchain as oc


def test_encode_attestation_is_stable_and_nonempty():
    a = oc.encode_attestation_data("image", "0xdb6c6340342e71a63cd11ebac2185204b7777777", "avoid", 2, "stiffed 2x")
    b = oc.encode_attestation_data("image", "0xDB6c6340342e71A63cD11Ebac2185204b7777777", "avoid", 2, "stiffed 2x")
    assert a == b and len(a) > 0


def test_schema_uid_is_deterministic():
    assert oc.schema_uid() == oc.schema_uid()
    assert oc.schema_uid().startswith("0x") and len(oc.schema_uid()) == 66


def test_score_is_clamped_into_uint8():
    # should not raise for out-of-range scores
    oc.encode_attestation_data("image", "0x" + "11" * 20, "transact", 999, "x")
    oc.encode_attestation_data("image", "0x" + "11" * 20, "transact", -5, "x")


def test_attest_without_key_returns_prepared_unsent():
    p = oc.attest_outcome("0x" + "11" * 20, "image", "avoid", 2, "stiffed 2x", private_key=None)
    assert p["sent"] is False
    assert p["data_hex"].startswith("0x")
    assert p["schema_uid"].startswith("0x")
    assert p["eas"] == oc.EAS_ADDRESS
