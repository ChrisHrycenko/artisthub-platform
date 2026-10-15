"""Real consumer transactions on an exclusively Alembic-built database."""

import json
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from app.models import (AnalyticsState, Artist, Fan, Follow, Notification,
                        ProcessedEvent)
from consumers import analytics_consumer as analytics
from consumers import notification_consumer as notifications


@pytest.fixture
def release_event(lab) -> dict:
    """Seed domain relationships only after a complete Alembic upgrade."""
    lab.migrate()
    artist = Artist(email='migration@example.test', password_hash='test-only',
                    display_name='Artist')
    fan = Fan(email='fan@example.test', username='fan', password_hash='test-only')
    lab.db.session.add_all([artist, fan])
    lab.db.session.flush()
    lab.db.session.add(Follow(artist_id=artist.id, fan_id=fan.id))
    lab.db.session.commit()
    return {'event_id': 'release-event', 'event_type': 'artist.release.created',
            'event_version': '1', 'payload': {'artist_id': artist.id,
                                              'release_id': 1, 'title': 'Release'}}


def counts(lab, artist_id: int) -> tuple:
    """Read projections and markers from a new session after each delivery."""
    lab.db.session.remove()
    state = lab.db.session.get(AnalyticsState, artist_id)
    return (state.release_count if state else 0,
            lab.db.session.scalar(sa.select(sa.func.count()).select_from(Notification)),
            set(lab.db.session.scalars(sa.select(ProcessedEvent.consumer_group))))


def message(event: dict) -> MagicMock:
    """Provide a Kafka-shaped message without contacting a broker."""
    result = MagicMock()
    result.topic.return_value = 'artisthub.releases'
    result.partition.return_value = 0
    result.offset.return_value = 17
    result.value.return_value = json.dumps(event).encode()
    return result


def test_independent_groups_restart_redelivery(lab, release_event) -> None:
    """Both actual handlers commit their own markers once across restarts."""
    producer = MagicMock()
    event = release_event
    for _ in range(2):
        lab.db.session.remove()
        assert analytics.process_message(lab.app, message(event), producer)
        lab.db.session.remove()
        assert notifications.process_message(lab.app, message(event), producer)
        assert counts(lab, event['payload']['artist_id']) == (
            1, 1, {analytics.CONSUMER_GROUP, notifications.CONSUMER_GROUP})
    producer.produce.assert_not_called()


@pytest.mark.parametrize('consumer', ['analytics', 'notification'])
def test_rollback_then_redelivery(lab, release_event, consumer) -> None:
    """Projection changes and markers roll back together before successful retry."""
    event = release_event
    if consumer == 'analytics':
        analytics.apply_analytics_update(lab.db.session, event['event_type'],
                                         event['payload'], event['event_id'],
                                         'artisthub.releases', 0, 17)
    else:
        notifications.process_release_created(lab.db.session, event,
                                               'artisthub.releases', 0, 17)
    lab.db.session.flush()
    lab.db.session.rollback()
    assert counts(lab, event['payload']['artist_id']) == (0, 0, set())
    handler = (analytics.process_message if consumer == 'analytics'
               else notifications.process_message)
    producer = MagicMock()
    assert handler(lab.app, message(event), producer)
    lab.db.session.remove()
    assert handler(lab.app, message(event), producer)
    expected = ((1, 0, {analytics.CONSUMER_GROUP}) if consumer == 'analytics'
                else (0, 1, {notifications.CONSUMER_GROUP}))
    assert counts(lab, event['payload']['artist_id']) == expected
    producer.produce.assert_not_called()


@pytest.mark.parametrize('consumer', ['analytics', 'notification'])
def test_existing_db_failure_deadletter_contract(
        lab, release_event, consumer, monkeypatch) -> None:
    """Actual handlers roll back and retain existing retry/offset decisions."""
    module = analytics if consumer == 'analytics' else notifications
    monkeypatch.setattr(module, 'MAX_RETRIES', 2)
    monkeypatch.setattr(module, 'RETRY_BACKOFF', 0)
    producer = MagicMock()

    def fail_commit(session) -> None:
        """Fail the transaction after actual projection processing."""
        raise RuntimeError('injected commit failure')

    session_type = type(lab.db.session())
    sa.event.listen(session_type, 'before_commit', fail_commit)
    try:
        # Existing behavior permits offset commitment after dead-lettering.
        assert module.process_message(lab.app, message(release_event), producer)
    finally:
        sa.event.remove(session_type, 'before_commit', fail_commit)
    assert counts(lab, release_event['payload']['artist_id']) == (0, 0, set())
    producer.produce.assert_called_once()
