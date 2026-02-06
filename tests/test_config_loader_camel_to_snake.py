from nanobot.config.loader import camel_to_snake


def test_camel_to_snake_basic() -> None:
    assert camel_to_snake("bridgeUrl") == "bridge_url"
    assert camel_to_snake("apiKey") == "api_key"


def test_camel_to_snake_acronyms() -> None:
    assert camel_to_snake("allowIPv6") == "allow_ipv6"
    assert camel_to_snake("MyURLParser") == "my_url_parser"

