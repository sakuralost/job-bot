from worker.remote3 import _company_and_location, _published_at, _remote_value, _rss_items


def test_rss_item_and_metadata_are_normalized():
    xml = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>Product Manager</title>
      <link>https://remote3.co/remote-jobs/example</link>
      <guid>https://remote3.co/remote-jobs/example</guid>
      <pubDate>Tue, 11 Aug 2026 04:00:02 GMT</pubDate>
      <description>at Small Builder - Full-Time - Worldwide</description>
    </item></channel></rss>"""

    items = _rss_items(xml)

    assert items[0]["title"] == "Product Manager"
    assert _company_and_location(items[0]["description"]) == ("Small Builder", "Worldwide")
    assert _remote_value("Worldwide") is True
    assert _published_at(items[0]["pubDate"]).isoformat() == "2026-08-11T04:00:02+00:00"
