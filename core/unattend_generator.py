"""Generator for `autounattend.xml` (Windows Setup Answer File).

Built via `xml.etree.ElementTree` (never string concatenation), so the
output is always well-formed regardless of which options are enabled -
see `win-iso-customizer-prompt.md` section 3.3 for the functional spec.

LEGITIMACY NOTE: the hardware-check and account-screen bypass options here
only skip Windows Setup's TPM/Secure Boot/RAM/account gating during image
preparation (the same category of tooling as NTLite/MSMG ToolKit). They do
not touch Windows activation or licensing.

IMPORTANT - verify before relying on this in production: the LabConfig
registry bypass for TPM/Secure Boot/RAM/CPU/Storage checks, and the
LabConfig BypassNRO key, are the mechanisms Windows Setup has honored since
Windows 11 launched, but Microsoft has adjusted enforcement around them
across builds and may again. Test against the specific Windows 11 build/ISO
you are deploying before relying on this for a production rollout. Element
ordering below follows the layout consistently seen in published
Microsoft-Windows-Shell-Setup/Microsoft-Windows-Setup answer-file samples;
if you have a Windows ADK install, pass its unattend XSDs to
`validate_against_xsd()` to confirm independently.
"""

from __future__ import annotations

import base64
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("wct.unattend_generator")

_UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
_WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"

ET.register_namespace("", _UNATTEND_NS)
ET.register_namespace("wcm", _WCM_NS)

_PRODUCT_KEY_RE = re.compile(r"^[A-Za-z0-9]{5}-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}$")
_INVALID_COMPUTER_NAME_CHARS = set('\\/:*?"<>|,')
_INVALID_USERNAME_CHARS = set('"/\\[]:;|=,+*?<>')


@dataclass
class LocalUserAccount:
    """A local account to provision via the oobeSystem pass.

    `password` is held in cleartext on this object only for the duration
    of generation. Callers must NOT serialize a `LocalUserAccount` (or any
    config containing one) into a saved preset file in plaintext - that is
    a GUI/preset-storage concern this module cannot enforce, only warn
    about (see `write_unattend_xml`).
    """

    name: str
    password: str
    group: str = "Administrators"
    display_name: str | None = None


@dataclass
class RegionalSettings:
    input_locale: str = "en-US"
    system_locale: str = "en-US"
    ui_language: str = "en-US"
    user_locale: str = "en-US"
    timezone: str = "UTC"


@dataclass
class UnattendConfig:
    """All options needed to render one `autounattend.xml`.

    A typed dataclass rather than a bare dict, so options are discoverable
    and defaulted in one place instead of scattered string keys.
    """

    bypass_tpm_check: bool = True
    bypass_secure_boot_check: bool = True
    bypass_ram_check: bool = True
    bypass_storage_check: bool = True
    bypass_cpu_check: bool = True
    bypass_nro: bool = True

    local_user: LocalUserAccount | None = None
    plaintext_password_in_xml: bool = False

    regional: RegionalSettings = field(default_factory=RegionalSettings)
    computer_name: str | None = None
    product_key: str | None = None

    hide_eula_page: bool = True
    skip_machine_oobe: bool = True


class UnattendValidationError(ValueError):
    """Raised when a config or the generated XML fails validation."""


def _validate_config(config: UnattendConfig) -> None:
    if config.local_user is not None:
        user = config.local_user
        if not user.name.strip():
            raise UnattendValidationError("local_user.name must not be empty")
        if len(user.name) > 20:
            raise UnattendValidationError("local_user.name must be 20 characters or fewer")
        if any(ch in _INVALID_USERNAME_CHARS for ch in user.name):
            raise UnattendValidationError(f"local_user.name {user.name!r} contains invalid characters")
        if not user.password:
            raise UnattendValidationError(
                "local_user.password must not be empty - Windows Setup rejects a "
                "blank local account password unless the account is intentionally "
                "left password-less, which this generator does not support"
            )
        if user.group not in {"Administrators", "Users"}:
            logger.warning(
                "local_user.group %r is not one of the built-in groups "
                "(Administrators/Users) - Windows Setup will fail if this group "
                "doesn't exist on the target system.",
                user.group,
            )

    if config.bypass_nro and config.local_user is None:
        logger.warning(
            "bypass_nro is enabled without a local_user configured - OOBE will "
            "still prompt for account creation with no provisioned local "
            "account to fall back to."
        )

    if config.product_key and not _PRODUCT_KEY_RE.match(config.product_key):
        raise UnattendValidationError(
            f"product_key {config.product_key!r} doesn't match the expected "
            "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX format"
        )

    if config.computer_name:
        if len(config.computer_name) > 15:
            raise UnattendValidationError("computer_name must be 15 characters or fewer (NetBIOS limit)")
        if any(ch in _INVALID_COMPUTER_NAME_CHARS for ch in config.computer_name):
            raise UnattendValidationError(
                f"computer_name {config.computer_name!r} contains invalid characters"
            )


