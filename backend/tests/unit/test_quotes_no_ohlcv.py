"""Guard: the streamer keeps intraday prices in Redis only. No module under
app.quotes may import or reference the Ohlcv model (that would risk leaking
intraday ticks into the daily hypertable)."""

import importlib
import inspect
import pkgutil

import app.quotes


def _quotes_modules():
    for info in pkgutil.iter_modules(app.quotes.__path__):
        yield importlib.import_module(f"app.quotes.{info.name}")


def test_no_quotes_module_references_ohlcv():
    checked = []
    for module in _quotes_modules():
        checked.append(module.__name__)
        assert not hasattr(module, "Ohlcv"), f"{module.__name__} imported the Ohlcv model"
        assert "Ohlcv" not in inspect.getsource(module), (
            f"{module.__name__} references the Ohlcv model"
        )
    # sanity: we actually inspected the real package
    assert "app.quotes.poller" in checked
    assert "app.quotes.streamer" in checked
