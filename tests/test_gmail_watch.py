from pharmacy_agent.gmail_watch import TOPIC_NAME, get_service, start_watch, stop_watch


def test_start_watch_registers_a_real_push_subscription():
    service = get_service()
    try:
        result = start_watch(service=service)
        assert result["historyId"]
        assert result["expiration"]
    finally:
        stop_watch(service=service)


def test_start_watch_uses_the_provisioned_gmail_notifications_topic():
    assert TOPIC_NAME.endswith("/topics/gmail-notifications")
