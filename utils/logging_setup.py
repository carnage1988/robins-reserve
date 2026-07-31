import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
def configure_logging(base_dir: Path) -> None:
    log_dir=base_dir/'logs'; log_dir.mkdir(parents=True,exist_ok=True)
    fmt='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    console=logging.StreamHandler(); console.setFormatter(logging.Formatter(fmt))
    file=RotatingFileHandler(log_dir/'robins_reserve.log',mode='a',maxBytes=5*1024*1024,backupCount=5,encoding='utf-8')
    file.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=logging.INFO,handlers=[console,file])
