MAX_RETRIES = 7


def backoff(delay=2.5):
    """Seconds to wait before the next retry."""
    return delay * 2
