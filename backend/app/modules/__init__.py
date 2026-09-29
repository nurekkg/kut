import importlib
import importlib.util
import pkgutil


def import_all_models() -> None:
    for module in pkgutil.iter_modules(__path__):
        if not module.ispkg:
            continue
        models_module = f"{__name__}.{module.name}.models"
        if importlib.util.find_spec(models_module):
            importlib.import_module(models_module)
