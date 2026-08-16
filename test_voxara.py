from datetime import datetime, timezone

import pytest

from voxara import SourceRef, Voxara, VoxaraError


T1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)


def test_publish_and_provenance_are_deterministic():
    v = Voxara()
    article = v.publish(
        title='Grid Update',
        body='Demand remained stable.',
        author='Desk',
        sources=[SourceRef('Utility', 'https://example.test/u')],
        created_at=T1,
    )
    p1 = v.provenance(article.article_id)
    p2 = v.provenance(article.article_id)
    assert p1 == p2
    assert p1['revision_count'] == 1
    assert p1['status'] == 'published'


def test_duplicate_content_is_rejected():
    v = Voxara()
    v.publish(title='A', body='Same', author='One', created_at=T1)
    with pytest.raises(VoxaraError, match='duplicate content'):
        v.publish(title='A', body='Same', author='Two', created_at=T2)


def test_correction_creates_immutable_chain():
    v = Voxara()
    first = v.publish(title='A', body='Initial', author='Desk', created_at=T1)
    second = v.correct(first.article_id, body='Corrected', author='Editor', created_at=T2)
    history = v.history(first.article_id)
    assert len(history) == 2
    assert history[0] == first
    assert second.corrects_revision_id == first.revision_id
    assert second.version == 2
    assert second.status == 'corrected'


def test_retraction_hides_from_default_feed():
    v = Voxara()
    first = v.publish(title='A', body='Initial', author='Desk', created_at=T1)
    v.retract(first.article_id, author='Editor', reason='Source withdrawn', created_at=T2)
    assert v.feed() == []
    assert v.feed(include_retracted=True)[0].status == 'retracted'


def test_retracted_article_cannot_be_corrected():
    v = Voxara()
    first = v.publish(title='A', body='Initial', author='Desk', created_at=T1)
    v.retract(first.article_id, author='Editor', reason='Invalid', created_at=T2)
    with pytest.raises(VoxaraError, match='cannot be corrected'):
        v.correct(first.article_id, body='Again', author='Editor')


def test_sources_are_normalized_and_sorted():
    v = Voxara()
    article = v.publish(
        title='A',
        body='Body',
        author='Desk',
        sources=[
            SourceRef('Secondary', kind='secondary'),
            SourceRef('Primary', kind='primary'),
        ],
        created_at=T1,
    )
    assert [s['kind'] for s in article.sources] == ['primary', 'secondary']


def test_feed_is_newest_first():
    v = Voxara()
    a = v.publish(title='A', body='One', author='Desk', created_at=T1)
    b = v.publish(title='B', body='Two', author='Desk', created_at=T2)
    assert [x.article_id for x in v.feed()] == [b.article_id, a.article_id]


def test_export_hash_is_stable():
    v = Voxara()
    v.publish(title='A', body='Body', author='Desk', created_at=T1)
    assert v.export()['archive_hash'] == v.export()['archive_hash']


def test_invalid_terminal_status_requires_editorial_action():
    v = Voxara()
    with pytest.raises(VoxaraError):
        v.publish(title='A', body='Body', author='Desk', status='retracted')