def _obfuscate_password(password: str, suffix: str) -> str:
    """Reproduce Microsoft's unattend 'obfuscated' password encoding.

    This is Base64(UTF-16LE(password + suffix)) - the same scheme Windows
    System Image Manager uses when `PlainText` is set to false. It is NOT
    encryption (Microsoft documents it as reversible obfuscation only), but
    it avoids the password appearing as a bare string when the XML is
    glanced at, grepped, or logged.
    """
    return base64.b64encode((password + suffix).encode("utf-16-le")).decode("ascii")


def _q(tag: str) -> str:
    return f"{{{_UNATTEND_NS}}}{tag}"


def _qwcm(name: str) -> str:
    return f"{{{_WCM_NS}}}{name}"


def _component_attrs(name: str) -> dict[str, str]:
    return {
        "name": name,
        "processorArchitecture": "amd64",
        "publicKeyToken": "31bf3856ad364e35",
        "language": "neutral",
        "versionScope": "nonSxS",
    }


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _hardware_bypass_commands(config: UnattendConfig) -> list[str]:
    checks = [
        (config.bypass_tpm_check, "BypassTPMCheck"),
        (config.bypass_secure_boot_check, "BypassSecureBootCheck"),
        (config.bypass_ram_check, "BypassRAMCheck"),
        (config.bypass_storage_check, "BypassStorageCheck"),
        (config.bypass_cpu_check, "BypassCPUCheck"),
    ]
    commands = [
        f"reg add HKLM\\SYSTEM\\Setup\\LabConfig /v {value_name} /t REG_DWORD /d 1 /f"
        for enabled, value_name in checks
        if enabled
    ]
    if config.bypass_nro:
        commands.append("reg add HKLM\\SYSTEM\\Setup\\LabConfig /v BypassNRO /t REG_DWORD /d 1 /f")
    return commands


def _build_windows_pe_pass(unattend: ET.Element, config: UnattendConfig) -> None:
    settings = ET.SubElement(unattend, _q("settings"), {"pass": "windowsPE"})

    intl = ET.SubElement(settings, _q("component"), _component_attrs("Microsoft-Windows-International-Core-WinPE"))
    setup_ui_lang = ET.SubElement(intl, _q("SetupUILanguage"))
    ET.SubElement(setup_ui_lang, _q("UILanguage")).text = config.regional.ui_language
    ET.SubElement(intl, _q("InputLocale")).text = config.regional.input_locale
    ET.SubElement(intl, _q("SystemLocale")).text = config.regional.system_locale
    ET.SubElement(intl, _q("UILanguage")).text = config.regional.ui_language
    ET.SubElement(intl, _q("UserLocale")).text = config.regional.user_locale

    setup = ET.SubElement(settings, _q("component"), _component_attrs("Microsoft-Windows-Setup"))
    user_data = ET.SubElement(setup, _q("UserData"))
    ET.SubElement(user_data, _q("AcceptEula")).text = "true"

    commands = _hardware_bypass_commands(config)
    if commands:
        run_sync = ET.SubElement(setup, _q("RunSynchronous"))
        for order, command in enumerate(commands, start=1):
            cmd_el = ET.SubElement(run_sync, _q("RunSynchronousCommand"), {_qwcm("action"): "add"})
            ET.SubElement(cmd_el, _q("Order")).text = str(order)
            ET.SubElement(cmd_el, _q("Path")).text = command


def _build_specialize_pass(unattend: ET.Element, config: UnattendConfig) -> None:
    settings = ET.SubElement(unattend, _q("settings"), {"pass": "specialize"})

    intl = ET.SubElement(settings, _q("component"), _component_attrs("Microsoft-Windows-International-Core"))
    ET.SubElement(intl, _q("InputLocale")).text = config.regional.input_locale
    ET.SubElement(intl, _q("SystemLocale")).text = config.regional.system_locale
    ET.SubElement(intl, _q("UILanguage")).text = config.regional.ui_language
    ET.SubElement(intl, _q("UserLocale")).text = config.regional.user_locale

    shell = ET.SubElement(settings, _q("component"), _component_attrs("Microsoft-Windows-Shell-Setup"))
    ET.SubElement(shell, _q("TimeZone")).text = config.regional.timezone
    if config.computer_name:
        ET.SubElement(shell, _q("ComputerName")).text = config.computer_name
    if config.product_key:
        ET.SubElement(shell, _q("ProductKey")).text = config.product_key


def _set_password(password_element: ET.Element, password: str, suffix: str, plaintext: bool) -> None:
    value_el = ET.SubElement(password_element, _q("Value"))
    plaintext_el = ET.SubElement(password_element, _q("PlainText"))
    if plaintext:
        value_el.text = password
        plaintext_el.text = "true"
    else:
        value_el.text = _obfuscate_password(password, suffix)
        plaintext_el.text = "false"


