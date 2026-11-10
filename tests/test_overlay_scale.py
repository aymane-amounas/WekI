def test_overlay_handles_ten_thousand_articles(store):
    article_count = 10_000
    with store.transaction():
        for index in range(article_count):
            store.upsert_article(
                f"Scale_{index}",
                f"Scale article {index}",
                f"<p>scalemark shared topic{index % 100}</p>",
                f"scalemark shared topic{index % 100}",
                index + 1,
                "2026-11-01T00:00:00Z",
            )

    assert store.counts()["articles"] == article_count
    assert len(store.search("scalemark")) == 30
    assert store.article("Scale_9999").rev_id == article_count

    store.upsert_article(
        "Scale_9999",
        "Scale article 9999",
        "<p>updatedmarker</p>",
        "updatedmarker",
        article_count + 1,
        "2026-11-02T00:00:00Z",
    )
    assert store.counts()["articles"] == article_count
    assert store.search("updatedmarker")[0].path == "Scale_9999"
