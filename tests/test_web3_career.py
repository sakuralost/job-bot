from worker.web3_career import _is_product_title, _records, _strip_html


def test_web3_api_records_supports_documentation_prefix():
    payload = ["Web3 Jobs API documentation", [{"id": 7, "title": "Product Manager"}]]
    assert _records(payload) == [{"id": 7, "title": "Product Manager"}]


def test_web3_title_filter_keeps_only_product_management_roles():
    assert _is_product_title("Senior Product Manager, Wallet")
    assert _is_product_title("Product Owner")
    assert not _is_product_title("Product Marketing Manager")


def test_web3_html_description_is_readable_for_llm():
    assert _strip_html("<h1>Role</h1><p>English &amp; Chinese</p>") == "Role English & Chinese"
