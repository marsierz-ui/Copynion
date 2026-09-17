"""Redaction, policy and vault behaviour."""

from __future__ import annotations

import pytest

from copynion.models import Observation, Visibility
from copynion.privacy import Policy, Redactor, Vault, VaultError

# -- redaction ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, must_not_contain, expected_rule",
    [
        ("Mail from john.doe@acme.com", "john.doe@acme.com", "email"),
        ("password: hunter2", "hunter2", "secret_assignment"),
        ("api_key = sk-lmnoQRSTuvwx1234yz", "sk-lmno", "secret_assignment"),
        ("Payment DE89 3704 0044 0532 0130 00", "3704", "iban"),
        ("Card 4111 1111 1111 1111 charged", "4111 1111", "card"),
        ("Call +48 123 456 789 now", "123 456 789", "phone"),
        ("https://x.example/reset?token=abc123def", "abc123def", "url_query"),
        ("/home/marcin/taxes.ods", "marcin", "home_path"),
        ("C:\\Users\\Marcin\\taxes.xlsx", "Marcin", "home_path"),
        ("Order 987654321 shipped", "987654321", "long_number"),
        ("d41d8cd98f00b204e9800998ecf8427e report", "d41d8cd98f00b204", "hex_secret"),
    ],
)
def test_sensitive_fragments_are_removed(raw, must_not_contain, expected_rule):
    report = Redactor().scrub(raw)
    assert must_not_contain not in report.text
    assert expected_rule in report.hits, f"expected rule {expected_rule}, got {report.hits}"


def test_jwt_is_removed():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
    report = Redactor().scrub(f"Debugging {token} in console")
    assert token not in report.text
    assert "<jwt>" in report.text


def test_ordinary_titles_survive_redaction():
    """Over-redaction would destroy categorisation; normal titles must pass through."""
    for title in [
        "sampler.py - copynion - Visual Studio Code",
        "GitHub - marsierz-ui/copynion: Pull requests",
        "Inbox - Thunderbird",
        "Q3 budget - LibreOffice Calc",
    ]:
        assert Redactor().scrub(title).text == title


def test_custom_patterns_are_applied():
    r = Redactor(custom_patterns=[r"ProjectNeptune"])
    out = r.scrub("ProjectNeptune roadmap - Notion").text
    assert "ProjectNeptune" not in out
    assert "<redacted>" in out


def test_invalid_custom_pattern_is_rejected_loudly():
    with pytest.raises(ValueError, match="invalid custom redaction pattern"):
        Redactor(custom_patterns=["(unclosed"])


def test_long_titles_are_truncated():
    report = Redactor(max_length=40).scrub("x" * 500)
    assert len(report.text) <= 43
    assert report.truncated


def test_disabled_redactor_still_normalises_but_does_not_scrub():
    r = Redactor(enabled=False)
    out = r.scrub("mail   from  a@b.com").text
    assert out == "mail from a@b.com"


# -- policy -------------------------------------------------------------------

def test_default_policy_withholds_titles():
    """An app nobody configured must not have its titles recorded."""
    permitted, decision = Policy().apply(Observation.now("someapp", "Secret client - Acme Corp"))
    assert decision.visibility is Visibility.APP_ONLY
    assert permitted.title == ""
    assert permitted.app == "someapp"


def test_app_rule_enables_full_capture():
    permitted, decision = Policy(app_rules={"code": "full"}).apply(
        Observation.now("code", "main.py")
    )
    assert decision.visibility is Visibility.FULL
    assert permitted.title == "main.py"


def test_password_managers_are_protected_by_default():
    for app in ("1password", "bitwarden", "keepassxc"):
        _, decision = Policy().apply(Observation.now(app, "Personal vault - Bank login"))
        assert decision.visibility is Visibility.APP_ONLY, app


def test_user_rule_can_override_a_sensitive_default():
    """The defaults are protective, not paternalistic - the user stays in charge."""
    _, decision = Policy(app_rules={"1password": "drop"}).apply(Observation.now("1password", "x"))
    assert decision.visibility is Visibility.DROP


def test_title_denylist_drops_everything():
    policy = Policy(app_rules={"code": "full"}, title_denylist=[r"AcmeCorp"])
    permitted, decision = policy.apply(Observation.now("code", "AcmeCorp contract.docx"))
    assert permitted is None
    assert decision.visibility is Visibility.DROP


def test_private_browsing_is_respected():
    policy = Policy(app_rules={"firefox": "full"})
    permitted, decision = policy.apply(Observation.now("firefox", "Search (Private Browsing)"))
    assert decision.visibility is Visibility.APP_ONLY
    assert permitted.title == ""


def test_pause_drops_everything():
    policy = Policy(app_rules={"code": "full"})
    policy.pause("lunch")
    permitted, decision = policy.apply(Observation.now("code", "main.py"))
    assert permitted is None
    assert "lunch" in decision.reason
    policy.resume()
    assert policy.apply(Observation.now("code", "main.py"))[0].title == "main.py"


def test_unknown_window_is_opaque_not_guessed():
    permitted, decision = Policy().apply(Observation.now("", ""))
    assert decision.visibility is Visibility.OPAQUE
    assert permitted.app == ""


# -- vault --------------------------------------------------------------------

def test_seal_open_roundtrip(vault):
    blob = vault.seal("Quarterly report - Acme")
    assert b"Quarterly" not in blob
    assert vault.open(blob) == "Quarterly report - Acme"


def test_ciphertext_differs_each_time(vault):
    """A deterministic ciphertext would leak which titles repeat."""
    assert vault.seal("same text") != vault.seal("same text")


def test_wrong_key_cannot_decrypt(vault):
    blob = vault.seal("secret")
    with pytest.raises(VaultError, match="failed authentication"):
        Vault(Vault.generate_key()).open(blob)


def test_tampered_ciphertext_is_rejected(vault):
    blob = bytearray(vault.seal("secret"))
    blob[-1] ^= 0xFF
    with pytest.raises(VaultError):
        vault.open(bytes(blob))


def test_vault_fails_closed_without_a_key():
    """No key must mean no plaintext on disk - not a silent downgrade."""
    assert Vault(None).seal("secret") is None
    assert Vault(None).can_store_titles is False


def test_explicit_opt_in_allows_unencrypted():
    v = Vault(None, allow_unencrypted=True)
    blob = v.seal("secret")
    assert blob is not None
    assert v.open(blob) == "secret"


def test_fingerprint_is_stable_and_keyed(vault):
    assert vault.fingerprint("report.ods") == vault.fingerprint("report.ods")
    assert vault.fingerprint("a") != vault.fingerprint("b")
    other = Vault(Vault.generate_key())
    assert vault.fingerprint("report.ods") != other.fingerprint("report.ods")


def test_key_file_permissions_are_enforced(tmp_path):
    key_path = tmp_path / "copynion.key"
    Vault.load(key_path, create=True, use_keyring=False)
    assert key_path.stat().st_mode & 0o777 == 0o600
    key_path.chmod(0o644)
    with pytest.raises(VaultError, match="readable by other users"):
        Vault.load(key_path, use_keyring=False)


def test_key_location_is_reported_honestly(tmp_path):
    v = Vault.load(tmp_path / "k.key", create=True, use_keyring=False)
    assert v.key_location.startswith("file:")
    assert "0600 file" in v.describe_key_location()
