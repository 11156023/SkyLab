import importlib


def test_import_app_main_without_circular_import() -> None:
    module = importlib.import_module("app.main")

    assert module.app is not None
