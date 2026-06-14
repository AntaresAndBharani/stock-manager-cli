from tradingtools_stock.core import config_store


class _FakeCursor:
    """Minimal cursor that emulates the app_config key/value SQL."""

    def __init__(self, store):
        self.store = store
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = sql.strip().upper()
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("SELECT"):
            value = self.store.get(params[0])
            self._result = (value,) if value is not None else None
        elif s.startswith("INSERT"):
            key, value = params
            self.store[key] = value

    def fetchone(self):
        return self._result


class _FakeConn:
    def __init__(self):
        self.store = {}

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass


def test_lookback_defaults_when_unset():
    conn = _FakeConn()
    assert (
        config_store.get_sma_1000_touch_lookback(conn)
        == config_store.DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS
    )


def test_lookback_persists_and_reads_back():
    conn = _FakeConn()
    config_store.set_sma_1000_touch_lookback(conn, 25)
    assert config_store.get_sma_1000_touch_lookback(conn) == 25


def test_lookback_invalid_stored_value_falls_back_to_default():
    conn = _FakeConn()
    config_store.set_config(
        conn, config_store.KEY_SMA_1000_TOUCH_LOOKBACK, "not-a-number"
    )
    assert (
        config_store.get_sma_1000_touch_lookback(conn)
        == config_store.DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS
    )


def test_lookback_non_positive_falls_back_to_default():
    conn = _FakeConn()
    config_store.set_sma_1000_touch_lookback(conn, 0)
    assert (
        config_store.get_sma_1000_touch_lookback(conn)
        == config_store.DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS
    )