def _build_oobe_system_pass(unattend: ET.Element, config: UnattendConfig) -> None:
    settings = ET.SubElement(unattend, _q("settings"), {"pass": "oobeSystem"})
    shell = ET.SubElement(settings, _q("component"), _component_attrs("Microsoft-Windows-Shell-Setup"))

    oobe = ET.SubElement(shell, _q("OOBE"))
    ET.SubElement(oobe, _q("HideEULAPage")).text = _bool(config.hide_eula_page)
    if config.bypass_nro:
        ET.SubElement(oobe, _q("HideOnlineAccountScreens")).text = "true"
        ET.SubElement(oobe, _q("HideWirelessSetupInOOBE")).text = "true"
    ET.SubElement(oobe, _q("NetworkLocation")).text = "Work"
    ET.SubElement(oobe, _q("ProtectYourPC")).text = "3"
    ET.SubElement(oobe, _q("SkipMachineOOBE")).text = _bool(config.skip_machine_oobe)
    ET.SubElement(oobe, _q("SkipUserOOBE")).text = _bool(config.local_user is not None)

    if config.local_user is not None:
        user_accounts = ET.SubElement(shell, _q("UserAccounts"))
        local_accounts = ET.SubElement(user_accounts, _q("LocalAccounts"))
        account = ET.SubElement(local_accounts, _q("LocalAccount"), {_qwcm("action"): "add"})
        password_el = ET.SubElement(account, _q("Password"))
        _set_password(
            password_el,
            config.local_user.password,
            "Password",
            config.plaintext_password_in_xml,
        )
        ET.SubElement(account, _q("DisplayName")).text = (
            config.local_user.display_name or config.local_user.name
        )
        ET.SubElement(account, _q("Group")).text = config.local_user.group
        ET.SubElement(account, _q("Name")).text = config.local_user.name


def _validate_well_formed(xml_text: str) -> None:
    try:
        ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UnattendValidationError(f"Generated XML is not well-formed: {exc}") from exc


def validate_against_xsd(xml_path: str | Path, xsd_path: str | Path) -> None:
    """Validate a generated answer file against a local unattend XSD.

    Requires the optional `lxml` package (not a hard dependency of this
    project - `pip install lxml` if you want this check) and a local copy
    of the Microsoft unattend schema, typically found inside a Windows ADK
    install under `.../Deployment Tools/<arch>/WSIM/`. Neither is bundled
    here.
    """
    try:
        from lxml import etree as lxml_etree
    except ImportError as exc:
        raise ImportError(
            "XSD validation requires the optional 'lxml' package "
            "(pip install lxml), which is not installed."
        ) from exc

    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(xsd_path)))
    xml_doc = lxml_etree.parse(str(xml_path))
    if not schema.validate(xml_doc):
        raise UnattendValidationError(
            "Generated unattend.xml failed schema validation:\n"
            + "\n".join(str(e) for e in schema.error_log)
        )


def build_unattend_tree(config: UnattendConfig) -> ET.ElementTree:
    """Build the answer file as an in-memory ElementTree (no I/O)."""
    _validate_config(config)
    root = ET.Element(_q("unattend"))
    _build_windows_pe_pass(root, config)
    _build_specialize_pass(root, config)
    _build_oobe_system_pass(root, config)
    return ET.ElementTree(root)


def render_unattend_xml(config: UnattendConfig) -> str:
    """Render `config` to an XML string, guaranteed well-formed."""
    tree = build_unattend_tree(config)
    root = tree.getroot()
    ET.indent(tree, space="    ")
    xml_text = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    _validate_well_formed(xml_text)
    return xml_text


def write_unattend_xml(
    config: UnattendConfig,
    output_path: str | Path,
    *,
    xsd_path: str | Path | None = None,
) -> Path:
    """Render `config` and write it to `output_path` as `autounattend.xml`.

    If `xsd_path` is given, additionally validates against that schema
    (see `validate_against_xsd`) before returning.
    """
    xml_text = render_unattend_xml(config)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_text, encoding="utf-8")
    logger.info("Wrote unattend answer file to %s", output_path)

    if xsd_path is not None:
        validate_against_xsd(output_path, xsd_path)
        logger.info("Validated %s against schema %s", output_path, xsd_path)

    if config.local_user is not None:
        if config.plaintext_password_in_xml:
            logger.warning(
                "Local account password was written in PLAINTEXT inside %s. "
                "Treat this file as sensitive: do not commit it to source "
                "control or leave it in a shared build directory.",
                output_path,
            )
        else:
            logger.info(
                "Local account password was written in obfuscated (not "
                "encrypted) form, matching Microsoft's own answer-file "
                "convention. %s should still be treated as sensitive.",
                output_path,
            )

    return output_path
